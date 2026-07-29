"""OpenClaw gateway readiness and response-shape handling."""

import asyncio

import httpx
import pytest

from app.agent.mock import MockDriver
from app.agent.openclaw import OpenClawDriver


class FakeClient:
    def __init__(self, option_status=405, post_status=200, post_json=None):
        self.option_status = option_status
        self.post_status = post_status
        self.post_json = post_json or {
            "choices": [{"message": {"content": "READY"}}]
        }

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def options(self, url, **kwargs):
        return httpx.Response(
            self.option_status,
            request=httpx.Request("OPTIONS", url),
        )

    async def post(self, url, **kwargs):
        return httpx.Response(
            self.post_status,
            request=httpx.Request("POST", url),
            json=self.post_json,
        )


def client_factory(*, option_status=405, post_status=200, post_json=None):
    def make_client(**kwargs):
        return FakeClient(option_status, post_status, post_json)

    return make_client


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (401, "unauthorized"),
        (403, "unauthorized"),
        (404, "endpoint_disabled"),
        (405, "endpoint_enabled"),
    ],
)
def test_probe_classifies_configured_chat_endpoint(code, expected):
    driver = OpenClawDriver(client_factory=client_factory(option_status=code))

    probe = asyncio.run(driver.probe())

    assert probe.status == expected
    assert probe.endpoint_enabled is (code == 405)


def test_connected_is_false_for_auth_and_missing_endpoint():
    for code in (401, 403, 404):
        driver = OpenClawDriver(client_factory=client_factory(option_status=code))
        assert asyncio.run(driver.connected()) is False


def test_malformed_successful_response_marks_chat_failed():
    driver = OpenClawDriver(
        client_factory=client_factory(post_json={"choices": []})
    )

    reply = asyncio.run(driver.chat("hello", "dashboard"))
    probe = asyncio.run(driver.probe())

    assert "agent" in reply.lower() and ("unavailable" in reply.lower() or "didn't answer" in reply.lower())
    assert probe.status == "failed"
    assert probe.last_chat_ok is False


def test_valid_completion_marks_chat_verified():
    driver = OpenClawDriver(client_factory=client_factory())

    reply = asyncio.run(driver.chat("hello", "dashboard"))
    probe = asyncio.run(driver.probe())

    assert reply == "READY"
    assert probe.status == "verified"
    assert probe.last_chat_ok is True


def test_mock_follow_up_prompt_accepts_hyphenated_wording():
    reply = asyncio.run(
        MockDriver().chat(
            "Which active buyers need a follow-up?",
            "dashboard",
        )
    )

    assert "2 leads haven't been touched" in reply
