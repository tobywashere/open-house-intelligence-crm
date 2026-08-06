import os
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .db import init_db
from .integrations import router as integrations
from .routers import (calendar, chat, knowledge, leads, misc, pending_changes,
                      reports, scan, settings as settings_router, vertical, voice)

app = FastAPI(title="Open House Intelligence")


async def api_token_guard(request: Request, call_next):
    token = os.environ.get("OHI_API_TOKEN", "")
    if (token and request.method != "OPTIONS"  # preflight carries no auth by design
            and request.url.path.startswith("/api")
            and request.url.path != "/api/health"
            and not secrets.compare_digest(request.headers.get("X-API-Token", ""), token)):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "missing or invalid X-API-Token"}, status_code=401)
    return await call_next(request)


# Starlette's add_middleware inserts at position 0 (innermost-registered-last
# wraps outermost), so registering the auth guard BEFORE CORSMiddleware here
# puts CORS outermost. That matters: with a token set, a cross-origin
# preflight (browsers never attach custom headers to OPTIONS) must be
# answered by CORSMiddleware itself — with CORS headers — before it would
# otherwise be rejected 401 by the guard with no CORS headers, which the
# browser would then block outright.
app.add_middleware(BaseHTTPMiddleware, dispatch=api_token_guard)

_default_cors_origins = ["http://localhost:5173", "http://localhost:8080"]
_cors_origins_env = os.environ.get("CORS_ORIGINS", "")
cors_origins = ([o.strip() for o in _cors_origins_env.split(",") if o.strip()]
                 or _default_cors_origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(leads.router, prefix="/api")
app.include_router(pending_changes.router, prefix="/api")
app.include_router(calendar.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(misc.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(scan.router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")
app.include_router(integrations.router, prefix="/api")
app.include_router(settings_router.router, prefix="/api")
app.include_router(vertical.router, prefix="/api")
app.include_router(voice.router, prefix="/api")


@app.on_event("startup")
def startup():
    init_db()
    # Recover committed approval hooks continuously. The worker has one
    # process-local instance and never holds SQLite across provider calls.
    from .integrations.hook_outbox import start_worker
    app.state.hook_outbox_thread = start_worker()
    host = os.environ.get("HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost") and not os.environ.get("OHI_API_TOKEN"):
        print("WARNING: serving on a non-localhost interface with no OHI_API_TOKEN — "
              "anyone on the network can read/write the CRM and use the agent.")
    from .integrations import composio_client as cc
    if cc.mode() == "live" and not cc.is_live():
        print("WARNING: INTEGRATIONS_MODE=live but COMPOSIO_API_KEY is not set — "
              "running with integrations OFF (simulated).")
    # INTEGRATIONS_POLLER must be explicitly opted in — with the CLI transport
    # the connected mailbox is a PERSONAL account. Polling sends real inbound
    # mail to the model and queues new-lead proposals for human review.
    if cc.is_live() and os.environ.get("INTEGRATIONS_POLLER", "off") == "on":
        import asyncio
        from .integrations.poller import poll_loop
        app.state.poller_task = asyncio.get_event_loop().create_task(poll_loop())


@app.on_event("shutdown")
def shutdown():
    from .integrations.hook_outbox import stop_worker

    stop_worker()


# GB10 single-port hosting: if the dashboard has been built (npm run build),
# serve it from this same server — http://<gb10>:8000 is the whole product.
# API routes above always win; everything else falls back to the SPA.
DIST = Path(__file__).resolve().parent.parent.parent / "dashboard" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):  # unknown API routes stay real 404s, not HTML
            raise HTTPException(404, f"unknown API route: /{path}")
        # resolve + confine: uvicorn percent-decodes, so "..%2f" would otherwise
        # escape dist and serve crm.db or /etc/passwd
        root = DIST.resolve()
        file = (root / path).resolve()
        if path and file.is_file() and file.is_relative_to(root):
            return FileResponse(file)
        # never cache the shell — hashed assets carry the versioning; a cached
        # index.html would pin browsers to a stale bundle after every rebuild
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})
