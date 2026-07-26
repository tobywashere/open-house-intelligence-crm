from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
