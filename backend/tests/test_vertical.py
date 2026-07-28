"""Pack loading must degrade to real-estate defaults, never crash."""
import json
from pathlib import Path

import pytest
from app import vertical

VERTICALS_ROOT = Path(__file__).resolve().parents[2] / "verticals"
SHIPPED_PACKS = sorted(VERTICALS_ROOT.glob("*/pack.json"))
assert SHIPPED_PACKS, (
    "no shipped packs found under verticals/*/pack.json — the parametrized "
    "test below silently passes on an empty pack list, which proves nothing"
)


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


def test_deeply_nested_json_falls_back_and_does_not_raise(tmp_path, monkeypatch):
    """json.loads raises RecursionError (not JSONDecodeError/OSError) on a
    pathologically deep pack.json — the loader's except clause must catch
    that too, or a malformed pack takes down GET /api/vertical."""
    d = tmp_path / "too-deep"
    d.mkdir()
    depth = 50_000
    nested = "[" * depth + "]" * depth
    (d / "pack.json").write_text(nested)
    monkeypatch.setenv("VERTICALS_DIR", str(tmp_path))
    monkeypatch.setenv("VERTICAL", "too-deep")
    vertical.clear_cache()
    assert vertical.load_pack()["labels"]["budget"] == "Budget"


def test_non_object_json_falls_back_and_does_not_raise(tmp_path, monkeypatch):
    """Valid JSON that isn't an object (e.g. a bare list) must also degrade
    to defaults — `raw.items()` would otherwise raise AttributeError."""
    d = tmp_path / "not-an-object"
    d.mkdir()
    (d / "pack.json").write_text(json.dumps(["not", "an", "object"]))
    monkeypatch.setenv("VERTICALS_DIR", str(tmp_path))
    monkeypatch.setenv("VERTICAL", "not-an-object")
    vertical.clear_cache()
    assert vertical.load_pack()["labels"]["budget"] == "Budget"


def test_shipped_real_estate_pack_matches_defaults():
    """The extracted pack must be byte-equivalent in effect to the built-in
    defaults — that is what makes this refactor a provable no-op."""
    vertical.clear_cache()
    import os
    os.environ.pop("VERTICALS_DIR", None)
    os.environ["VERTICAL"] = "real-estate"
    vertical.clear_cache()
    assert vertical.load_pack() == vertical.DEFAULT_PACK


def test_default_pack_stage_rules_cover_the_shipped_funnel():
    """The six real-estate stages must be expressible in the rule vocabulary —
    if a rule type is missing, funnel.ts can't reproduce today's behavior."""
    from app.vertical import DEFAULT_PACK, KNOWN_RULE_TYPES
    keys = [s["key"] for s in DEFAULT_PACK["stages"]]
    assert keys == ["new", "contacted", "qualified", "tours", "offers", "closed"]
    for s in DEFAULT_PACK["stages"]:
        assert s["rule"]["type"] in KNOWN_RULE_TYPES
    qualified = next(s for s in DEFAULT_PACK["stages"] if s["key"] == "qualified")
    assert qualified["rule"] == {
        "type": "status_at_least_or_score",
        "status": "meeting_booked",
        "score_status": "contacted",
        "min_score": 70,
    }


def test_qualified_rule_names_both_thresholds_explicitly():
    """The live funnel qualifies at meeting_booked OR at contacted-with-score.
    Both thresholds must be explicit — an implicit rank offset is how this rule
    got mis-encoded the first time."""
    from app.vertical import DEFAULT_PACK
    rule = next(s["rule"] for s in DEFAULT_PACK["stages"] if s["key"] == "qualified")
    assert rule["type"] == "status_at_least_or_score"
    assert rule["status"] == "meeting_booked"
    assert rule["score_status"] == "contacted"
    assert rule["min_score"] == 70


def test_vertical_endpoint_returns_resolved_pack(client):
    r = client.get("/api/vertical")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "real-estate"
    assert len(body["stages"]) == 6
    assert body["labels"]["budget"] == "Budget"


def test_vertical_endpoint_writes_no_audit_row(client):
    """It is a READ. CONTRACT §3 says exactly two reads audit; this isn't one."""
    before = len(client.get("/api/audit?limit=500").json())
    client.get("/api/vertical")
    assert len(client.get("/api/audit?limit=500").json()) == before


