"""Turn a Review into GitHub markdown and into readable terminal output."""

from __future__ import annotations

from rich.console import Console, Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .demo import DEMO_NOTICE
from .diff import FileDiff
from .models import Review, ReviewComment

ICONS = {"critical": "🔴", "warning": "🟠", "suggestion": "🔵", "nit": "⚪"}
COLORS = {"critical": "red", "warning": "dark_orange", "suggestion": "dodger_blue1", "nit": "grey62"}
VERDICT_LABELS = {
    "request_changes": ("Changes requested", "red"),
    "comment": ("Comments", "dark_orange"),
    "approve": ("Looks good", "green"),
}
LEXERS = {"py": "python", "php": "php", "ts": "typescript", "tsx": "tsx", "js": "javascript", "go": "go"}
MARKER = "<!-- pr-review-agent -->"


# ---------- GitHub markdown ----------


def comment_markdown(c: ReviewComment) -> str:
    text = f"{ICONS[c.severity]} **{c.severity.title()} · {c.category}**: {c.title}\n\n{c.body}"
    if c.suggestion:
        text += f"\n\n```suggestion\n{c.suggestion}\n```"
    return text


def review_markdown(review: Review, demo: bool = False) -> str:
    label, _ = VERDICT_LABELS[review.verdict]
    counts = " · ".join(
        f"{ICONS[s]} {review.count(s)} {s}" for s in ("critical", "warning", "suggestion", "nit") if review.count(s)
    )
    lines = [MARKER, f"## 🤖 AI review: {label}", ""]
    if demo:
        lines += [f"> ⚠️ **{DEMO_NOTICE}.** Add a `GEMINI_API_KEY` secret for real reviews.", ""]
    lines += [review.summary, ""]
    if counts:
        lines += [f"**{len(review.comments)} comment(s):** {counts}", ""]
    lines.append(f"<sub>Model: `{review.model}` · Files reviewed: {len(review.files_reviewed)}")
    if review.files_skipped:
        lines[-1] += f" · Skipped: {', '.join(f'`{p}`' for p in review.files_skipped)}"
    lines[-1] += " · Comments are suggestions; a human still approves.</sub>"
    return "\n".join(lines)


def comments_as_markdown_list(review: Review) -> str:
    """Fallback when GitHub rejects inline comments: list them in the body instead."""
    return "\n\n".join(f"**`{c.path}:{c.line}`** {comment_markdown(c)}" for c in review.comments)


# ---------- Terminal ----------


def _code_context(c: ReviewComment, files: dict[str, FileDiff]) -> Syntax | None:
    f = files.get(c.path)
    if not f:
        return None
    start, end = max(1, c.line - 1), c.line + 1
    lines = [(n, f.line_text(n)) for n in range(start, end + 1)]
    lines = [(n, t) for n, t in lines if t is not None]
    if not lines:
        return None
    lexer = LEXERS.get(c.path.rsplit(".", 1)[-1], "text")
    return Syntax(
        "\n".join(t for _, t in lines),
        lexer,
        line_numbers=True,
        start_line=lines[0][0],
        highlight_lines={c.line},
        theme="monokai",
        background_color="default",
        word_wrap=True,
    )


def print_review(console: Console, review: Review, files: list[FileDiff], demo: bool = False) -> None:
    by_path = {f.path: f for f in files}
    label, color = VERDICT_LABELS[review.verdict]

    header = Text()
    header.append(f" {label.upper()} ", style=f"bold white on {color}")
    header.append(f"  {len(review.comments)} comment(s) on {len(review.files_reviewed)} file(s)", style="bold")
    header.append(f"  ·  model {review.model}", style="dim")
    console.print(Panel(Group(header, Text(""), Text(review.summary)), title="🤖 AI review", border_style=color))

    for c in review.comments:
        title = Text()
        title.append(f"{ICONS[c.severity]} {c.severity.upper()}", style=f"bold {COLORS[c.severity]}")
        title.append(f" · {c.category} · ", style="dim")
        title.append(f"{c.path}:{c.line}", style="cyan")
        parts: list = [Text(c.title, style="bold"), Text(c.body)]
        code = _code_context(c, by_path)
        if code:
            parts.insert(1, code)
        if c.suggestion:
            parts.append(Text("suggested change:", style="green"))
            parts.append(Text("+ " + c.suggestion.strip(), style="green"))
        console.print(Panel(Group(*parts), title=title, title_align="left", border_style=COLORS[c.severity]))

    table = Table(show_header=False, box=None, padding=(0, 2))
    for sev in ("critical", "warning", "suggestion", "nit"):
        if review.count(sev):
            table.add_row(f"{ICONS[sev]} {sev}", str(review.count(sev)))
    if review.dropped:
        table.add_row("guard: dropped, outside diff", str(review.dropped), style="dim")
    if review.files_skipped:
        table.add_row("skipped files", ", ".join(review.files_skipped), style="dim")
    if table.row_count:
        console.print(table)
    if demo:
        console.print(f"[yellow]⚠ {DEMO_NOTICE}[/yellow]")
