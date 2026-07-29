"""Local knowledge corpus: retrieval (read) + doc management (write).

The management endpoints are a filesystem write surface reachable over HTTP,
so filenames are reduced to a slug of their basename and every resolved path
is re-checked against knowledge_dir() before any write or unlink. The BM25
index self-invalidates on mtime (see knowledge/index.py), so neither upload
nor delete needs to bust a cache — backend/tests/test_knowledge_docs.py
asserts that rather than trusting it.
"""
import base64
import binascii
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..db import audit, get_conn
from ..knowledge import hits_to_dicts, retrieve
from ..knowledge.chunking import chunk_markdown
from ..knowledge.index import get_corpus, knowledge_dir

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

MAX_DOC_BYTES = 2_000_000
# base64 inflates ~4/3; refuse obviously-oversize payloads before decoding
# them into memory rather than after.
MAX_ENCODED_CHARS = (MAX_DOC_BYTES * 4) // 3 + 1024

_SLUG_STRIP = re.compile(r"[^a-z0-9._-]+")


class DocIn(BaseModel):
    filename: str
    data: str  # base64-encoded UTF-8 markdown


def _safe_name(filename: str) -> str:
    """Client filename → a bare `*.md` slug that cannot leave the corpus dir.

    Only the basename survives, so "../../etc/passwd.md" becomes "passwd.md"
    and "a/b.md" becomes "b.md". Percent-encoded separators are not decoded
    (they are not path separators to us); they simply lose their `%` to the
    slug filter, which is why "..%2f..%2fx.md" ends up leading with a dot and
    is refused below rather than resolving anywhere.
    """
    base = Path(filename.strip()).name.lower()
    if Path(base).suffix != ".md":
        raise HTTPException(422, "only .md knowledge documents are accepted")
    slug = _SLUG_STRIP.sub("-", base)
    stem = slug[:-3] if slug.endswith(".md") else ""
    # a dot-leading stem would create a hidden file the listing never shows;
    # an empty stem means the client sent nothing but punctuation.
    if not stem or stem.startswith(".") or set(stem) <= {".", "-"}:
        raise HTTPException(422, "filename has no usable name")
    return f"{stem}.md"


def _resolved(name: str) -> Path:
    """Resolve `name` inside the knowledge dir, refusing anything that lands
    outside it even after symlinks are followed."""
    directory = knowledge_dir().resolve()
    path = (directory / name).resolve()
    if path.parent != directory:
        raise HTTPException(422, "invalid document name")
    return path


@router.get("/search")
def search(q: str = Query(..., min_length=1), k: int = Query(3, ge=1, le=10)):
    """Debug/dashboard endpoint over the local BM25 knowledge index. Read-only —
    deliberately NOT audited (see docs/CONTRACT.md §3: only two reads audit,
    and this isn't one of them)."""
    hits = retrieve(q, k=k)
    return hits_to_dicts(hits)


@router.get("/docs")
def list_docs():
    """Indexed documents with their chunk and byte counts. Chunk counts come
    from the live corpus, so what is listed is what retrieval can actually
    reach — a file present but unindexed honestly reports 0."""
    directory = knowledge_dir()
    if not directory.is_dir():
        return []
    per_doc: dict[str, int] = {}
    for chunk in get_corpus(directory).chunks:
        per_doc[chunk.doc] = per_doc.get(chunk.doc, 0) + 1
    out = []
    for f in sorted(directory.glob("*.md")):
        try:
            size = f.stat().st_size
        except OSError:
            continue
        out.append({"name": f.name, "chunks": per_doc.get(f.name, 0), "bytes": size})
    return out


@router.post("/docs")
def upload_doc(body: DocIn):
    if len(body.data) > MAX_ENCODED_CHARS:
        raise HTTPException(413, "document too large — max 2 MB")
    name = _safe_name(body.filename)
    try:
        raw = base64.b64decode(body.data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "invalid base64 payload")
    if len(raw) > MAX_DOC_BYTES:
        raise HTTPException(413, "document too large — max 2 MB")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(422, "document must be UTF-8 text")
    if "\x00" in text:
        raise HTTPException(422, "document must be UTF-8 text")

    directory = knowledge_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = _resolved(name)
    # The write stays OUTSIDE get_conn(): that holds an exclusive SQLite write
    # lock, and file I/O has no business blocking other writers behind it.
    path.write_text(text, encoding="utf-8")
    chunks = len(chunk_markdown(text, doc=name))

    with get_conn() as conn:
        audit(conn, "user", "upload_knowledge_doc",
              {"filename": body.filename}, {"name": name, "bytes": len(raw), "chunks": chunks})
    return {"name": name, "chunks": chunks, "bytes": len(raw)}


@router.delete("/docs/{name}")
def delete_doc(name: str):
    safe = _safe_name(name)
    path = _resolved(safe)
    if not path.is_file():
        raise HTTPException(404, f"{safe} not found")
    path.unlink()
    with get_conn() as conn:
        audit(conn, "user", "delete_knowledge_doc", {"name": safe}, {"deleted": True})
    return {"name": safe, "deleted": True}
