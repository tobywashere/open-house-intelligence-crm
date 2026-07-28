"""Pack loading must degrade to real-estate defaults, never crash."""
import json
import pytest
from app import vertical


def test_missing_pack_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("VERTICALS_DIR", str(tmp_path))
    monkeypatch.setenv("VERTICAL", "does-not-exist")
    vertical.clear_cache()
    pack = vertical.load_pack()
    assert pack["stages"][0]["key"] == "new"
    assert pack["labels"]["budget"] == "Budget"


def test_partial_pack_merges_over_defaults(tmp_path, monkeypatch):
    d = tmp_path / "partial"
    d.mkdir()
    (d / "pack.json").write_text(json.dumps({"labels": {"budget": "Deal size"}}))
    monkeypatch.setenv("VERTICALS_DIR", str(tmp_path))
    monkeypatch.setenv("VERTICAL", "partial")
    vertical.clear_cache()
    pack = vertical.load_pack()
    assert pack["labels"]["budget"] == "Deal size"      # overridden
    assert pack["labels"]["area"] == "Area"             # defaulted
    assert len(pack["stages"]) == 6                     # defaulted


def test_malformed_json_falls_back_and_does_not_raise(tmp_path, monkeypatch):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "pack.json").write_text("{ not json")
    monkeypatch.setenv("VERTICALS_DIR", str(tmp_path))
    monkeypatch.setenv("VERTICAL", "broken")
    vertical.clear_cache()
    assert vertical.load_pack()["labels"]["budget"] == "Budget"


def test_unknown_stage_rule_is_dropped_not_fatal(tmp_path, monkeypatch):
    d = tmp_path / "weird"
    d.mkdir()
    (d / "pack.json").write_text(json.dumps({"stages": [
        {"key": "new", "label": "New", "rule": {"type": "all"}},
        {"key": "bogus", "label": "Bogus", "rule": {"type": "telepathy"}},
    ]}))
    monkeypatch.setenv("VERTICALS_DIR", str(tmp_path))
    monkeypatch.setenv("VERTICAL", "weird")
    vertical.clear_cache()
    stages = vertical.load_pack()["stages"]
    assert [s["key"] for s in stages] == ["new"]


def test_shipped_real_estate_pack_matches_defaults():
    """The extracted pack must be byte-equivalent in effect to the built-in
    defaults — that is what makes this refactor a provable no-op."""
    vertical.clear_cache()
    import os
    os.environ.pop("VERTICALS_DIR", None)
    os.environ["VERTICAL"] = "real-estate"
    vertical.clear_cache()
    assert vertical.load_pack() == vertical.DEFAULT_PACK
