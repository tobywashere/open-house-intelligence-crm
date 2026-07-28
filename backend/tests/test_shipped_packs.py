"""Every shipped vertical pack must load cleanly and be genuinely usable.

A pack that silently falls back to real-estate defaults is a broken pack — it
would look fine in the UI while being the wrong vertical. A knowledge doc that
retrieves nothing demonstrates nothing. Both are asserted here so a pack added
later can't ship half-working.
"""
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHIPPED = sorted((REPO / "verticals").glob("*/pack.json"))
EXAMPLE_PACKS = [p for p in SHIPPED if p.parent.name != "real-estate"]


def test_shipped_packs_are_discovered():
    """Guards the globs below: an empty glob makes every parametrized test
    pass vacuously."""
    assert SHIPPED, "no verticals/*/pack.json found"
    assert EXAMPLE_PACKS, "no non-real-estate example packs found"


@pytest.mark.parametrize("pack_path", SHIPPED, ids=lambda p: p.parent.name)
def test_pack_loads_as_itself(pack_path, monkeypatch):
    from app import vertical

    monkeypatch.delenv("VERTICALS_DIR", raising=False)
    monkeypatch.setenv("VERTICAL", pack_path.parent.name)
    vertical.clear_cache()
    pack = vertical.load_pack()
    assert pack["name"] == pack_path.parent.name
    assert len(pack["stages"]) >= 2
    assert pack["research"]["regions"]
    vertical.clear_cache()


@pytest.mark.parametrize("pack_path", SHIPPED, ids=lambda p: p.parent.name)
def test_pack_supplies_the_full_key_surface(pack_path):
    """A missing copy key silently renders real-estate wording — "Book a tour"
    in a recruiting install is exactly what these packs exist to disprove."""
    from app.vertical import DEFAULT_PACK

    pack = json.loads(pack_path.read_text())
    assert not set(DEFAULT_PACK) - set(pack), "missing top-level keys"
    assert not set(DEFAULT_PACK["copy"]) - set(pack["copy"]), "missing copy keys"


@pytest.mark.parametrize("pack_path", EXAMPLE_PACKS, ids=lambda p: p.parent.name)
def test_example_packs_are_actually_reskinned(pack_path):
    from app.vertical import DEFAULT_PACK

    pack = json.loads(pack_path.read_text())
    assert pack["copy"]["booking.cta"] != DEFAULT_PACK["copy"]["booking.cta"]
    assert pack["research"]["regions"] != DEFAULT_PACK["research"]["regions"]
    assert pack["brand"] != DEFAULT_PACK["brand"]


@pytest.mark.parametrize("pack_path", EXAMPLE_PACKS, ids=lambda p: p.parent.name)
def test_example_knowledge_docs_are_retrievable(pack_path, monkeypatch):
    """The doc must chunk into a real corpus — a sample that indexes nothing
    can't demonstrate that a vertical's knowledge base works."""
    from app.knowledge.index import get_corpus

    kdir = pack_path.parent / "knowledge"
    assert list(kdir.glob("*.md")), f"{pack_path.parent.name} ships no knowledge doc"
    monkeypatch.setenv("KNOWLEDGE_DIR", str(kdir))
    corpus = get_corpus(kdir)
    assert len(corpus.chunks) >= 8


@pytest.mark.parametrize("pack_path", EXAMPLE_PACKS, ids=lambda p: p.parent.name)
def test_example_knowledge_docs_are_labelled_as_samples(pack_path):
    """These are AI-written illustrations, not researched guidance. The header
    must say so in the file itself, not only in the docs."""
    for doc in (pack_path.parent / "knowledge").glob("*.md"):
        assert "Illustrative sample" in doc.read_text()
