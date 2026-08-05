import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "skills" / "daily-brief" / "scripts" / "run_daily_brief.py"
SPEC = importlib.util.spec_from_file_location("daily_brief_script", SCRIPT)
assert SPEC and SPEC.loader
daily_brief = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(daily_brief)


def _payload():
    items = []
    for index, url in enumerate(daily_brief.SOURCE_URLS):
        items.append(
            {
                "title": f"Title {index}",
                "source": f"Source {index}",
                "takeaway": f"Takeaway {index}",
                "url": url,
                "date": "2026-07-28",
                "summary": f"Summary {index}",
                "geo": "Seattle",
            }
        )
    return {
        "date": "2026-07-28",
        "generated_at": "2026-07-28T20:42:29-07:00",
        "greeting": "Daily brief",
        "market_watch": items,
        "ai_insights": [{"title": "Insight", "body": "Supported interpretation"}],
    }


def test_ai_payload_requires_every_configured_source(tmp_path):
    payload = _payload()
    path = tmp_path / "ai-brief.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert daily_brief.load_payload(str(path)) == payload

    payload["market_watch"].pop()
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one item"):
        daily_brief.load_payload(str(path))


def test_ai_payload_rejects_unconfigured_url(tmp_path):
    payload = _payload()
    payload["market_watch"][0]["url"] = "https://example.com/untrusted"
    path = tmp_path / "ai-brief.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="not a configured source"):
        daily_brief.load_payload(str(path))


def test_publisher_uses_shared_crm_helpers_and_reads_back(monkeypatch):
    payload = _payload()
    calls = []

    def post_summary(value):
        calls.append(("post", value))
        return value

    def get_summary(date):
        calls.append(("get", date))
        return payload

    crm = SimpleNamespace(
        BASE_URL=None,
        post_summary=post_summary,
        get_summary=get_summary,
    )
    monkeypatch.setattr(daily_brief, "_load_crm_tools", lambda: crm)

    base, saved = daily_brief.publish(payload, "http://127.0.0.1:8000/api")

    assert base == "http://127.0.0.1:8000/api"
    assert saved == payload
    assert calls == [("post", payload), ("get", payload["date"])]


def test_installed_skill_commands_are_location_independent():
    daily = (REPO_ROOT / "skills" / "daily-brief" / "SKILL.md").read_text()
    card = (REPO_ROOT / "skills" / "business-card-scanner" / "SKILL.md").read_text()
    crm = (REPO_ROOT / "skills" / "crm-db-operations" / "SKILL.md").read_text()
    command_center = (
        REPO_ROOT / "skills" / "daily-command-center" / "SKILL.md"
    ).read_text()

    assert "{baseDir}/scripts/run_daily_brief.py" in daily
    assert "python3 {baseDir}" not in daily
    assert "~/.openclaw/skills/crm-db-operations" not in card
    assert "python3 -c" not in crm
    assert "{baseDir}/cli.py" in crm
    assert "{baseDir}/../crm-db-operations/cli.py list_appointments" in command_center
