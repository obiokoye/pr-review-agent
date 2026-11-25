import json

import httpx
import pytest

from rich.console import Console

from reviewer.cli import main, run_action, should_fail
from reviewer.demo import demo_llm
from reviewer.github import GitHubClient
from reviewer.llm import FakeLLM
from reviewer.models import Review, ReviewComment
from reviewer.render import comment_markdown, review_markdown


def make_review(severity: str = "critical", suggestion: str | None = None) -> Review:
    c = ReviewComment(path="a.py", line=3, severity=severity, category="security", title="Bad thing", body="Fix it.", suggestion=suggestion)
    return Review(summary="S", verdict="request_changes", comments=[c], files_reviewed=["a.py"], model="m")


class FakeGitHub:
    """Records requests, like Http::fake() + Http::assertSent()."""

    def __init__(self, diff: str, reject_inline: bool = False) -> None:
        self.diff = diff
        self.reject_inline = reject_inline
        self.posted: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.headers["accept"] == "application/vnd.github.diff":
            return httpx.Response(200, text=self.diff)
        if request.method == "GET":
            return httpx.Response(200, json={"title": "T", "body": "", "head": {"sha": "abc123"}})
        payload = json.loads(request.content)
        self.posted.append(payload)
        if self.reject_inline and payload["comments"]:
            return httpx.Response(422, json={"message": "line must be part of the diff"})
        return httpx.Response(200, json={"id": 1})

    def client(self) -> GitHubClient:
        return GitHubClient("t", transport=httpx.MockTransport(self.handler))


def test_post_review_sends_inline_comments_and_never_approves():
    fake = FakeGitHub("")
    fake.client().post_review("o/r", 1, "sha", make_review())
    [payload] = fake.posted
    assert payload["event"] == "COMMENT"
    assert payload["comments"][0] == {"path": "a.py", "line": 3, "side": "RIGHT", "body": comment_markdown(make_review().comments[0])}


def test_request_changes_is_opt_in():
    fake = FakeGitHub("")
    fake.client().post_review("o/r", 1, "sha", make_review(), allow_request_changes=True)
    assert fake.posted[0]["event"] == "REQUEST_CHANGES"


def test_falls_back_to_body_only_when_github_rejects_inline_comments():
    fake = FakeGitHub("", reject_inline=True)
    fake.client().post_review("o/r", 1, "sha", make_review())
    assert len(fake.posted) == 2
    assert fake.posted[1]["comments"] == [] and "`a.py:3`" in fake.posted[1]["body"]


def test_markdown_has_suggestion_block_and_demo_banner():
    md = comment_markdown(make_review(suggestion="safe()").comments[0])
    assert "🔴 **Critical · security**: Bad thing" in md and "```suggestion\nsafe()\n```" in md
    body = review_markdown(make_review(), demo=True)
    assert "Demo mode: scripted AI replies" in body and "a human still approves" in body


def test_should_fail_threshold():
    assert not should_fail(make_review("warning"), "never")
    assert not should_fail(make_review("warning"), "critical")
    assert should_fail(make_review("warning"), "warning")
    assert should_fail(make_review("critical"), "suggestion")


def test_action_end_to_end(tmp_path, sql_diff):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7, "title": "Add search", "body": "", "head": {"sha": "abc123"}, "base": {"repo": {"full_name": "o/r"}}}}))
    summary = tmp_path / "summary.md"
    fake = FakeGitHub(sql_diff)
    env = {"GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": "o/r", "GITHUB_STEP_SUMMARY": str(summary), "FAIL_ON": "critical"}

    code = run_action(env, demo_llm(), True, Console(file=open(tmp_path / "log.txt", "w")), gh=fake.client())

    assert code == 1  # critical finding + FAIL_ON=critical fails the check
    [payload] = fake.posted
    assert payload["commit_id"] == "abc123" and payload["event"] == "COMMENT"
    assert sorted(c["line"] for c in payload["comments"]) == [18, 20, 22, 26]
    assert "Changes requested" in summary.read_text(encoding="utf-8")


def test_action_ignores_non_pr_events(tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"push": {}}))
    fake = FakeGitHub("")
    assert run_action({"GITHUB_EVENT_PATH": str(event)}, FakeLLM([]), True, Console(file=open(tmp_path / "l", "w")), gh=fake.client()) == 0
    assert fake.posted == []


def test_cli_review_json_in_demo_mode(monkeypatch, capsys, sql_diff, tmp_path):
    for key in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DEMO_MODE", "true")
    path = tmp_path / "x.diff"
    path.write_text(sql_diff)
    assert main(["review", str(path), "--json", "--fail-on", "never"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "request_changes" and data["dropped"] == 1


def test_cli_fail_on_returns_nonzero(monkeypatch, sql_diff, tmp_path):
    monkeypatch.setenv("DEMO_MODE", "true")
    path = tmp_path / "x.diff"
    path.write_text(sql_diff)
    assert main(["review", str(path), "--json", "--fail-on", "critical"]) == 1


def test_cli_pr_ref_must_be_valid(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    with pytest.raises(SystemExit):
        main(["pr", "not-a-ref"])
