"""Diff-scoped check of the CONSTRAINTS.md floor: flags changes that lower the bar.

    python scripts/floor_guard.py                 # against origin/main
    python scripts/floor_guard.py --base main

Looks at everything since the merge base: committed, staged, unstaged and
untracked. Flags new suppression comments, stubs, skipped or deleted tests,
removed assertions, new Exceptions rows and lowered thresholds in
CONSTRAINTS.md. Tightening is silent; loosening is loud.

Exit codes: 0 clean, 1 at least one violation (block), 2 the guard couldn't
run (not a git repo, no merge base). Paths in .constraintsignore (one glob per
line, each citing an Exceptions ID from CONSTRAINTS.md) are skipped. A skip
is allowed when a commit since the merge base says why, with a trailer line
`Skip-reason: <test file> <reason>`; a new Exceptions row is allowed once a
human approves it in a commit message, `Exception-approved: E1, E2`.

Python port of the agent-skills constraint-driven-development floor-guard
reference; keep its contract if you change the patterns.
"""

import argparse
import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSTRAINTS = "CONSTRAINTS.md"
IGNORE_FILE = ".constraintsignore"

SUPPRESSIONS = re.compile(
    r"#\s*noqa|#\s*type:\s*ignore|#\s*pragma:\s*no\s*(cover|mutate)|#\s*nosec|pylint:\s*disable|nosemgrep|gitleaks:allow"
)
STUBS = re.compile(r"raise NotImplementedError|\bTODO\b|\bFIXME\b")
APPROVAL = re.compile(r"^Exception-approved:(.*)$", re.M)
SKIP_REASON = re.compile(r"^Skip-reason:\s*(\S+)", re.M)
SKIPS = re.compile(r"@pytest\.mark\.(skip|skipif|xfail)\b|pytest\.skip\(|importorskip\(|unittest\.skip")
ASSERTION = re.compile(r"\bassert\b|pytest\.raises\(")
EXCEPTION_ROW = re.compile(r"^\|\s*E\d+\s*\|")
NUMBER = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Line:
    """One added or removed line of the diff."""

    file: str
    line: int
    text: str


@dataclass(frozen=True)
class Finding:
    """A floor violation: which rule, where, and a short excerpt."""

    rule: str
    file: str
    line: int
    text: str


def find_violations(
    added: list[Line],
    removed: list[Line],
    deleted: list[str],
    ignore: list[str] | None = None,
    skip_reasons: set[str] | None = None,
    approved_exceptions: set[str] | None = None,
) -> list[Finding]:
    """Return every move in the diff that lowers the bar.

    Args:
        added: lines added since the merge base (untracked files count as added).
        removed: lines removed since the merge base.
        deleted: paths of files deleted since the merge base.
        ignore: path globs to skip (from .constraintsignore).
        skip_reasons: test files whose added skips a commit message explains.
        approved_exceptions: Exceptions IDs a commit message approves.

    Suppression, stub and skip patterns are code patterns, so they only apply
    to Python files; CONSTRAINTS.md itself names them.
    """
    ignore = ignore or []
    skip_reasons = skip_reasons or set()
    approved_exceptions = approved_exceptions or set()

    def skip(path: str) -> bool:
        return any(fnmatch.fnmatch(path, g) for g in ignore)

    added = [a for a in added if not skip(a.file)]
    removed = [r for r in removed if not skip(r.file)]
    findings: list[Finding] = []

    def flag(rule: str, at: Line) -> None:
        findings.append(Finding(rule, at.file, at.line, at.text.strip()[:120]))

    for i, a in enumerate(added):
        if a.file.endswith(".py"):
            if SUPPRESSIONS.search(a.text):
                flag("silenced-checker", a)
            if STUBS.search(a.text) or _empty_except(a, added[i + 1] if i + 1 < len(added) else None):
                flag("unfinished-work", a)
            if _is_test(a.file) and SKIPS.search(a.text) and a.file not in skip_reasons:
                flag("test-made-easier", a)
        if a.file == CONSTRAINTS and EXCEPTION_ROW.search(a.text) and _row_key(a.text) not in approved_exceptions:
            flag("new-exception", a)

    for path in sorted({r.file for r in removed if _is_test(r.file)}):
        lost = [r for r in removed if r.file == path and ASSERTION.search(r.text)]
        gained = [a for a in added if a.file == path and ASSERTION.search(a.text)]
        if len(lost) > len(gained):
            flag("assertion-removed", lost[-1])

    findings += [Finding("test-deleted", path, 0, "") for path in deleted if _is_test(path) and not skip(path)]
    findings += _lowered_thresholds(added, removed)
    return findings


def _is_test(path: str) -> bool:
    return Path(path).name.startswith("test_") and path.endswith(".py")


