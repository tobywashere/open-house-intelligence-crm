"""Knowledge doc management endpoints (upload / list / delete).

This is a filesystem WRITE surface reachable over HTTP, so the adversarial
cases below carry more weight than the happy path: a filename must never
escape the knowledge dir, non-markdown and non-UTF-8 payloads must be
refused, and both mutations must land in the audit log.

KNOWLEDGE_DIR is redirected at tmp_path for every test here — nothing may
write into the real docs/knowledge/.
"""
import base64
from pathlib import Path

import pytest


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


@pytest.fixture()
def tmp_knowledge(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "knowledge"
    d.mkdir()
    monkeypatch.setenv("KNOWLEDGE_DIR", str(d))
    # the shipped corpus tunes this well above 0; these fixtures are tiny
    monkeypatch.setenv("KNOWLEDGE_MIN_SCORE", "0.0")
    return d


def test_upload_then_listed_and_retrievable(client, tmp_knowledge):
    body = "# Widget Pricing\n\nWidgets are priced by throughput tier.\n"
    r = client.post("/api/knowledge/docs",
                    json={"filename": "widgets.md", "data": _b64(body)})
    assert r.status_code == 200
    assert "widgets.md" in [d["name"] for d in client.get("/api/knowledge/docs").json()]
    hits = client.get("/api/knowledge/search?q=widget throughput pricing tier").json()
    assert any("Widget Pricing" in h["heading"] for h in hits)


def test_listing_reports_chunk_and_byte_counts(client, tmp_knowledge):
    body = "# Alpha\n\nFirst body.\n\n## Beta\n\nSecond body.\n"
    client.post("/api/knowledge/docs", json={"filename": "counts.md", "data": _b64(body)})
    row = next(d for d in client.get("/api/knowledge/docs").json() if d["name"] == "counts.md")
    assert row["bytes"] == len(body.encode())
    assert row["chunks"] >= 2


def test_traversal_filename_cannot_escape(client, tmp_knowledge):
    for evil in ("../../etc/passwd.md", "..%2f..%2fx.md", "/abs/path.md", "a/b.md"):
        r = client.post("/api/knowledge/docs", json={"filename": evil, "data": _b64("# x\n")})
        assert r.status_code in (200, 422)
        if r.status_code == 200:
            written = r.json()["name"]
            assert "/" not in written and ".." not in written
    assert not (tmp_knowledge.parent / "passwd.md").exists()
    assert not (tmp_knowledge.parent / "x.md").exists()
    # every file that did get written stayed inside the knowledge dir
    for f in tmp_knowledge.glob("*.md"):
        assert f.resolve().parent == tmp_knowledge.resolve()


def test_non_markdown_rejected(client, tmp_knowledge):
    assert client.post("/api/knowledge/docs",
                       json={"filename": "x.exe", "data": _b64("MZ\x00")}).status_code == 422
    assert client.post("/api/knowledge/docs",
                       json={"filename": "x.md", "data": base64.b64encode(b"\x00\x01\x02").decode()}
                       ).status_code == 422


def test_non_utf8_payload_rejected(client, tmp_knowledge):
    assert client.post("/api/knowledge/docs",
                       json={"filename": "x.md", "data": base64.b64encode(b"\xff\xfe\xfa").decode()}
                       ).status_code == 422


def test_malformed_base64_rejected(client, tmp_knowledge):
    r = client.post("/api/knowledge/docs", json={"filename": "x.md", "data": "not!base64!"})
    assert r.status_code in (400, 422)


def test_oversize_rejected(client, tmp_knowledge):
    assert client.post("/api/knowledge/docs",
                       json={"filename": "big.md", "data": _b64("#\n" + "x" * 3_000_000)}
                       ).status_code == 413


def test_dotfile_and_empty_slug_rejected(client, tmp_knowledge):
    for bad in (".md", "...", "   ", ".hidden.md"):
        r = client.post("/api/knowledge/docs", json={"filename": bad, "data": _b64("# x\n")})
        assert r.status_code == 422, bad


def test_delete_removes_and_deindexes(client, tmp_knowledge):
    client.post("/api/knowledge/docs", json={"filename": "temp.md",
                "data": _b64("# Zebra Facts\n\nZebras are striped.\n")})
    assert client.delete("/api/knowledge/docs/temp.md").status_code == 200
    assert client.get("/api/knowledge/search?q=zebra striped").json() == []


def test_delete_missing_is_404(client, tmp_knowledge):
    assert client.delete("/api/knowledge/docs/nope.md").status_code == 404


def test_delete_cannot_escape_the_knowledge_dir(client, tmp_knowledge):
    """Over HTTP the `../` is normalized out of the path before routing, so the
    handler is never even reached (405/404). What matters either way is that
    the file outside the corpus survives."""
    outside = tmp_knowledge.parent / "secret.md"
    outside.write_text("# secret\n")
    r = client.delete("/api/knowledge/docs/../secret.md")
    assert r.status_code in (404, 405, 422)
    assert outside.exists(), "delete escaped the knowledge directory"


def test_name_guard_neutralizes_traversal_directly(tmp_knowledge):
    """The URL-normalization above means the HTTP test cannot reach the guard,
    so exercise the guard itself — this is the defense that actually holds."""
    from app.routers.knowledge import _resolved, _safe_name

    assert _safe_name("../secret.md") == "secret.md"
    assert _safe_name("../../etc/passwd.md") == "passwd.md"
    assert _resolved(_safe_name("../secret.md")).parent == tmp_knowledge.resolve()


def test_resolved_refuses_a_name_that_climbs_out(tmp_knowledge):
    from fastapi import HTTPException

    from app.routers.knowledge import _resolved

    with pytest.raises(HTTPException):
        _resolved("../escape.md")


def test_reupload_replaces_and_reindexes(client, tmp_knowledge):
    """The index self-invalidates on mtime — asserted, not assumed."""
    client.post("/api/knowledge/docs", json={"filename": "swap.md",
                "data": _b64("# Original\n\nPelicans nest on cliffs.\n")})
    assert client.get("/api/knowledge/search?q=pelicans nest cliffs").json()
    client.post("/api/knowledge/docs", json={"filename": "swap.md",
                "data": _b64("# Replaced\n\nOtters float in kelp.\n")})
    assert client.get("/api/knowledge/search?q=pelicans nest cliffs").json() == []
    assert client.get("/api/knowledge/search?q=otters float kelp").json()


def test_upload_and_delete_are_audited(client, tmp_knowledge):
    client.post("/api/knowledge/docs", json={"filename": "a.md", "data": _b64("# A\n\ntext\n")})
    client.delete("/api/knowledge/docs/a.md")
    tools = [a["tool"] for a in client.get("/api/audit?limit=50").json()]
    assert "upload_knowledge_doc" in tools and "delete_knowledge_doc" in tools
