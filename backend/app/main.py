from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .db import init_db
from .integrations import router as integrations
from .routers import calendar, chat, leads, misc, reports, scan

app = FastAPI(title="Open House Intelligence")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # hackathon mode; tighten if this ever leaves the demo
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(leads.router, prefix="/api")
app.include_router(calendar.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(misc.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(scan.router, prefix="/api")
app.include_router(integrations.router, prefix="/api")


@app.on_event("startup")
def startup():
    init_db()
    from .integrations import composio_client as cc
    if cc.mode() == "live" and not cc.is_live():
        print("WARNING: INTEGRATIONS_MODE=live but COMPOSIO_API_KEY is not set — "
              "running with integrations OFF (simulated).")
    if cc.is_live():
        import asyncio
        from .integrations.poller import poll_loop
        asyncio.get_event_loop().create_task(poll_loop())


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
