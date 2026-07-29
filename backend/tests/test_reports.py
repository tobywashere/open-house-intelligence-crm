"""Generated report contracts.

These routes are an agent-to-UI trust boundary: generated payloads must be
structurally valid and may only refer to CRM records that actually exist.
"""


def test_briefing_post_rejects_unknown_lead_reference(client):
    response = client.post(
        "/api/briefing",
        json={
            "date": "2026-07-28",
            "meeting_briefs": [
                {
                    "lead_id": 999,
                    "prepare": ["Review history"],
                    "recommendation": "Ask about timing",
                }
            ],
        },
    )

    assert response.status_code == 422


def test_summary_post_requires_a_real_source_url(client):
    response = client.post(
        "/api/summary",
        json={
            "date": "2026-07-28",
            "greeting": "Good morning",
            "market_watch": [
                {
                    "title": "Rates changed",
                    "source": "Unknown",
                    "url": "not-a-url",
                    "takeaway": "Check the source",
                }
            ],
            "ai_insights": [],
        },
    )

    assert response.status_code == 422
