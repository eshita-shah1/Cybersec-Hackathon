from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AlertReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["acknowledged", "resolved", "false_positive", "escalated"]
    reviewer: str = Field(default="analyst", min_length=1, max_length=128)
    notes: str | None = Field(default=None, max_length=2000)


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer: str = Field(default="analyst", min_length=1, max_length=128)
    notes: str | None = Field(default=None, max_length=2000)
