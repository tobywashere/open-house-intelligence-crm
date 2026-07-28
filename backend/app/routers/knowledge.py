from fastapi import APIRouter, Query

from ..knowledge import hits_to_dicts, retrieve

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/search")
def search(q: str = Query(..., min_length=1), k: int = Query(3, ge=1, le=10)):
    """Debug/dashboard endpoint over the local BM25 knowledge index. Read-only —
    deliberately NOT audited (see docs/CONTRACT.md §3: only two reads audit,
    and this isn't one of them)."""
    hits = retrieve(q, k=k)
    return hits_to_dicts(hits)
