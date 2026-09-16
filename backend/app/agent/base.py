"""Agent driver interface for deterministic development and local OpenClaw."""
from abc import ABC, abstractmethod

from .status import AgentProbe


class AgentDriver(ABC):
    name = "base"

    @abstractmethod
    async def chat(self, message: str, session_id: str) -> str:
        """Send a user message to the agent, return its reply."""

    @abstractmethod
    async def extract(self, raw_text: str) -> dict:
        """Extract structured lead fields from unstructured text.

        Returns keys: name?, phone?, email?, budget?, area?, timeline?,
        preferences (list), intent, missing_fields (list).
        """

    @abstractmethod
    async def draft_followup(self, lead: dict) -> str:
        """Write a personalized follow-up message for this lead."""

    @abstractmethod
    async def explain_score(self, lead: dict, score: int) -> str:
        """One-sentence explanation of why this lead got this score."""

    async def connected(self) -> bool:
        return True

    async def probe(self) -> AgentProbe:
        return AgentProbe(
            status="mock",
            gateway_reachable=False,
            endpoint_enabled=False,
            last_chat_ok=True,
        )

    async def live_check(self) -> AgentProbe:
        return await self.probe()

    async def request_crm_capability(
        self,
        session_id: str,
        probe_nonce: str,
    ) -> dict:
        raise RuntimeError("CRM capability is unavailable for this driver")


def get_driver() -> AgentDriver:
    import os
    if os.environ.get("AGENT_MODE", "mock") == "openclaw":
        from .openclaw import OpenClawDriver
        return OpenClawDriver()
    from .mock import MockDriver
    return MockDriver()
