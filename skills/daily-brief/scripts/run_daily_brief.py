#!/usr/bin/env python3
"""Fetch the three daily-brief sources, publish the report, and verify it.

Stdlib-only so OpenClaw can run the installed script as one terminal tool call.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
import urllib.request

JOB_URL = "https://www.bls.gov/eag/eag.wa_seattle_msa.htm"
BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BLS_UNEMPLOYMENT_SERIES = "LAUMT534266000000003"
BLS_NONFARM_SERIES = "SMU53426600000000001"
FED_URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260617a.htm"
COMMUNITY_URL = (
    "https://frontporch.seattle.gov/2026/07/07/"
    "city-of-seattle-opens-second-round-of-neighborhood-funding-for-community-led-projects/"
)
SOURCE_URLS = (JOB_URL, FED_URL, COMMUNITY_URL)
USER_AGENT = "OpenHouseIntelligence/1.0 daily-brief"
REQUIRED_KEYS = {"date", "generated_at", "greeting", "market_watch", "ai_insights"}
MARKET_ITEM_KEYS = {"title", "source", "takeaway", "url", "date", "summary", "geo"}
INSIGHT_KEYS = {"title", "body"}
CRM_TOOLS_PATH = (
    Path(__file__).resolve().parents[2] / "crm-db-operations" / "tools.py"
)


def _fetch_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def _fetch_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON source did not return an object")
    return value


def _text(raw_html: str) -> str:
    cleaned = re.sub(r"<(script|style)\b.*?</\1>", " ", raw_html, flags=re.I | re.S)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    return re.sub(r"\s+", " ", html.unescape(cleaned)).strip()


def _numbers_between(text: str, start: str, end: str) -> list[float]:
    match = re.search(re.escape(start) + r"(.*?)" + re.escape(end), text, re.I | re.S)
    if not match:
        return []
    return [
        float(value.replace(",", ""))
        for value in re.findall(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?", match.group(1))
    ]


def _job_item(raw_html: str) -> dict:
    text = _text(raw_html)
    rates = _numbers_between(text, "Unemployment Rate", "Nonfarm Wage and Salary Employment")
    totals = _numbers_between(text, "Total Nonfarm", "12-month % change")
    changes = _numbers_between(text, "12-month % change", "Mining, Logging, and Construction")
    if len(rates) < 2 or not totals or not changes:
        raise ValueError("BLS page did not contain the expected labor table")
    current_rate = rates[-1]
    prior_rate = rates[-2]
    total_jobs = totals[-1]
    annual_change = changes[-1]
    source_date = _publication_date_from_text(text)
    direction = "up" if current_rate > prior_rate else "down" if current_rate < prior_rate else "unchanged"
    return {
        "title": f"Seattle-area unemployment {direction} to {current_rate:.1f}% in June",
        "source": "U.S. Bureau of Labor Statistics",
        "takeaway": (
            f"The preliminary June reading moved from {prior_rate:.1f}% to {current_rate:.1f}%; "
            f"total nonfarm employment was {annual_change:.1f}% above a year earlier."
        ),
        "url": JOB_URL,
        "date": source_date,
        "summary": (
            f"BLS reported a preliminary {current_rate:.1f}% unemployment rate for "
            f"Seattle-Tacoma-Bellevue in June 2026, compared with {prior_rate:.1f}% in May. "
            f"Total nonfarm employment was {total_jobs / 1000:.4f} million."
        ),
        "geo": "Seattle-Tacoma-Bellevue",
    }


def _job_item_api() -> dict:
    response = _fetch_json(
        BLS_API_URL,
        {
            "seriesid": [BLS_UNEMPLOYMENT_SERIES, BLS_NONFARM_SERIES],
            "startyear": "2025",
            "endyear": "2026",
        },
    )
    if response.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError(f"BLS API failed: {response.get('message')}")
    source_date = _publication_date_from_api(response)
    series = {
        item.get("seriesID"): item.get("data", [])
        for item in response.get("Results", {}).get("series", [])
    }

    def value(series_id: str, year: str, period: str) -> float:
        for observation in series.get(series_id, []):
            if observation.get("year") == year and observation.get("period") == period:
                return float(observation["value"])
        raise ValueError(f"BLS series {series_id} lacks {year} {period}")

    current_rate = value(BLS_UNEMPLOYMENT_SERIES, "2026", "M06")
    prior_rate = value(BLS_UNEMPLOYMENT_SERIES, "2026", "M05")
    total_jobs = value(BLS_NONFARM_SERIES, "2026", "M06")
    prior_year_jobs = value(BLS_NONFARM_SERIES, "2025", "M06")
    annual_change = (total_jobs / prior_year_jobs - 1) * 100
    direction = (
        "up"
        if current_rate > prior_rate
        else "down"
        if current_rate < prior_rate
        else "unchanged"
    )
    return {
        "title": f"Seattle-area unemployment {direction} to {current_rate:.1f}% in June",
        "source": "U.S. Bureau of Labor Statistics",
        "takeaway": (
            f"The June reading moved from {prior_rate:.1f}% to {current_rate:.1f}%; "
            f"total nonfarm employment was {annual_change:.1f}% above a year earlier."
        ),
        "url": JOB_URL,
        "date": source_date,
        "summary": (
            f"BLS reported a {current_rate:.1f}% unemployment rate for "
            f"Seattle-Tacoma-Bellevue in June 2026, compared with {prior_rate:.1f}% in May. "
            f"Total nonfarm employment was {total_jobs / 1000:.4f} million."
        ),
        "geo": "Seattle-Tacoma-Bellevue",
    }


def _parse_publication_date(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("BLS source lacks an authoritative publication date")
    cleaned = value.strip()
    for parser in (
        lambda candidate: dt.date.fromisoformat(candidate),
        lambda candidate: dt.datetime.strptime(candidate, "%B %d, %Y").date(),
    ):
        try:
            return parser(cleaned).isoformat()
        except ValueError:
            continue
    raise ValueError("BLS publication date is not a supported date")


def _publication_date_from_text(text: str) -> str:
    match = re.search(
        r"(?:release|publication)\s+date\s*:?\s*"
        r"([A-Z][a-z]+ \d{1,2}, \d{4}|\d{4}-\d{2}-\d{2})",
        text,
        re.I,
    )
    if not match:
        raise ValueError("BLS source lacks an authoritative publication date")
    return _parse_publication_date(match.group(1))


def _publication_date_from_api(response: dict) -> str:
    containers = [response, response.get("Results")]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("releaseDate", "release_date", "publicationDate", "publication_date"):
            if key in container:
                return _parse_publication_date(container[key])
    raise ValueError("BLS API lacks an authoritative publication date")


def _fed_item(raw_html: str) -> dict:
    text = _text(raw_html)
    rate = re.search(
        r"target range for the federal funds rate at\s+([0-9-]+/[0-9]+)\s+to\s+([0-9-]+/[0-9]+)",
        text,
        re.I,
    )
    if not rate:
        raise ValueError("Federal Reserve page did not contain the target range")
    low, high = rate.groups()
    return {
        "title": f"Federal Reserve held its target rate at {low}%–{high}%",
        "source": "Federal Reserve Board",
        "takeaway": (
            "The policy rate remained unchanged while the Committee said inflation "
            "was elevated relative to its 2% goal."
        ),
        "url": FED_URL,
        "date": "2026-06-17",
        "summary": (
            f"The FOMC voted 12–0 to maintain the federal funds target range at "
            f"{low}% to {high}%. It said economic activity was expanding at a solid pace."
        ),
        "geo": "United States",
    }


def _community_item(raw_html: str) -> dict:
    text = _text(raw_html)
    if "$50,000" not in text or "September 8, 2026" not in text:
        raise ValueError("Seattle page did not contain the expected grant amount and deadline")
    return {
        "title": "Seattle opens neighborhood grants up to $50,000",
        "source": "Seattle Department of Neighborhoods",
        "takeaway": (
            "Community groups can seek funding for organizing, public art, parks, "
            "cultural events, and community facilities."
        ),
        "url": COMMUNITY_URL,
        "date": "2026-07-07",
        "summary": (
            "Seattle opened a Community Partnership Fund application round with awards "
            "up to $50,000. Applications are due by 5:00 p.m. on September 8, 2026."
        ),
        "geo": "Seattle",
    }


def validate_payload(payload: object, *, require_all_sources: bool = False) -> dict:
    if not isinstance(payload, dict) or set(payload) != REQUIRED_KEYS:
        raise ValueError(
            "daily summary must contain exactly: " + ", ".join(sorted(REQUIRED_KEYS))
        )
    try:
        dt.date.fromisoformat(payload["date"])
        dt.datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("date or generated_at is not valid ISO-8601") from exc
    if not isinstance(payload["greeting"], str) or not payload["greeting"].strip():
        raise ValueError("greeting must be a non-empty string")

    market_watch = payload["market_watch"]
    if not isinstance(market_watch, list):
        raise ValueError("market_watch must be a list")
    if require_all_sources and not market_watch:
        raise ValueError("market_watch must contain every configured source")
    if len(market_watch) > len(SOURCE_URLS):
        raise ValueError("market_watch contains more items than configured sources")
    if require_all_sources and len(market_watch) != len(SOURCE_URLS):
        raise ValueError("market_watch must contain exactly one item per configured URL")
    seen_urls: set[str] = set()
    for index, item in enumerate(market_watch):
        if not isinstance(item, dict) or not MARKET_ITEM_KEYS.issubset(item):
            raise ValueError(f"market_watch[{index}] lacks required fields")
        if any(not isinstance(item[key], str) or not item[key].strip() for key in MARKET_ITEM_KEYS):
            raise ValueError(f"market_watch[{index}] fields must be non-empty strings")
        try:
            dt.date.fromisoformat(item["date"])
        except ValueError as exc:
            raise ValueError(f"market_watch[{index}].date is not ISO-8601") from exc
        if item["url"] not in SOURCE_URLS:
            raise ValueError(f"market_watch[{index}].url is not a configured source")
        if item["url"] in seen_urls:
            raise ValueError(f"market_watch[{index}].url is duplicated")
        seen_urls.add(item["url"])
    if require_all_sources and seen_urls != set(SOURCE_URLS):
        raise ValueError("market_watch must cover every configured URL exactly once")

    insights = payload["ai_insights"]
    if not isinstance(insights, list) or not insights:
        raise ValueError("ai_insights must contain at least one item")
    for index, item in enumerate(insights):
        if not isinstance(item, dict) or not INSIGHT_KEYS.issubset(item):
            raise ValueError(f"ai_insights[{index}] lacks title or body")
        if any(not isinstance(item[key], str) or not item[key].strip() for key in INSIGHT_KEYS):
            raise ValueError(f"ai_insights[{index}] fields must be non-empty strings")
    return payload


def build_payload() -> dict:
    now = dt.datetime.now().astimezone()
    items: list[dict] = []
    failures: list[str] = []
    try:
        items.append(_job_item(_fetch_html(JOB_URL)))
    except Exception:
        try:
            # The BLS HTML page frequently rejects automated clients with 403.
            # Its official public API exposes the same underlying series.
            items.append(_job_item_api())
        except Exception as exc:
            failures.append(f"job market: {exc}")
    for label, url, parser in (
        ("Federal Reserve", FED_URL, _fed_item),
        ("Seattle community", COMMUNITY_URL, _community_item),
    ):
        try:
            items.append(parser(_fetch_html(url)))
        except Exception as exc:
            failures.append(f"{label}: {exc}")
    insights: list[dict] = []
    if items:
        insights.append(
            {
                "title": "Sources checked",
                "body": "This brief shows only the source-backed items listed above.",
            }
        )
    if failures:
        insights.append({"title": "Sources unavailable", "body": "; ".join(failures)})
    payload = {
        "date": now.date().isoformat(),
        "generated_at": now.isoformat(timespec="seconds"),
        "greeting": (
            "Your Seattle daily brief is ready."
            if items
            else "Daily brief sources are unavailable."
        ),
        "market_watch": items,
        "ai_insights": insights,
    }
    return validate_payload(payload)


def load_payload(path: str) -> dict:
    payload_path = Path(path)
    if payload_path.stat().st_size > 256 * 1024:
        raise ValueError("daily summary payload exceeds 256 KiB")
    with payload_path.open(encoding="utf-8") as handle:
        return validate_payload(json.load(handle), require_all_sources=True)


def _api_candidates() -> list[str]:
    configured = os.environ.get("CRM_API_URL")
    if configured:
        return [configured.rstrip("/")]
    # Dev server is :8000; single-port/OpenClaw production is :8080.
    return ["http://127.0.0.1:8000/api", "http://127.0.0.1:8080/api"]


def _validated_local_api_base(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("CRM_API_URL must be a valid loopback API URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("CRM_API_URL must be an HTTP loopback API URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("CRM_API_URL must not include credentials, query, or fragment")
    hostname = parsed.hostname.lower()
    is_loopback = hostname == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback:
        raise ValueError("CRM_API_URL must use a loopback host")
    if parsed.path.rstrip("/") != "/api":
        raise ValueError("CRM_API_URL must point to the local /api root")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("CRM_API_URL port is outside the valid range")
    return value.rstrip("/")


def _load_crm_tools():
    if not CRM_TOOLS_PATH.is_file():
        raise RuntimeError(f"crm-db-operations tools not found: {CRM_TOOLS_PATH}")
    spec = importlib.util.spec_from_file_location("daily_brief_crm_tools", CRM_TOOLS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load CRM tools: {CRM_TOOLS_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def publish(payload: dict) -> tuple[str, dict]:
    bases = [_validated_local_api_base(base) for base in _api_candidates()]
    crm = _load_crm_tools()
    errors: list[str] = []
    for base in bases:
        # Use the shared CRM client for both calls after every candidate has
        # been proven to be a local API origin.
        crm.BASE_URL = base
        try:
            posted = crm.post_summary(payload)
            saved = crm.get_summary(payload["date"])
            if not isinstance(posted, dict) or not isinstance(saved, dict):
                raise ValueError("summary endpoint did not return JSON")
            if saved.get("generated_at") != payload["generated_at"]:
                raise ValueError("summary read-back generated_at did not match")
            return base, saved
        except Exception as exc:
            errors.append(f"{base}: {exc}")
    raise RuntimeError("could not publish daily brief: " + " | ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    payload = build_payload()
    if args.dry_run:
        print(json.dumps({"ok": True, "published": False, "payload": payload}))
        return 0
    api_base, saved = publish(payload)
    print(
        json.dumps(
            {
                "ok": True,
                "published": True,
                "api_base": api_base,
                "date": saved["date"],
                "generated_at": saved["generated_at"],
                "market_items": len(saved["market_watch"]),
                "insight_items": len(saved["ai_insights"]),
            }
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)
