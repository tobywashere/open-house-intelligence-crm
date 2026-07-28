"""Serves the active vertical pack to the dashboard. Read-only, no audit row
(CONTRACT §3: exactly two reads audit, and this is not one of them)."""
from fastapi import APIRouter

from ..vertical import load_pack

router = APIRouter(tags=["vertical"])


@router.get("/vertical")
def get_vertical() -> dict:
    return load_pack()
