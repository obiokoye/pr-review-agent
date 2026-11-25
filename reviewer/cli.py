"""Command line entry point.

    python -m reviewer review examples/sql-injection.diff   # review a diff file ("-" = stdin)
    python -m reviewer review --git main                    # review your branch against main
    python -m reviewer pr owner/repo#12 [--post]            # review a real PR (needs GITHUB_TOKEN)
    python -m reviewer action                               # what the GitHub Action runs
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

from .agent import PRReviewer
from .demo import DEMO_NOTICE
from .diff import filter_files, parse_unified_diff
from .github import GitHubClient
from .llm import LLM, is_truthy, make_llm
from .models import SEVERITY_RANK, Review
from .render import print_review, review_markdown

FAIL_LEVELS = ["never", "critical", "warning", "suggestion", "nit"]


def should_fail(review: Review, fail_on: str) -> bool:
    if fail_on == "never":
        return False
    return any(SEVERITY_RANK[c.severity] <= SEVERITY_RANK[fail_on] for c in review.comments)


def _ignore_list(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def build_reviewer(llm: LLM, console: Console, max_comments: int, ignore: list[str]) -> PRReviewer:
    return PRReviewer(
        llm=llm,
        max_comments=max_comments,
        ignore=ignore,
        on_event=lambda m: console.print(f"  [dim]› {m}[/dim]"),
    )


def _read_diff(args: argparse.Namespace) -> str:
    if args.git:
        return subprocess.run(
            ["git", "diff", f"{args.git}...HEAD"], capture_output=True, text=True, check=True
        ).stdout
    if args.diff in (None, "-"):
        return sys.stdin.read()
    return Path(args.diff).read_text(encoding="utf-8")


def cmd_review(args: argparse.Namespace, console: Console, llm: LLM, demo: bool) -> int:
    diff_text = _read_diff(args)
    log_console = Console(stderr=True) if args.json else console  # keep stdout pure JSON
    reviewer = build_reviewer(llm, log_console, args.max_comments, _ignore_list(args.ignore))
    review = reviewer.review(diff_text, title=args.title or "")
    if args.json:
        print(review.model_dump_json(indent=2))
    else:
        files, _ = filter_files(parse_unified_diff(diff_text), reviewer.ignore)
        print_review(console, review, files, demo=demo)
    return 1 if should_fail(review, args.fail_on) else 0


PR_RE = re.compile(r"^([\w.-]+/[\w.-]+)#(\d+)$")


def cmd_pr(args: argparse.Namespace, console: Console, llm: LLM, demo: bool) -> int:
    match = PR_RE.match(args.ref)
    if not match:
        raise SystemExit("Use the form owner/repo#123")
    repo, number = match.group(1), int(match.group(2))
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise SystemExit("Set GITHUB_TOKEN (a token with pull request read/write access).")
    gh = GitHubClient(token)
    pull = gh.get_pull(repo, number)
    diff_text = gh.get_pull_diff(repo, number)
    console.print(f"[bold]{repo}#{number}[/bold] {pull.get('title', '')}")
    reviewer = build_reviewer(llm, console, args.max_comments, _ignore_list(args.ignore))
    review = reviewer.review(diff_text, title=pull.get("title", ""), description=pull.get("body") or "")
    files, _ = filter_files(parse_unified_diff(diff_text), reviewer.ignore)
    print_review(console, review, files, demo=demo)
    if args.post:
        gh.post_review(repo, number, pull["head"]["sha"], review, demo=demo)
        console.print(f"[green]✓ Posted review to https://github.com/{repo}/pull/{number}[/green]")
    return 1 if should_fail(review, args.fail_on) else 0


def run_action(env: dict[str, str], llm: LLM, demo: bool, console: Console, gh: GitHubClient | None = None) -> int:
    """Entry point inside GitHub Actions. Reads the pull_request event and posts a review."""
    event_path = env.get("GITHUB_EVENT_PATH")
    if not event_path or not Path(event_path).exists():
        raise SystemExit("GITHUB_EVENT_PATH is missing. This command runs inside GitHub Actions.")
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    pull = event.get("pull_request")
    if not pull:
        console.print("Not a pull_request event; nothing to review.")
        return 0

    repo = env.get("GITHUB_REPOSITORY") or pull["base"]["repo"]["full_name"]
    number = int(pull["number"])
    gh = gh or GitHubClient(env.get("GITHUB_TOKEN", ""), api_url=env.get("GITHUB_API_URL", "https://api.github.com"))
    diff_text = gh.get_pull_diff(repo, number)

    max_comments = int(env.get("MAX_COMMENTS") or 10)
    reviewer = build_reviewer(llm, console, max_comments, _ignore_list(env.get("IGNORE_PATHS")))
    review = reviewer.review(diff_text, title=pull.get("title", ""), description=pull.get("body") or "")
    files, _ = filter_files(parse_unified_diff(diff_text), reviewer.ignore)
    print_review(console, review, files, demo=demo)

    gh.post_review(
        repo,
        number,
        pull["head"]["sha"],
        review,
        demo=demo,
        allow_request_changes=is_truthy(env.get("REQUEST_CHANGES")),
    )
    console.print(f"✓ Posted review to {repo}#{number}")

    summary_path = env.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(review_markdown(review, demo=demo) + "\n")
    return 1 if should_fail(review, (env.get("FAIL_ON") or "never").lower()) else 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="reviewer", description="AI pull request reviewer")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--max-comments", type=int, default=int(os.getenv("MAX_COMMENTS") or 10))
        p.add_argument("--fail-on", choices=FAIL_LEVELS, default=os.getenv("FAIL_ON", "never"))
        p.add_argument("--ignore", default=os.getenv("IGNORE_PATHS", ""), help="extra globs, comma separated")

    p_review = sub.add_parser("review", help="review a local diff")
    p_review.add_argument("diff", nargs="?", help="path to a .diff file, or - for stdin")
    p_review.add_argument("--git", metavar="BASE", help="review `git diff BASE...HEAD`")
    p_review.add_argument("--title", help="PR title to give the model")
    p_review.add_argument("--json", action="store_true", help="print the review as JSON")
    common(p_review)

    p_pr = sub.add_parser("pr", help="review a GitHub pull request")
    p_pr.add_argument("ref", help="owner/repo#123")
    p_pr.add_argument("--post", action="store_true", help="post the review to GitHub")
    common(p_pr)

    sub.add_parser("action", help="run inside GitHub Actions")
    args = parser.parse_args(argv)

    console = Console()
    llm, demo = make_llm()
    if demo and not getattr(args, "json", False):
        console.print(f"[yellow]⚠ {DEMO_NOTICE}[/yellow] (add GEMINI_API_KEY for real reviews)")

    if args.command == "review":
        return cmd_review(args, console, llm, demo)
    if args.command == "pr":
        return cmd_pr(args, console, llm, demo)
    return run_action(dict(os.environ), llm, demo, console)
