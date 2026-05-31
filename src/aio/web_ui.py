"""Static assets for the `aio --web` dashboard.

The dashboard is split into real, separately-servable files under
``aio/static/`` — ``index.html`` (markup), ``app.css`` (styles) and
``app.js`` (behaviour) — instead of one giant inline string. Keeping them
as first-class files means they get syntax highlighting, can be linted /
``node --check``-ed, and are validated by ``tests/test_frontend.py``
(route contract, DOM-id integrity, JS syntax).

``web.py`` serves these via :func:`static_asset`. ``INDEX_HTML`` stays
exported for backwards compatibility (it is the contents of index.html).
"""

from __future__ import annotations

from importlib.resources import files

_STATIC = files(__package__) / "static"

# Content types for the extensions the dashboard serves (any file with one of
# these suffixes that lives directly in aio/static/ may be served).
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}


def _read(name: str) -> str:
    return (_STATIC / name).read_text(encoding="utf-8")


def static_asset(path: str) -> tuple[bytes, str] | None:
    """Return ``(body, content_type)`` for a dashboard asset, or ``None``.

    ``path`` is the request path (e.g. ``"/app.js"`` or ``"/"``). Only plain
    filenames with a known extension are served — never a nested path, ``..``
    traversal, or anything but the whitelisted asset extensions.
    """
    name = path.lstrip("/") or "index.html"
    if "/" in name or "\\" in name or name.startswith("."):
        return None
    import os.path

    ext = os.path.splitext(name)[1]
    content_type = _CONTENT_TYPES.get(ext)
    if content_type is None:
        return None
    try:
        return _read(name).encode("utf-8"), content_type
    except FileNotFoundError:
        return None


# Backwards-compatible export: the served HTML document.
INDEX_HTML = _read("index.html")
