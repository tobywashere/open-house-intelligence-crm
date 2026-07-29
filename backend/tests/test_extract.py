"""Lead-note extraction (MockDriver.extract).

This is not just the demo path: OpenClawDriver.extract falls back to this
regex extractor whenever the gateway errors or is unreachable, so a name it
cannot find becomes a real "Unknown lead" row in a real deployment.

Sync tests driving the coroutine with asyncio.run — the suite has no
pytest-asyncio and the repo carries a no-new-dependencies constraint.
"""
import asyncio

import pytest

from app.agent.mock import MockDriver


def extract(text: str) -> dict:
    return asyncio.run(MockDriver().extract(text))


@pytest.mark.parametrize(
    "note,expected",
    [
        # the placeholder the Leads page ships in its own input
        ("Met Alex Rivera at the open house, looking in Redmond around $950k",
         "Alex Rivera"),
        # the chat rail's own suggested prompt
        ("Add Minh Nguyen, 425-555-0198, buyer interested in Kirkland and Redmond",
         "Minh Nguyen"),
        # bare "Name, phone, ..." shape
        ("Jane Doe, 555-0100, looking for a 3br in Bellevue", "Jane Doe"),
        # leading verb variants
        ("Called Tom Grigsby about the Renton listing", "Tom Grigsby"),
        ("Spoke with Priya Natarajan, wants a tour", "Priya Natarajan"),
        # couples joined by an ampersand stay one lead
        ("Emily & Josh Tran, $975k, Issaquah", "Emily & Josh Tran"),
        # single-word name
        ("Cher called about Seattle condos", "Cher"),
    ],
)
def test_extracts_name(note, expected):
    assert extract(note)["name"] == expected


def test_area_word_is_not_mistaken_for_a_name():
    """A note that opens with an area must not name the lead after the city."""
    out = extract("Bellevue buyer, $1.1M, relocating in 6 weeks")
    assert out.get("name") is None
    assert "name" in out["missing_fields"]


def test_name_absent_is_reported_missing_not_invented():
    out = extract("walk-in, no contact details yet")
    assert out.get("name") is None
    assert "name" in out["missing_fields"]


def test_found_name_is_not_listed_missing():
    assert "name" not in extract("Met Alex Rivera at the open house")["missing_fields"]


def test_existing_field_extraction_still_works():
    """Guards the fields that already worked — name support must not regress them."""
    out = extract(
        "Met Alex Rivera at the open house, 425-555-0142, "
        "looking in Redmond around $950k, timeline 3 months, alex@example.com"
    )
    assert out["name"] == "Alex Rivera"
    assert out["phone"] == "4255550142"
    assert out["email"] == "alex@example.com"
    assert out["budget"] == 950_000
    assert out["area"] == "Redmond"
    assert out["timeline"] == "3 months"
    # Pre-existing gap, asserted as-is so the fix above is not credited or
    # blamed for it: the intent regex matches "looking for" but not
    # "looking in", so the app's own placeholder note scores intent=unknown.
    assert out["intent"] == "unknown"


def test_intent_still_detected_on_the_phrasings_it_does_support():
    assert extract("wants to buy in Kirkland")["intent"] == "buy"
    assert extract("looking for a 3br in Renton")["intent"] == "buy"
    assert extract("wants to sell their condo")["intent"] == "sell"
