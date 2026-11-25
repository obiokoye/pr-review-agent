"""A small GitHub REST client: read a PR's diff, post a review with inline comments."""

from __future__ import annotations

import httpx

from .models import Review
from .render import comment_markdown, comments_as_markdown_list, review_markdown


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(
        self,
        token: str,
        api_url: str = "https://api.github.com",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = httpx.Client(
            base_url=api_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "pr-review-agent",
            },
            timeout=30,
            transport=transport,
        )

    def _check(self, r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            raise GitHubError(f"GitHub API {r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:300]}")
        return r

    def get_pull(self, repo: str, number: int) -> dict:
        return self._check(self._http.get(f"/repos/{repo}/pulls/{number}")).json()

    def get_pull_diff(self, repo: str, number: int) -> str:
        r = self._http.get(f"/repos/{repo}/pulls/{number}", headers={"Accept": "application/vnd.github.diff"})
        return self._check(r).text

    def post_review(
        self,
        repo: str,
        number: int,
        commit_id: str,
        review: Review,
        demo: bool = False,
        allow_request_changes: bool = False,
    ) -> dict:
        """Post one review. The bot never approves: a human does that.

        If GitHub rejects the inline comments (422, e.g. the PR moved on since
        we fetched it), retry once with the comments listed in the body.
        """
        event = "REQUEST_CHANGES" if (review.verdict == "request_changes" and allow_request_changes) else "COMMENT"
        body = review_markdown(review, demo=demo)
        payload = {
            "commit_id": commit_id,
            "event": event,
            "body": body,
            "comments": [
                {"path": c.path, "line": c.line, "side": "RIGHT", "body": comment_markdown(c)}
                for c in review.comments
            ],
        }
        r = self._http.post(f"/repos/{repo}/pulls/{number}/reviews", json=payload)
        if r.status_code == 422 and review.comments:
            payload["comments"] = []
            payload["body"] = body + "\n\n---\n\n" + comments_as_markdown_list(review)
            r = self._http.post(f"/repos/{repo}/pulls/{number}/reviews", json=payload)
        return self._check(r).json()
