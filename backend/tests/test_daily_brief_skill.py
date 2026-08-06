import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys
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
    monkeypatch.setenv("CRM_API_URL", "http://127.0.0.1:8000/api")
    monkeypatch.setattr(daily_brief, "_load_crm_tools", lambda: crm)

    base, saved = daily_brief.publish(payload)

    assert base == "http://127.0.0.1:8000/api"
    assert saved == payload
    assert calls == [("post", payload), ("get", payload["date"])]


def test_publish_rejects_off_origin_before_loading_authenticated_client(monkeypatch):
    loaded = False

    def load_client():
        nonlocal loaded
        loaded = True
        raise AssertionError("authenticated CRM client must not load")

    monkeypatch.setenv("CRM_API_URL", "https://attacker.example/api")
    monkeypatch.setenv("OHI_API_TOKEN", "must-not-leave-this-process")
    monkeypatch.setattr(daily_brief, "_load_crm_tools", load_client)

    with pytest.raises(ValueError, match="loopback"):
        daily_brief.publish(_payload())

    assert loaded is False


def test_model_runner_rejects_api_base_before_loading_authenticated_client(
    monkeypatch,
):
    loaded = False

    def load_client():
        nonlocal loaded
        loaded = True
        raise AssertionError("authenticated CRM client must not load")

    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "--api-base", "https://attacker.example/api"],
    )
    monkeypatch.setenv("OHI_API_TOKEN", "must-not-leave-this-process")
    monkeypatch.setattr(daily_brief, "build_payload", _payload)
    monkeypatch.setattr(daily_brief, "_load_crm_tools", load_client)

    with pytest.raises(SystemExit) as exc:
        daily_brief.main()

    assert exc.value.code == 2
    assert loaded is False


def test_installed_skill_commands_are_location_independent():
    daily = (REPO_ROOT / "skills" / "daily-brief" / "SKILL.md").read_text()
    card = (REPO_ROOT / "skills" / "business-card-scanner" / "SKILL.md").read_text()
    crm = (REPO_ROOT / "skills" / "crm-db-operations" / "SKILL.md").read_text()
    command_center = (
        REPO_ROOT / "skills" / "daily-command-center" / "SKILL.md"
    ).read_text()

    assert "{baseDir}/scripts/run_daily_brief.py" in daily
    assert "python3 {baseDir}" not in daily
    assert "python3 skills/daily-brief" not in daily
    assert "~/.openclaw/skills/crm-db-operations" not in card
    assert "python3 -c" not in crm
    assert "{baseDir}/cli.py" in crm
    assert "{baseDir}/../crm-db-operations/cli.py list_appointments" in command_center
    assert "sample-crm.json" not in command_center
    assert "do not publish" in command_center.lower()


def test_deterministic_failure_is_disclosed_without_inventing_item(monkeypatch):
    def offline(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(daily_brief, "_fetch_html", offline)
    monkeypatch.setattr(daily_brief, "_job_item_api", offline)

    payload = daily_brief.build_payload()

    assert payload["market_watch"] == []
    assert payload["greeting"] == "Daily brief sources are unavailable."
    failures = [
        item for item in payload["ai_insights"] if item["title"] == "Sources unavailable"
    ]
    assert len(failures) == 1
    assert "offline" in failures[0]["body"]


def test_deterministic_partial_failure_keeps_only_successful_source_items(monkeypatch):
    def item(url, title):
        return {
            "title": title,
            "source": "Verified source",
            "takeaway": "Only this parsed source item may be displayed.",
            "url": url,
            "date": "2026-08-05",
            "summary": "The source parser returned this item.",
            "geo": "Seattle",
        }

    monkeypatch.setattr(daily_brief, "_fetch_html", lambda _url: "source page")
    monkeypatch.setattr(
        daily_brief, "_job_item", lambda _html: item(daily_brief.JOB_URL, "Jobs")
    )

    def unavailable(_html):
        raise RuntimeError("offline")

    monkeypatch.setattr(daily_brief, "_fed_item", unavailable)
    monkeypatch.setattr(
        daily_brief,
        "_community_item",
        lambda _html: item(daily_brief.COMMUNITY_URL, "Community"),
    )

    payload = daily_brief.build_payload()

    assert [item["url"] for item in payload["market_watch"]] == [
        daily_brief.JOB_URL,
        daily_brief.COMMUNITY_URL,
    ]
    failures = [
        item for item in payload["ai_insights"] if item["title"] == "Sources unavailable"
    ]
    assert len(failures) == 1
    assert "Federal Reserve: offline" in failures[0]["body"]


def _bls_response(*, release_date=None):
    response = {
        "status": "REQUEST_SUCCEEDED",
        "Results": {
            "series": [
                {
                    "seriesID": daily_brief.BLS_UNEMPLOYMENT_SERIES,
                    "data": [
                        {"year": "2026", "period": "M06", "value": "4.6"},
                        {"year": "2026", "period": "M05", "value": "4.4"},
                    ],
                },
                {
                    "seriesID": daily_brief.BLS_NONFARM_SERIES,
                    "data": [
                        {"year": "2026", "period": "M06", "value": "2100"},
                        {"year": "2025", "period": "M06", "value": "2000"},
                    ],
                },
            ]
        },
    }
    if release_date is not None:
        response["releaseDate"] = release_date
    return response


def test_bls_api_uses_authoritative_release_date_not_run_date(monkeypatch):
    class FrozenDate(dt.date):
        @classmethod
        def today(cls):
            return cls(2035, 1, 2)

    monkeypatch.setattr(daily_brief.dt, "date", FrozenDate)
    monkeypatch.setattr(
        daily_brief,
        "_fetch_json",
        lambda *_args, **_kwargs: _bls_response(release_date="2026-07-02"),
    )

    item = daily_brief._job_item_api()

    assert item["date"] == "2026-07-02"


def test_bls_api_omits_item_without_authoritative_release_date(monkeypatch):
    monkeypatch.setattr(
        daily_brief,
        "_fetch_json",
        lambda *_args, **_kwargs: _bls_response(),
    )

    with pytest.raises(ValueError, match="publication date"):
        daily_brief._job_item_api()


def test_bls_html_does_not_use_retrieval_date_as_publication_date():
    page = (
        "Unemployment Rate 4.4 4.6 "
        "Nonfarm Wage and Salary Employment "
        "Total Nonfarm 2,100 12-month % change 5.0 "
        "Mining, Logging, and Construction "
        "Data extracted on: August 6, 2026"
    )

    with pytest.raises(ValueError, match="publication date"):
        daily_brief._job_item(page)


def test_bls_html_prefers_explicit_release_date_over_retrieval_date():
    page = (
        "Release date: July 2, 2026 "
        "Unemployment Rate 4.4 4.6 "
        "Nonfarm Wage and Salary Employment "
        "Total Nonfarm 2,100 12-month % change 5.0 "
        "Mining, Logging, and Construction "
        "Data extracted on: August 6, 2026"
    )

    assert daily_brief._job_item(page)["date"] == "2026-07-02"
