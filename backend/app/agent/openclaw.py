"""Relay to the OpenClaw gateway running on the GB10.

The gateway exposes an HTTP API (default port 18789) authenticated with a Bearer
token. Chat goes through its OpenAI-compatible chat endpoint by default; override
AGENT_CHAT_PATH if the installed OpenClaw version mounts it elsewhere.

Extraction/drafting also route through chat with instruction prompts — the agent's
system prompt + skill (see agent/prompts and agent/skills) make it answer with
bare JSON / bare text. K owns prompt quality here.
"""
import json
import logging
import os

import httpx

from .base import AgentDriver

GATEWAY_URL = os.environ.get("AGENT_GATEWAY_URL", "http://gb10:18789")
CHAT_PATH = os.environ.get("AGENT_CHAT_PATH", "/v1/chat/completions")
TOKEN = os.environ.get("AGENT_GATEWAY_TOKEN", "")
TIMEOUT = float(os.environ.get("AGENT_TIMEOUT_SECONDS", "120"))


class OpenClawDriver(AgentDriver):
    name = "openclaw"

    async def _send(self, message: str, session_id: str = "backend") -> str:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                GATEWAY_URL.rstrip("/") + CHAT_PATH,
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={
                    "model": "openclaw",
                    "user": session_id,
                    "messages": [{"role": "user", "content": message}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def chat(self, message: str, session_id: str) -> str:
        return await self._send(message, session_id)

    async def extract(self, raw_text: str) -> dict:
        try:
            reply = await self._send(
                "Extract lead fields from the note below. Reply with ONLY a JSON object "
                "with keys: name, phone, email, budget, area, timeline, "
                "preferences (array), intent (buy|sell|browse|unknown), missing_fields (array). "
                "Omit unknown scalar keys.\n\nNOTE:\n" + raw_text
            )
            start, end = reply.find("{"), reply.rfind("}")
            if start == -1 or end == -1:
                raise ValueError(f"agent returned no JSON: {reply[:200]}")
            return json.loads(reply[start:end + 1])
        except Exception as exc:  # demo insurance: lead creation must never break
            logging.warning("openclaw extract failed (%s) — regex fallback", exc)
            from .mock import MockDriver
            return await MockDriver().extract(raw_text)

    async def draft_followup(self, lead: dict) -> str:
        return await self._send(
            "Write a short, warm, personalized follow-up message (2-3 sentences, "
            "no subject line) for this real-estate lead. Reply with ONLY the message.\n\n"
            + json.dumps(lead, default=str)
        )

    async def explain_score(self, lead: dict, score: int) -> str:
        return await self._send(
            f"This lead scored {score}/100 on our deterministic priority formula. "
            "In ONE sentence, explain the score to the realtor.\n\n"
            + json.dumps(lead, default=str)
        )

    async def connected(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(GATEWAY_URL)
                return resp.status_code < 500
        except httpx.HTTPError:
            return False