def test_every_copy_key_used_by_the_dashboard_exists_in_the_pack():
    """Guards against a `copy('x', ...)` call whose key nobody added to the pack —
    the fallback would silently mask it."""
    import re
    from pathlib import Path
    from app.vertical import DEFAULT_PACK
    src = Path(__file__).resolve().parents[2] / "dashboard" / "src"
    used = set()
    for f in src.rglob("*.ts*"):
        used |= set(re.findall(r"copy\(\s*'([a-z0-9_.]+)'", f.read_text()))
    missing = used - set(DEFAULT_PACK["copy"])
    assert not missing, f"copy keys used in the dashboard but absent from the pack: {sorted(missing)}"


def test_persona_rules_reproduce_the_shipped_inference():
    """The mock generator's persona inference must be pack-driven and must
    still produce today's real-estate personas for the real-estate pack."""
    from app.vertical import DEFAULT_PACK
    rules = DEFAULT_PACK["persona_rules"]
    assert rules[-1].get("when") is None, "last rule must be the unconditional default"
    personas = {r["persona"] for r in rules}
    assert {"Seller", "First-Time Buyer", "Home Buyer"} <= personas
    for r in DEFAULT_PACK["persona_recommendations"]:
        assert r in personas, f"recommendation for unknown persona {r}"
    assert set(DEFAULT_PACK["schedule_titles"]) >= {"default", "sell"}


def test_partial_mock_summary_does_not_inherit_real_estate_content(tmp_path, monkeypatch):
    """mock_summary/schedule_titles/persona_recommendations are content blocks,
    not label bags — a pack partially overriding one must replace it wholesale,
    not merge, or real-estate news/persona copy leaks into another vertical's
    demo (fix round 1)."""
    d = tmp_path / "recruiting"
    d.mkdir()
    (d / "pack.json").write_text(json.dumps({
        "mock_summary": {"greeting": "Good morning — new candidates today.", "ai_insights": []},
        "schedule_titles": {"default": "Interview"},
        "persona_recommendations": {"Passive Candidate": "Sell the mission, not the ping-pong table."},
    }))
    monkeypatch.setenv("VERTICALS_DIR", str(tmp_path))
    monkeypatch.setenv("VERTICAL", "recruiting")
    vertical.clear_cache()
    pack = vertical.load_pack()

    assert pack["mock_summary"]["greeting"] == "Good morning — new candidates today."
    assert "market_watch" not in pack["mock_summary"], (
        "real-estate market_watch leaked into a pack that never set it")

    assert pack["schedule_titles"] == {"default": "Interview"}, (
        "real-estate 'sell': 'Listing appointment' leaked into a partial schedule_titles override")

    assert pack["persona_recommendations"] == {
        "Passive Candidate": "Sell the mission, not the ping-pong table."
    }, "real-estate persona_recommendations leaked into a pack with its own personas"


def test_default_pack_persona_rules_pass_the_sanitizer_unchanged():
    """The sanitizer must never quietly rewrite/drop anything from our own
    shipped rules — if it does, either the vocabulary drifted from what the
    sanitizer allows, or the sanitizer is too strict.

    NOTE on the equality check: `_sanitize_persona_rules` returns each rule's
    NORMALIZED form, which always has an explicit `when` key (`None` for the
    unconditional default). A rule written as `{"persona": "X"}` with no
    `when` key at all is legal input but is NOT equal to its normalized
    `{"persona": "X", "when": None}` output — that rule would fail this
    `== rules` comparison even though the sanitizer accepted it fine. Every
    rule below already writes `when` explicitly (including `when: None` for
    the default), so this holds; a future rule must do the same or compare
    against the sanitized output instead of the raw input."""
    rules = vertical.DEFAULT_PACK["persona_rules"]
    assert vertical._sanitize_persona_rules(rules) == rules


