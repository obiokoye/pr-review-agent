"""The review pipeline: diff in, validated review out.

    parse diff -> skip noise -> chunk -> ask model (JSON) -> validate with Pydantic
    -> retry once on bad JSON -> drop comments outside the diff -> rank and cap
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from .diff import FileDiff, chunk_files, filter_files, parse_unified_diff
from .llm import LLM, Message
from .models import SEVERITY_RANK, LLMReview, Review, ReviewComment, Verdict
from .prompts import system_prompt, user_prompt

FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class ReviewError(RuntimeError):
    """The model never returned valid JSON."""


def extract_json(text: str) -> str:
    """Models sometimes wrap JSON in ``` fences or add a sentence. Pull out the object."""
    fenced = FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def decide_verdict(comments: list[ReviewComment]) -> Verdict:
    """The verdict comes from code, not the model, so it can't be talked into approving."""
    if any(c.severity == "critical" for c in comments):
        return "request_changes"
    if any(c.severity in {"warning", "suggestion"} for c in comments):
        return "comment"
    return "approve"


@dataclass
class PRReviewer:
    llm: LLM
    max_comments: int = 10
    max_chars: int = 24_000
    ignore: list[str] | None = None
    on_event: Callable[[str], None] | None = None

    def _emit(self, message: str) -> None:
        if self.on_event:
            self.on_event(message)

    def review(self, diff_text: str, title: str = "", description: str = "") -> Review:
        files, skipped = filter_files(parse_unified_diff(diff_text), self.ignore)
        if skipped:
            self._emit(f"skipped {len(skipped)} file(s): {', '.join(skipped)}")
        if not files:
            return Review(
                summary="No reviewable changes (only lockfiles, binaries, deletions or ignored paths).",
                verdict="approve",
                files_skipped=skipped,
                model=self.llm.model,
            )

        chunks = chunk_files(files, self.max_chars)
        summaries: list[str] = []
        raw_comments: list[ReviewComment] = []
        for i, chunk in enumerate(chunks, 1):
            self._emit(f"reviewing chunk {i}/{len(chunks)}: {', '.join(f.path for f in chunk)}")
            result = self._ask(chunk, title, description)
            summaries.append(result.summary.strip())
            raw_comments.extend(result.comments)

        kept, dropped = self._guard(raw_comments, files)
        if dropped:
            self._emit(f"dropped {dropped} comment(s) that pointed outside the diff")

        kept.sort(key=lambda c: (SEVERITY_RANK[c.severity], c.path, c.line))
        kept = kept[: self.max_comments]
        summary = summaries[0] if len(summaries) == 1 else "\n".join(f"- {s}" for s in summaries)
        return Review(
            summary=summary,
            verdict=decide_verdict(kept),
            comments=kept,
            files_reviewed=[f.path for f in files],
            files_skipped=skipped,
            dropped=dropped,
            model=self.llm.model,
        )

    def _ask(self, files: list[FileDiff], title: str, description: str) -> LLMReview:
        messages: list[Message] = [
            {"role": "system", "content": system_prompt(self.max_comments)},
            {"role": "user", "content": user_prompt(files, title, description, self.max_chars)},
        ]
        for attempt in range(2):
            raw = self.llm.complete(messages)
            try:
                return LLMReview.model_validate(json.loads(extract_json(raw)))
            except (json.JSONDecodeError, ValidationError) as exc:
                if attempt == 1:
                    raise ReviewError(f"Model returned invalid review JSON twice: {exc}") from exc
                self._emit("model reply was not valid JSON, asking it to fix it")
                messages += [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": f"That was not valid JSON for the schema ({str(exc)[:300]}). "
                        "Reply again with the JSON object only.",
                    },
                ]
        raise AssertionError("unreachable")

    @staticmethod
    def _guard(comments: list[ReviewComment], files: list[FileDiff]) -> tuple[list[ReviewComment], int]:
        """Keep only comments on real, commentable lines, without duplicates."""
        valid = {f.path: f.commentable_lines for f in files}
        kept: list[ReviewComment] = []
        seen: set[tuple[str, int, str]] = set()
        dropped = 0
        for c in comments:
            key = (c.path, c.line, c.title.strip().lower())
            if c.line not in valid.get(c.path, set()):
                dropped += 1
            elif key not in seen:
                seen.add(key)
                kept.append(c)
        return kept, dropped
