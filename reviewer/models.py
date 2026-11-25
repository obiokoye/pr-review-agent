"""The review's data shapes. Pydantic validates everything the model returns."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Severity = Literal["critical", "warning", "suggestion", "nit"]
Category = Literal["bug", "security", "performance", "readability", "testing", "style"]
Verdict = Literal["approve", "comment", "request_changes"]

SEVERITY_RANK: dict[str, int] = {"critical": 0, "warning": 1, "suggestion": 2, "nit": 3}


class ReviewComment(BaseModel):
    path: str
    line: int = Field(ge=1)
    severity: Severity
    category: Category
    title: str = Field(min_length=3, max_length=120)
    body: str = Field(min_length=3)
    suggestion: str | None = Field(
        default=None,
        description="Replacement code for exactly this one line (GitHub suggestion block).",
    )

    @field_validator("severity", "category", mode="before")
    @classmethod
    def _lower(cls, v: object) -> object:
        return v.lower().strip() if isinstance(v, str) else v


class LLMReview(BaseModel):
    """What we ask the model to return for one chunk of the diff."""

    summary: str
    comments: list[ReviewComment] = Field(default_factory=list)


class Review(BaseModel):
    """The final, validated review for a whole pull request."""

    summary: str
    verdict: Verdict
    comments: list[ReviewComment] = Field(default_factory=list)
    files_reviewed: list[str] = Field(default_factory=list)
    files_skipped: list[str] = Field(default_factory=list)
    dropped: int = 0  # comments thrown away because they pointed outside the diff
    model: str = ""

    def count(self, severity: str) -> int:
        return sum(1 for c in self.comments if c.severity == severity)

    def worst(self) -> str | None:
        if not self.comments:
            return None
        return min(self.comments, key=lambda c: SEVERITY_RANK[c.severity]).severity
