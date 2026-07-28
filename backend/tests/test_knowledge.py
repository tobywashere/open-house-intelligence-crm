import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.knowledge import chunk_markdown, retrieve
from app.knowledge.index import REPO_ROOT, get_corpus

REAL_KNOWLEDGE_DIR = REPO_ROOT / "docs" / "knowledge"


# ── chunking ─────────────────────────────────────────────────────────────

def test_chunking_headings_become_chunks():
    md = (
        "# Report Title\n\n"
        "## Section One\n"
        "Body text for section one.\n\n"
        "### Subsection A\n"
        "Nested body text.\n"
    )
    chunks = chunk_markdown(md, doc="fixture.md")
    headings = [c.heading for c in chunks]
    assert "Section One" in headings
    assert "Subsection A" in headings
    one = next(c for c in chunks if c.heading == "Section One")
    assert one.breadcrumb == "Report Title > Section One"
    sub = next(c for c in chunks if c.heading == "Subsection A")
    assert sub.breadcrumb == "Report Title > Section One > Subsection A"
    assert all(c.doc == "fixture.md" for c in chunks)


def test_chunking_oversized_section_splits_on_paragraph_boundary():
    para = "Lorem ipsum dolor sit amet. " * 30  # ~870 chars per paragraph
    body = "\n\n".join([para] * 4)  # ~3500 chars, over the 1500 cap
    md = f"# Title\n\n## Big Section\n{body}\n"
    chunks = chunk_markdown(md, doc="fixture.md", max_chars=1500)
    big = [c for c in chunks if c.heading == "Big Section"]
    assert len(big) > 1
    for c in big:
        assert len(c.text) <= 1500
        # never split mid-paragraph: every piece is a clean concatenation of
        # whole paragraphs
        assert c.text.strip().endswith("amet.")


# ── retrieval ────────────────────────────────────────────────────────────

FIXTURE_DOCS = {
    "widgets.md": (
        "# Widget Manual\n\n"
        "## Widget Assembly\n"
        "To assemble the widget, attach the flange to the bracket using the "
        "supplied bolts. Torque each bolt to 12 newton-meters.\n\n"
        "## Widget Maintenance\n"
        "Lubricate the widget bearing every 500 hours of operation. Use only "
        "grade-2 synthetic lubricant on the bearing surfaces.\n"
    ),
    "gadgets.md": (
        "# Gadget Manual\n\n"
        "## Gadget Power Supply\n"
        "The gadget requires a 9-volt battery. Replace the battery when the "
        "indicator light turns red.\n"
    ),
}


def _write_fixture_dir(tmp_path: Path) -> Path:
    d = tmp_path / "knowledge"
    d.mkdir()
    for name, text in FIXTURE_DOCS.items():
        (d / name).write_text(text)
    return d


def test_retrieve_matching_query_ranks_relevant_chunk_first(tmp_path):
    d = _write_fixture_dir(tmp_path)
    hits = retrieve("how do I lubricate the widget bearing", k=3, directory=d)
    assert hits
    assert hits[0].heading == "Widget Maintenance"


def test_retrieve_unrelated_query_returns_no_hits(tmp_path):
    d = _write_fixture_dir(tmp_path)
    hits = retrieve("quantum entanglement in neutron stars", k=3, directory=d)
    assert hits == []


def test_retrieve_respects_k(tmp_path):
    d = _write_fixture_dir(tmp_path)
    hits = retrieve("widget", k=1, directory=d, min_score=0.0)
    assert len(hits) == 1


def test_retrieve_mtime_invalidation_without_restart(tmp_path):
    d = tmp_path / "knowledge"
    d.mkdir()
    f = d / "doc.md"
    f.write_text("# Title\n\n## Section\nOriginal placeholder content.\n")
    get_corpus(d)  # prime the cache
    hits = retrieve("giraffe zoology", k=3, directory=d, min_score=0.0)
    assert hits == []

    time.sleep(0.05)
    f.write_text("# Title\n\n## Giraffe Facts\nGiraffe zoology covers neck "
                 "vertebrae and browsing behavior in giraffes.\n")
    hits = retrieve("giraffe zoology", k=3, directory=d, min_score=0.0)
    assert hits
    assert hits[0].heading == "Giraffe Facts"


