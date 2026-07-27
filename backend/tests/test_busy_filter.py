def test_busy_blocks_filter_slots(client, monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_API_KEY", "k")
    # seeded availability comes from schema+seed; use a Tuesday
    date = "2026-07-28"
    monkeypatch.setenv("INTEGRATIONS_MODE", "off")
    baseline = client.get(f"/api/availability?date={date}").json()
    if not baseline:                      # no availability windows seeded on this day
        import pytest
        pytest.skip("no availability windows for test date")
    first = baseline[0]

    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setattr("app.routers.calendar.integrations_busy",
                        lambda d: [(first["start_ts"], first["end_ts"])])
    filtered = client.get(f"/api/availability?date={date}").json()
    assert first not in filtered
    assert len(filtered) == len(baseline) - 1
