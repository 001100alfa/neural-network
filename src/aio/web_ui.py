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

# (filename -> content-type) for the assets the dashboard serves.
_CONTENT_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
}


def _read(name: str) -> str:
    return (_STATIC / name).read_text(encoding="utf-8")


def static_asset(path: str) -> tuple[bytes, str] | None:
    """Return ``(body, content_type)`` for a dashboard asset, or ``None``.

    ``path`` is the request path (e.g. ``"/app.js"`` or ``"/"``).
    """
    name = path.lstrip("/") or "index.html"
    if name in _CONTENT_TYPES:
        try:
            return _read(name).encode("utf-8"), _CONTENT_TYPES[name]
        except FileNotFoundError:
            return None
    return None


# Backwards-compatible export: the served HTML document.
INDEX_HTML = _read("index.html")
