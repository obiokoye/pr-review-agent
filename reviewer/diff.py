"""Parse unified diffs (the format `git diff` and GitHub produce).

The reviewer needs two things from a diff:
1. A readable version for the model, with the *new* line number on every
   line, so the model can say "line 20" and mean it.
2. The set of lines GitHub will accept an inline comment on. Anything the
   model says about a line outside that set is dropped (hallucination guard).
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")

# Files a reviewer should never spend tokens on.
DEFAULT_IGNORE = [
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "composer.lock",
    "poetry.lock",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.svg",
    "dist/*",
    "vendor/*",
    "node_modules/*",
]


@dataclass
class DiffLine:
    kind: str  # "+" added, "-" removed, " " context
    content: str
    new_lineno: int | None
    old_lineno: int | None


@dataclass
class Hunk:
    header: str
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class FileDiff:
    path: str
    old_path: str
    hunks: list[Hunk] = field(default_factory=list)
    is_binary: bool = False
    is_deleted: bool = False
    is_new: bool = False

    @property
    def added_lines(self) -> set[int]:
        return {ln.new_lineno for h in self.hunks for ln in h.lines if ln.kind == "+" and ln.new_lineno}

    @property
    def commentable_lines(self) -> set[int]:
        """Lines on the new side of the diff (added or context): GitHub accepts comments here."""
        return {ln.new_lineno for h in self.hunks for ln in h.lines if ln.kind != "-" and ln.new_lineno}

    @property
    def additions(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.kind == "+")

    @property
    def deletions(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.kind == "-")

    def line_text(self, lineno: int) -> str | None:
        for h in self.hunks:
            for ln in h.lines:
                if ln.kind != "-" and ln.new_lineno == lineno:
                    return ln.content
        return None

    def render(self) -> str:
        """Diff text annotated with new-side line numbers, for the model."""
        out = [f"### File: {self.path}" + (" (new file)" if self.is_new else "")]
        for h in self.hunks:
            out.append(h.header)
            for ln in h.lines:
                num = f"{ln.new_lineno:>5}" if ln.kind != "-" else "     "
                out.append(f"{num} {ln.kind} {ln.content}")
        return "\n".join(out)


def parse_unified_diff(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    old_no = new_no = 0

    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            parts = raw.split(" b/", 1)
            path = parts[1] if len(parts) == 2 else raw.split()[-1]
            current = FileDiff(path=path, old_path=path)
            files.append(current)
            hunk = None
            continue
        if current is None:
            continue
        if hunk is None or not raw or raw[0] not in "+- \\":
            # Header area of a file (or a new hunk header).
            if raw.startswith("new file mode"):
                current.is_new = True
            elif raw.startswith("deleted file mode"):
                current.is_deleted = True
            elif raw.startswith("Binary files") or raw.startswith("GIT binary patch"):
                current.is_binary = True
            elif raw.startswith("--- "):
                current.old_path = raw[4:].removeprefix("a/")
                continue
            elif raw.startswith("+++ "):
                new_path = raw[4:]
                if new_path != "/dev/null":
                    current.path = new_path.removeprefix("b/")
                continue
        match = HUNK_RE.match(raw)
        if match:
            old_no, new_no = int(match.group(1)), int(match.group(3))
            hunk = Hunk(header=raw)
            current.hunks.append(hunk)
            continue
        if hunk is None or not raw:
            if hunk is not None and not raw:  # some tools strip the space on blank context lines
                hunk.lines.append(DiffLine(" ", "", new_no, old_no))
                old_no += 1
                new_no += 1
            continue
        kind, content = raw[0], raw[1:]
        if kind == "+":
            hunk.lines.append(DiffLine("+", content, new_no, None))
            new_no += 1
        elif kind == "-":
            hunk.lines.append(DiffLine("-", content, None, old_no))
            old_no += 1
        elif kind == " ":
            hunk.lines.append(DiffLine(" ", content, new_no, old_no))
            old_no += 1
            new_no += 1
        # "\ No newline at end of file" is ignored
    return files


def is_ignored(path: str, patterns: list[str]) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(name, p) for p in patterns)


def filter_files(files: list[FileDiff], ignore: list[str] | None = None) -> tuple[list[FileDiff], list[str]]:
    """Return (files worth reviewing, paths skipped)."""
    patterns = DEFAULT_IGNORE + (ignore or [])
    keep, skipped = [], []
    for f in files:
        if f.is_binary or f.is_deleted or not f.added_lines or is_ignored(f.path, patterns):
            skipped.append(f.path)
        else:
            keep.append(f)
    return keep, skipped


def chunk_files(files: list[FileDiff], max_chars: int) -> list[list[FileDiff]]:
    """Group files so each model call stays under a character budget.

    A single file bigger than the budget gets a chunk of its own; the prompt
    builder truncates it.
    """
    chunks: list[list[FileDiff]] = []
    current: list[FileDiff] = []
    size = 0
    for f in files:
        n = len(f.render())
        if current and size + n > max_chars:
            chunks.append(current)
            current, size = [], 0
        current.append(f)
        size += n
    if current:
        chunks.append(current)
    return chunks
