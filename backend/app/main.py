from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .db import init_db
from .routers import calendar, chat, leads, misc

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


@app.on_event("startup")
def startup():
    init_db()


# GB10 single-port hosting: if the dashboard has been built (npm run build),
# serve it from this same server — http://<gb10>:8000 is the whole product.
# API routes above always win; everything else falls back to the SPA.
DIST = Path(__file__).resolve().parent.parent.parent / "dashboard" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        file = DIST / path
        if path and file.is_file():
            return FileResponse(file)
        return FileResponse(DIST / "index.html")
