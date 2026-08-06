"""Typed agent-to-dashboard report contracts.

Generated prose is untrusted input. These models keep it bounded and ensure
external research has a source the operator can open. Canonical CRM facts are
rehydrated separately by briefing_service.py.
"""

from datetime import date as Date
from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


DAILY_BRIEF_SOURCE_URLS = {
    "https://www.bls.gov/eag/eag.wa_seattle_msa.htm",
    "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260617a.htm",
    (
        "https://frontporch.seattle.gov/2026/07/07/"
        "city-of-seattle-opens-second-round-of-neighborhood-funding-for-community-led-projects/"
    ),
}


class MeetingAdvice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lead_id: int = Field(gt=0)
    prepare: list[str] = Field(default_factory=list, max_length=10)
    recommendation: str | None = Field(default=None, max_length=2000)


class BriefingPost(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: Date
    generated_at: datetime | None = None
    meeting_briefs: list[MeetingAdvice] = Field(default_factory=list, max_length=100)


class MarketWatchItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=300)
    source: str = Field(min_length=1, max_length=200)
    url: AnyHttpUrl
    takeaway: str = Field(min_length=1, max_length=3000)
    date: str = Field(min_length=1, max_length=50)
    summary: str = Field(min_length=1, max_length=5000)
    geo: str = Field(min_length=1, max_length=200)
    content_opportunity: str | None = Field(default=None, max_length=3000)

    @field_validator("source", "title", "takeaway", "summary", "geo")
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        value = value.strip()
        try:
            Date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("must be an ISO-8601 date") from exc
        return value

    @field_validator("url")
    @classmethod
    def configured_source_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if str(value) not in DAILY_BRIEF_SOURCE_URLS:
            raise ValueError("must be one of the configured daily brief sources")
        return value

class InsightItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=5000)


class DailySummaryPost(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: Date
    generated_at: datetime | None = None
    greeting: str = Field(default="", max_length=1000)
    market_watch: list[MarketWatchItem] = Field(default_factory=list, max_length=20)
    ai_insights: list[InsightItem] = Field(default_factory=list, max_length=20)