def _empty_except(line: Line, following: Line | None) -> bool:
    return (
        re.match(r"\s*except\b.*:\s*$", line.text) is not None
        and following is not None
        and following.file == line.file
        and following.line == line.line + 1
        and following.text.strip() == "pass"
    )


def _lowered_thresholds(added: list[Line], removed: list[Line]) -> list[Finding]:
    """A CONSTRAINTS.md line whose numbers went down, or a floor bullet that disappeared."""
    after = [a for a in added if a.file == CONSTRAINTS]
    findings = []
    for r in (r for r in removed if r.file == CONSTRAINTS):
        key = _row_key(r.text)
        match = next((a for a in after if _row_key(a.text) == key), None)
        if match is None:
            if r.text.lstrip().startswith("- "):
                findings.append(Finding("threshold-lowered", r.file, r.line, f"removed: {r.text.strip()[:110]}"))
            continue
        old, new = NUMBER.findall(r.text), NUMBER.findall(match.text)
        if any(float(n) < float(o) for o, n in zip(old, new)):
            findings.append(
                Finding("threshold-lowered", r.file, r.line, f"{r.text.strip()[:55]} -> {match.text.strip()[:55]}")
            )
    return findings


def _row_key(text: str) -> str:
    cells = [c.strip() for c in text.strip().strip("|").split("|")]
    return cells[0] if cells else ""


def parse_diff(diff: str) -> tuple[list[Line], list[Line]]:
    """Split a unified diff into added and removed lines, each with its file and line number."""
    added: list[Line] = []
    removed: list[Line] = []
    file = ""
    old_no = new_no = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            file = raw[6:] if raw.startswith("+++ b/") else raw[4:]
        elif raw.startswith("--- "):
            continue
        elif raw.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)", raw)
            old_no, new_no = int(m.group(1)), int(m.group(2))
        elif raw.startswith("+"):
            added.append(Line(file, new_no, raw[1:]))
            new_no += 1
        elif raw.startswith("-"):
            removed.append(Line(file, old_no, raw[1:]))
            old_no += 1
        elif raw.startswith(" "):
            old_no += 1
            new_no += 1
    return added, removed


def skip_reasons_from(messages: str) -> set[str]:
    """Test files named by `Skip-reason:` trailers in commit messages."""
    return set(SKIP_REASON.findall(messages))


def approvals_from(messages: str) -> set[str]:
    """Exceptions IDs named by `Exception-approved:` trailers in commit messages."""
    return {i for line in APPROVAL.findall(messages) for i in re.findall(r"\bE\d+\b", line)}


def _git(*args: str) -> str | None:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def collect(base: str) -> tuple[list[Line], list[Line], list[str]] | None:
    """Added lines, removed lines and deleted files since the merge base with `base`; None if git can't say."""
    merge_base = _git("merge-base", base, "HEAD")
    if not merge_base:
        return None
    merge_base = merge_base.strip()
    added, removed = parse_diff(_git("diff", "--unified=0", merge_base, "--") or "")
    for path in filter(None, (_git("ls-files", "--others", "--exclude-standard") or "").splitlines()):
        try:
            text = (ROOT / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        added += [Line(path, i, t) for i, t in enumerate(text.splitlines(), 1)]
    deleted = (_git("diff", "--name-only", "--diff-filter=D", merge_base, "--") or "").split()
    return added, removed, deleted


def load_ignore(root: Path = ROOT) -> list[str]:
    """Globs from .constraintsignore; text after '#' is the Exceptions ID it cites."""
    path = root / IGNORE_FILE
    if not path.is_file():
        return []
    return [g for g in (line.split("#", 1)[0].strip() for line in path.read_text().splitlines()) if g]


def main(argv: list[str] | None = None) -> int:
    """Print findings and return the exit code (0 clean, 1 violations, 2 couldn't run)."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", default="origin/main", help="branch the change will merge into")
    args = p.parse_args(argv)
    diff = collect(args.base)
    if diff is None:
        print(f"floor-guard: no merge base against {args.base}", file=sys.stderr)
        return 2
    messages = _git("log", "--format=%B", f"{args.base}..HEAD") or ""
    findings = find_violations(
        *diff,
        ignore=load_ignore(),
        skip_reasons=skip_reasons_from(messages),
        approved_exceptions=approvals_from(messages),
    )
    if not findings:
        print("floor-guard: clean")
        return 0
    print(f"floor-guard: {len(findings)} floor violation(s):", file=sys.stderr)
    for f in findings:
        print(f"  [{f.rule}] {f.file}:{f.line}: {f.text}", file=sys.stderr)
    print("\nEach lowers the bar. Fix the code, or route it through an Exceptions row in CONSTRAINTS.md.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # a crash must read as "couldn't run" (2), never as violations (1) or clean (0)
        print(f"floor-guard: couldn't run: {e!r}", file=sys.stderr)
        sys.exit(2)
