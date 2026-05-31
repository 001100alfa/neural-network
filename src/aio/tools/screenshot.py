"""Screenshot tool — capture visual proof of a running web app.

Lets the agent produce the same kind of evidence a human reviewer asks for:
"show me it works". It renders a URL in a headless browser and saves a PNG into
the working directory, so the agent can verify a web UI it built/changed and
hand back a screenshot.

The headless browser (Playwright) is an OPTIONAL extra — AIO keeps zero required
runtime dependencies. When it isn't installed the tool returns a clear,
actionable message instead of failing the run.
"""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext, ToolError, _relpath

INSTALL_HINT = (
    "Screenshots need the optional Playwright browser. Install it once with:\n"
    "  pip install playwright && python -m playwright install chromium\n"
    "(AIO itself has zero required dependencies; this is an opt-in extra.)"
)


class ScreenshotTool(Tool):
    name = "screenshot"
    description = (
        "Capture a PNG screenshot of a web page (e.g. a local dev server or the "
        "AIO dashboard) and save it in the working directory — visual proof that "
        "a web UI renders/works. Provide a URL and an output path. Optionally "
        "wait for a CSS selector or a number of milliseconds before capturing."
    )
    needs_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Page to capture, e.g. http://localhost:8765"},
            "path": {"type": "string", "description": "Output .png path (relative to the working dir)."},
            "wait_selector": {"type": "string", "description": "Wait for this CSS selector to appear (optional)."},
            "wait_ms": {"type": "integer", "description": "Extra wait in milliseconds before capture (optional)."},
            "full_page": {"type": "boolean", "description": "Capture the full scrollable page (default false)."},
            "width": {"type": "integer", "description": "Viewport width (default 1280)."},
            "height": {"type": "integer", "description": "Viewport height (default 800)."},
        },
        "required": ["url", "path"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        url = (args.get("url") or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ToolError("url must start with http:// or https://")
        out = ctx.safe_path(args["path"])
        if out.suffix.lower() != ".png":
            out = out.with_suffix(".png")

        try:
            from playwright.sync_api import sync_playwright
        except Exception:  # pragma: no cover - depends on optional dep
            raise ToolError(INSTALL_HINT)

        width = int(args.get("width", 1280))
        height = int(args.get("height", 800))
        out.parent.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        try:
            with sync_playwright() as p:  # pragma: no cover - needs a browser binary
                try:
                    browser = p.chromium.launch()
                except Exception as exc:
                    raise ToolError(
                        f"Could not launch a browser: {exc}\n"
                        "Run: python -m playwright install chromium")
                page = browser.new_page(viewport={"width": width, "height": height})
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: errors.append(str(e)))
                try:
                    page.goto(url, wait_until="networkidle", timeout=20000)
                    sel = (args.get("wait_selector") or "").strip()
                    if sel:
                        page.wait_for_selector(sel, timeout=10000)
                    if args.get("wait_ms"):
                        page.wait_for_timeout(int(args["wait_ms"]))
                    page.screenshot(path=str(out), full_page=bool(args.get("full_page", False)))
                finally:
                    browser.close()
        except ToolError:
            raise
        except Exception as exc:  # pragma: no cover - network/render failure
            raise ToolError(f"Screenshot failed for {url}: {exc}")

        if ctx.ui is not None and hasattr(ctx.ui, "info"):
            ctx.ui.info(f"screenshot saved: {_relpath(out, ctx)}")
        note = f" (console errors: {len(errors)})" if errors else " (no console errors)"
        return f"Saved screenshot of {url} -> {_relpath(out, ctx)}{note}"