def test_retrieve_missing_dir_no_crash(tmp_path):
    hits = retrieve("anything", k=3, directory=tmp_path / "does-not-exist")
    assert hits == []


def test_retrieve_empty_dir_no_crash(tmp_path):
    d = tmp_path / "knowledge"
    d.mkdir()
    hits = retrieve("anything", k=3, directory=d)
    assert hits == []


def test_retrieve_empty_query_no_crash(tmp_path):
    d = _write_fixture_dir(tmp_path)
    assert retrieve("", k=3, directory=d) == []
    assert retrieve("   ", k=3, directory=d) == []


# ── real shipped report (integration) ───────────────────────────────────

def test_real_report_amazon_rsu_query_retrieves_the_right_section():
    hits = retrieve(
        "How does Amazon's RSU vesting schedule affect a buyer's liquidity?",
        k=3, directory=REAL_KNOWLEDGE_DIR,
    )
    assert hits, "expected the shipped report to be searchable"
    assert any("Vesting" in h.heading or "Equity" in h.heading for h in hits), \
        [h.heading for h in hits]


# ── chat integration ────────────────────────────────────────────────────

@pytest.fixture()
def knowledge_env(tmp_path, monkeypatch):
    d = _write_fixture_dir(tmp_path)
    monkeypatch.setenv("KNOWLEDGE_DIR", str(d))
    monkeypatch.setenv("KNOWLEDGE_MIN_SCORE", "0.0")
    from app.knowledge import index as knowledge_index
    knowledge_index._cache.clear()
    yield d
    knowledge_index._cache.clear()


def test_chat_augments_message_with_knowledge_when_hits(client, knowledge_env):
    with patch("app.routers.chat.get_driver") as mock_get_driver:
        driver = AsyncMock()
        driver.chat.return_value = "[mock reply]"
        mock_get_driver.return_value = driver
        res = client.post("/api/chat", json={"message": "how do I lubricate the widget bearing?",
                                               "session_id": "t1"})
    assert res.status_code == 200
    sent_message = driver.chat.await_args.args[0]
    assert "Widget Maintenance" in sent_message
    assert "reference material" in sent_message.lower() or "knowledge base" in sent_message.lower()
    assert "how do I lubricate the widget bearing?" in sent_message


def test_chat_sends_message_unchanged_when_no_hits(client, knowledge_env):
    with patch("app.routers.chat.get_driver") as mock_get_driver:
        driver = AsyncMock()
        driver.chat.return_value = "[mock reply]"
        mock_get_driver.return_value = driver
        res = client.post("/api/chat", json={"message": "quantum entanglement in neutron stars",
                                               "session_id": "t2"})
    assert res.status_code == 200
    sent_message = driver.chat.await_args.args[0]
    assert sent_message == "quantum entanglement in neutron stars"


def test_chat_survives_retrieval_failure(client, monkeypatch):
    monkeypatch.setattr("app.routers.chat.retrieve", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with patch("app.routers.chat.get_driver") as mock_get_driver:
        driver = AsyncMock()
        driver.chat.return_value = "[mock reply]"
        mock_get_driver.return_value = driver
        res = client.post("/api/chat", json={"message": "hello", "session_id": "t3"})
    assert res.status_code == 200
    assert driver.chat.await_args.args[0] == "hello"


# ── endpoint ─────────────────────────────────────────────────────────────

def test_knowledge_search_endpoint_happy_path(client, knowledge_env):
    res = client.get("/api/knowledge/search", params={"q": "widget bearing lubricant"})
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    assert body
    first = body[0]
    assert set(first.keys()) >= {"doc", "heading", "breadcrumb", "score", "text"}


def test_knowledge_search_endpoint_rejects_empty_q(client, knowledge_env):
    res = client.get("/api/knowledge/search", params={"q": ""})
    assert res.status_code == 422


def test_knowledge_search_endpoint_bounds_k(client, knowledge_env):
    res = client.get("/api/knowledge/search", params={"q": "widget", "k": 0})
    assert res.status_code == 422
    res = client.get("/api/knowledge/search", params={"q": "widget", "k": 11})
    assert res.status_code == 422
    res = client.get("/api/knowledge/search", params={"q": "widget", "k": 10})
    assert res.status_code == 200