@pytest.mark.parametrize("pack_path", SHIPPED_PACKS, ids=lambda p: p.parent.name)
def test_shipped_pack_persona_rules_pass_the_sanitizer_unchanged(pack_path):
    """Every verticals/*/pack.json that overrides persona_rules must ship
    rules the sanitizer accepts whole — parametrized over the directory glob
    so packs added by later tasks are covered automatically, without anyone
    having to remember to extend this test.

    Same NOTE as test_default_pack_persona_rules_pass_the_sanitizer_unchanged:
    this compares raw pack JSON against the sanitizer's NORMALIZED output, so
    a legal rule omitting the `when` key entirely (rather than writing
    `"when": null`) will fail this equality check even though the sanitizer
    accepts it. Packs added here must write `when` explicitly."""
    raw = json.loads(pack_path.read_text())
    rules = raw.get("persona_rules")
    if rules is None:
        pytest.skip(f"{pack_path} does not override persona_rules")
    assert vertical._sanitize_persona_rules(rules) == rules


def test_sanitizer_drops_malformed_persona_rules_but_keeps_good_ones():
    rules = [
        {"persona": "Seller", "when": {"field": "intent", "op": "eq", "value": "sell"}},
        "not-an-object",
        {"when": {"field": "intent", "op": "eq", "value": "sell"}},  # missing persona
        {"persona": 42, "when": None},  # non-string persona
        {"persona": "Bad Regex", "when": {"field": "name", "op": "regex", "value": "["}},
        {"persona": "Bad Op", "when": {"field": "budget", "op": "telepathy", "value": 1}},
        {"persona": "Bad Any", "when": {"any": "not-a-list"}},
        {
            "persona": "Mixed Any",
            "when": {"any": [
                {"field": "name", "op": "regex", "value": "["},  # dropped leaf
                {"field": "intent", "op": "eq", "value": "sell"},  # survives
            ]},
        },
        {"persona": "Home Buyer", "when": None},
    ]
    sanitized = vertical._sanitize_persona_rules(rules)
    personas = [r["persona"] for r in sanitized]
    assert personas == ["Seller", "Mixed Any", "Home Buyer"]
    # the bad leaf inside "Mixed Any"'s `any` was dropped; the good sibling survived
    assert sanitized[1]["when"]["any"] == [{"field": "intent", "op": "eq", "value": "sell"}]


def test_sanitizer_drops_rule_when_every_combinator_child_is_invalid():
    """A combinator whose every child fails sanitization must drop the WHOLE
    rule, not collapse to {"all": []} (vacuously true — matches every lead,
    shadowing every rule after it in the first-match-wins list) or silently
    keep {"any": []} (vacuously false) as if that were intended."""
    rules = [
        {
            "persona": "All Bad",
            "when": {"all": [
                {"field": "budget", "op": "telepathy", "value": 1},
                {"field": "name", "op": "regex", "value": "["},
            ]},
        },
        {
            "persona": "Any Bad",
            "when": {"any": [{"field": "budget", "op": "telepathy", "value": 1}]},
        },
        {"persona": "Home Buyer", "when": None},
    ]
    sanitized = vertical._sanitize_persona_rules(rules)
    personas = [r["persona"] for r in sanitized]
    assert personas == ["Home Buyer"], (
        "both 'All Bad' and 'Any Bad' should be dropped entirely — "
        f"got {personas}"
    )


def test_sanitizer_falls_back_to_default_when_nothing_survives(tmp_path, monkeypatch):
    d = tmp_path / "all-bad"
    d.mkdir()
    (d / "pack.json").write_text(json.dumps({"persona_rules": ["nonsense", {"persona": 1}]}))
    monkeypatch.setenv("VERTICALS_DIR", str(tmp_path))
    monkeypatch.setenv("VERTICAL", "all-bad")
    vertical.clear_cache()
    pack = vertical.load_pack()
    assert pack["persona_rules"] == vertical.DEFAULT_PACK["persona_rules"]


def test_non_dict_wholesale_value_is_rejected(tmp_path, monkeypatch):
    """A malformed wholesale-replace value (e.g. a string instead of an
    object) must not reach the dashboard verbatim — DailySummaryOverlay.tsx
    assumes mock_summary is an object with market_watch/ai_insights arrays
    and would throw on `.map()` over a string."""
    d = tmp_path / "bad-wholesale"
    d.mkdir()
    (d / "pack.json").write_text(json.dumps({"mock_summary": "nonsense"}))
    monkeypatch.setenv("VERTICALS_DIR", str(tmp_path))
    monkeypatch.setenv("VERTICAL", "bad-wholesale")
    vertical.clear_cache()
    pack = vertical.load_pack()
    assert pack["mock_summary"] == vertical.DEFAULT_PACK["mock_summary"]
