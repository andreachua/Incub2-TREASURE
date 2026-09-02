"""GET /app — the original exported design bundle, with the bridge injected.

The HTML on disk is never modified: the script tag goes into the *response*.
This route and static/bridge.js are the pre-Streamlit path, kept as-is; the
operator front end in use is frontend/streamlit_app/.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.api.deps import log
from app.api.paths import FRONTEND_DIR, find_frontend_html

router = APIRouter()


# --------------------------------------------------------------------------- #
# Serving the frontend.
#
# The exported HTML bundle is read-only: we inject the bridge <script> into the
# *response*, never into the file, so the artefact on disk stays byte-identical.
# --------------------------------------------------------------------------- #

_BRIDGE_TAG = '<script src="/static/bridge.js"></script>'
_page_cache: tuple[float, str] | None = None


@router.get("/app", response_class=HTMLResponse)
def serve_app() -> HTMLResponse:
    global _page_cache
    source = find_frontend_html()
    if source is None:
        raise HTTPException(404, f"no frontend bundle under {FRONTEND_DIR}")
    mtime = source.stat().st_mtime
    if _page_cache is None or _page_cache[0] != mtime:
        html = source.read_text(encoding="utf-8")
        if "</body>" not in html:
            raise HTTPException(500, "frontend bundle has no </body> to inject into")
        _page_cache = (mtime, html.replace("</body>", _BRIDGE_TAG + "</body>", 1))
        log.info("serving %s (%d bytes) with the bridge injected",
                 source.name, len(_page_cache[1]))
    return HTMLResponse(_page_cache[1])
