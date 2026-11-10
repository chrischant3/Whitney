"""Diff-scoped check of the CONSTRAINTS.md floor: flags changes that lower the bar.

    python scripts/floor_guard.py                 # against origin/main
    python scripts/floor_guard.py --base main
    python -I base/scripts/floor_guard.py --repo pr --base <sha>   # CI: main's copy, the PR's files

Looks at everything since the merge base: committed, staged, unstaged and
untracked. Flags new suppression comments, stubs, skipped or deleted tests,
removed assertions, new Exceptions rows and lowered thresholds in
CONSTRAINTS.md, lowered eval-gate thresholds, new `known_gap` scenarios, and
new entries in .constraintsignore or .gitleaksignore. Tightening is silent;
loosening is loud.

The eval-gate thresholds are also read by parsing the watched files at the
merge base and now (eval_threshold_findings), because a line-by-line diff
can't see a second `MIN_HELD = 0.1` lower in thresholds.py, an indented
decoy, a value written as an expression, or a `--min-*` default split over
several lines.

Exit codes: 0 clean, 1 at least one violation (block), 2 the guard couldn't
run (not a git repo, no merge base). Paths in .constraintsignore (one glob per
line, each citing an Exceptions ID from CONSTRAINTS.md) are exempt from the
pattern rules (suppressions, stubs, skips), because such files contain the
strings the patterns look for; a test there still can't lose assertions or be
deleted. A skip is allowed when a commit since the merge base says why, with
a trailer line `Skip-reason: <test file> <reason>`; a new Exceptions row, a
new ignore-file entry or a new `known_gap` citing that ID, is allowed once a
human approves it in a commit message, `Exception-approved: E1, E2` (CI also
requires that commit to be signed by an approver: scripts/check_approvals.py). Until
then a new .constraintsignore glob doesn't take effect, so it can't hide what
it covers.

Python port of the agent-skills constraint-driven-development floor-guard
reference; keep its contract if you change the patterns.
"""

import argparse
import ast
import fnmatch
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSTRAINTS = "CONSTRAINTS.md"
IGNORE_FILE = ".constraintsignore"
GITLEAKS_IGNORE_FILE = ".gitleaksignore"
EXCEPTION_ID = re.compile(r"\bE\d+\b")

SUPPRESSIONS = re.compile(
    r"#\s*noqa|#\s*type:\s*ignore|#\s*pragma:\s*no\s*(cover|mutate)|#\s*nosec|pylint:\s*disable|nosemgrep|gitleaks:allow"
)
GITLEAKS_ALLOW = re.compile(r"gitleaks:allow")  # gitleaks honours it in every file type, not just Python
STUBS = re.compile(r"raise NotImplementedError|\bTODO\b|\bFIXME\b")
APPROVAL = re.compile(r"^Exception-approved:(.*)$", re.M)
SKIP_REASON = re.compile(r"^Skip-reason:\s*(\S+)", re.M)
SKIPS = re.compile(r"@pytest\.mark\.(skip|skipif|xfail)\b|pytest\.skip\(|importorskip\(|unittest\.skip")
ASSERTION = re.compile(r"\bassert\b|pytest\.raises\(")
EXCEPTION_ROW = re.compile(r"^\|\s*E\d+\s*\|")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
# A scenario marked known_gap is left out of the eval gate's thresholds (src/whitney/report.py).
KNOWN_GAP = re.compile(r'"known_gap"\s*:')
# The eval gate's thresholds outside CONSTRAINTS.md; lowering one passes runs the gate should fail.
# The MIN_* constants live in thresholds.py; report.py held them first and stays watched.
# cli.py holds whitney run's --min-* options, which gate a user's own evals.
THRESHOLDS_FILE = "src/whitney/thresholds.py"
EVAL_THRESHOLD_FILES = {"scripts/run_evals.py", "src/whitney/report.py", THRESHOLDS_FILE, "src/whitney/cli.py"}
EVAL_THRESHOLD = re.compile(
    r"^\s*(?P<constant>MIN_[A-Z_]+)\s*=\s*(?P<value>\d+(?:\.\d+)?)\s*(?:#.*)?$"
    r'|"(?P<option>--min-[a-z-]+)".*\bdefault=(?P<default>\d+(?:\.\d+)?)'
)
# A --min-* option with no default, default=None or default=MIN_*: it replaces nothing, or reads a
# guarded constant, so it doesn't remove a threshold. Any other default (an expression) doesn't match.
OPTION_WITHOUT_NUMBER = re.compile(r'add_argument\(\s*"(?P<option>--min-[a-z-]+)"(?!.*\bdefault=(?!None\b|MIN_[A-Z_]+\b))')
THRESHOLD_NAME = re.compile(r"MIN_[A-Z_]+")
OPTION_NAME = re.compile(r"--min-[a-z-]+")
# The modules a watched file may import a MIN_* name from, under its own name.
THRESHOLD_SOURCES = {"whitney.thresholds", "whitney.report"}


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
    gitleaksignore_text: str | None = None,
) -> list[Finding]:
    """Return every move in the diff that lowers the bar.

    Args:
        added: lines added since the merge base (untracked files count as added).
        removed: lines removed since the merge base.
        deleted: paths of files deleted since the merge base.
        ignore: path globs to skip (from .constraintsignore in the working tree).
        skip_reasons: test files whose added skips a commit message explains.
        approved_exceptions: Exceptions IDs a commit message approves.
        gitleaksignore_text: the current .gitleaksignore, where an entry cites
            its Exceptions ID in the comment line above it.

    Suppression, stub and skip patterns are code patterns, so they only apply
    to Python files, except `gitleaks:allow`, which gitleaks honours anywhere;
    CONSTRAINTS.md itself names them. `ignore` exempts paths from these
    pattern rules only. A new ignore-file entry or known_gap whose cited ID
    isn't approved is flagged, and a new glob is dropped from `ignore`, so the
    change that adds it can't use it.
    """
    ignore = ignore or []
    skip_reasons = skip_reasons or set()
    approved_exceptions = approved_exceptions or set()
    findings: list[Finding] = []

    def flag(rule: str, at: Line) -> None:
        findings.append(Finding(rule, at.file, at.line, at.text.strip()[:120]))

    # Checked before any glob applies, so no glob can exempt an ignore file.
    unapproved = [
        a
        for a in added
        if a.file in (IGNORE_FILE, GITLEAKS_IGNORE_FILE)
        and _is_entry(a.text)
        and _cited_exception(a, gitleaksignore_text) not in approved_exceptions
    ]
    for a in unapproved:
        flag("new-exception", a)
    unapproved_globs = {_glob(a.text) for a in unapproved if a.file == IGNORE_FILE}
    ignore = [g for g in ignore if g not in unapproved_globs]

    def skip(path: str) -> bool:
        return any(fnmatch.fnmatch(path, g) for g in ignore)

    checked = [a for a in added if not skip(a.file)]
    for i, a in enumerate(checked):
        if a.file.endswith(".py"):
            if SUPPRESSIONS.search(a.text):
                flag("silenced-checker", a)
            if STUBS.search(a.text) or _empty_except(a, checked[i + 1] if i + 1 < len(checked) else None):
                flag("unfinished-work", a)
            if _is_test(a.file) and SKIPS.search(a.text) and a.file not in skip_reasons:
                flag("test-made-easier", a)
        elif a.file != CONSTRAINTS and GITLEAKS_ALLOW.search(a.text):
            flag("silenced-checker", a)
        if a.file == CONSTRAINTS and EXCEPTION_ROW.search(a.text) and _row_key(a.text) not in approved_exceptions:
            flag("new-exception", a)
        if a.file.startswith("evals/") and KNOWN_GAP.search(a.text) and _first_id(a.text) not in approved_exceptions:
            flag("new-exception", a)

    for path in sorted({r.file for r in removed if _is_test(r.file)}):
        lost = [r for r in removed if r.file == path and ASSERTION.search(r.text)]
        gained = [a for a in added if a.file == path and ASSERTION.search(a.text)]
        if len(lost) > len(gained):
            flag("assertion-removed", lost[-1])

    findings += [Finding("test-deleted", path, 0, "") for path in deleted if _is_test(path)]
    findings += _lowered_thresholds(added, removed)
    findings += _lowered_eval_thresholds(added, removed)
    return findings


def _first_id(text: str) -> str | None:
    found = EXCEPTION_ID.search(text)
    return found.group(0) if found else None


def _eval_threshold(line: Line) -> tuple[str, float | None] | None:
    """(name, value) if `line` sets an eval-gate threshold: a MIN_* constant or a --min-* option default.

    The value is None for a --min-* option whose default isn't written as a
    number (left out, None or a MIN_* constant).
    """
    if line.file not in EVAL_THRESHOLD_FILES:
        return None
    found = EVAL_THRESHOLD.search(line.text)
    if found:
        return found["constant"] or found["option"], float(found["value"] or found["default"])
    found = OPTION_WITHOUT_NUMBER.search(line.text)
    return (found["option"], None) if found else None


def _lowered_eval_thresholds(added: list[Line], removed: list[Line]) -> list[Finding]:
    """An eval-gate threshold whose value went down, or whose line went with no replacement.

    An option whose new default isn't a number replaces nothing, so it isn't
    a removal; eval_threshold_findings checks what such a default reads.
    """
    after = dict(filter(None, map(_eval_threshold, added)))
    findings = []
    for r in removed:
        threshold = _eval_threshold(r)
        if threshold is None or threshold[1] is None:
            continue
        name, old = threshold
        if name not in after:
            findings.append(Finding("threshold-lowered", r.file, r.line, f"{name}: removed"))
        elif after[name] is not None and after[name] < old:
            findings.append(Finding("threshold-lowered", r.file, r.line, f"{name}: {old:g} -> {after[name]:g}"))
    return findings


@dataclass
class _Thresholds:
    """What parsing the watched files at one revision shows about the eval gate's thresholds.

    constants: each MIN_* in thresholds.py set by one plain top-level line, with its value and line.
    bound: every MIN_* name thresholds.py binds, however.
    options: (file, --min-* option) -> its default (None: the check's own), whether that default
        is written as a number rather than read from a MIN_* constant, and its line; the lowest
        number wins when a file adds the option more than once.
    problems: (file, text) -> line, for what the guard can't read as a plain threshold.
    """

    constants: dict[str, tuple[float, int]] = field(default_factory=dict)
    bound: set[str] = field(default_factory=set)
    options: dict[tuple[str, str], tuple[float | None, bool, int]] = field(default_factory=dict)
    problems: dict[tuple[str, str], int] = field(default_factory=dict)


def _number(node: ast.expr | None) -> float | None:
    """The value of a plain int or float literal (not a bool), else None."""
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return float(node.value)
    return None


def _empty_list(node: ast.expr) -> bool:
    """`[]` or `()`: the default of an option that collects values, like --min-rate, which then replaces nothing."""
    return isinstance(node, (ast.List, ast.Tuple)) and not node.elts


def _threshold_bindings(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    """(name, node) for every place `tree` binds a MIN_* name, at any depth, by any statement."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            found.append((node.id, node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.append((node.name, node))
        elif isinstance(node, ast.arg):
            found.append((node.arg, node))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            found += [((alias.asname or alias.name).split(".")[0], node) for alias in node.names]
    return [(name, node) for name, node in found if THRESHOLD_NAME.fullmatch(name)]


def _plain_constant(node: ast.AST, tree: ast.Module) -> float | None:
    """The value if `node` is the target of a top-level `MIN_NAME = number` statement, else None."""
    for statement in tree.body:
        if (
            isinstance(statement, ast.Assign)
            and statement.targets == [node]
            and isinstance(node, ast.Name)
        ):
            return _number(statement.value)
    return None


def _read_thresholds(path: str, tree: ast.Module, facts: _Thresholds) -> None:
    """Add thresholds.py's MIN_* constants, and anything unclear about them, to `facts`."""
    by_name: dict[str, list[ast.AST]] = {}
    for name, node in _threshold_bindings(tree):
        by_name.setdefault(name, []).append(node)
    for name, nodes in by_name.items():
        facts.bound.add(name)
        if len(nodes) > 1:
            facts.problems[(path, f"{name} is bound {len(nodes)} times")] = max(n.lineno for n in nodes)
            continue
        value = _plain_constant(nodes[0], tree)
        if value is None:
            facts.problems[(path, f"{name} is not a plain `{name} = number` line at the top level")] = nodes[0].lineno
        else:
            facts.constants[name] = (value, nodes[0].lineno)


def _read_other(path: str, tree: ast.Module, facts: _Thresholds) -> None:
    """Add a watched file's MIN_* bindings that aren't plain imports, and its --min-* defaults, to `facts`."""
    for name, node in _threshold_bindings(tree):
        imported = isinstance(node, ast.ImportFrom) and node.module in THRESHOLD_SOURCES and any(
            alias.name == name and alias.asname in (None, name) for alias in node.names
        )
        if not imported:
            facts.problems[(path, f"{name} is bound outside {THRESHOLDS_FILE}")] = node.lineno
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument"):
            continue
        if not (node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            continue
        option = node.args[0].value
        if not OPTION_NAME.fullmatch(option):
            continue
        default = next((k.value for k in node.keywords if k.arg == "default"), None)
        value: float | None
        written = False
        if default is None or (isinstance(default, ast.Constant) and default.value is None) or _empty_list(default):
            value = None
        elif (number := _number(default)) is not None:
            value, written = number, True
        elif isinstance(default, ast.Name) and default.id in facts.constants:
            value = facts.constants[default.id][0]
        else:
            facts.problems[(path, f"{option}: the default is not None, a number or a MIN_* constant")] = node.lineno
            continue
        earlier = facts.options.get((path, option))
        if earlier is None or (value is not None and (earlier[0] is None or value < earlier[0])):
            facts.options[(path, option)] = (value, written, node.lineno)


def _parse_thresholds(sources: Mapping[str, str | None]) -> _Thresholds:
    """Parse the watched files at one revision; thresholds.py first, so option defaults can read its constants."""
    facts = _Thresholds()
    for path in sorted(EVAL_THRESHOLD_FILES, key=lambda p: p != THRESHOLDS_FILE):
        text = sources.get(path)
        if text is None:
            continue
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            facts.problems[(path, "the file doesn't parse as Python")] = 0
            continue
        (_read_thresholds if path == THRESHOLDS_FILE else _read_other)(path, tree, facts)
    return facts


def eval_threshold_findings(before: Mapping[str, str | None], after: Mapping[str, str | None]) -> list[Finding]:
    """Eval-gate thresholds that parsing the watched files shows fell, went or became unreadable.

    Args:
        before: each watched file's text at the merge base (None: it didn't exist).
        after: each watched file's text now, including uncommitted changes.

    Flags a thresholds.py constant that falls or goes; one bound more than
    once, at any depth, or set by anything but a plain top-level
    `MIN_NAME = number` line; a MIN_* name bound in another watched file
    except by importing it under its own name; and a --min-* default that
    falls, is written as a number where it was the check's own, or is an
    expression. A problem already at the merge base isn't flagged again.
    """
    old, new = _parse_thresholds(before), _parse_thresholds(after)
    findings = [Finding("threshold-unclear", path, line, text) for (path, text), line in new.problems.items() if (path, text) not in old.problems]
    for name, (value, line) in old.constants.items():
        if name in new.constants and new.constants[name][0] < value:
            findings.append(Finding("threshold-lowered", THRESHOLDS_FILE, new.constants[name][1], f"{name}: {value:g} -> {new.constants[name][0]:g}"))
        elif name not in new.bound:
            findings.append(Finding("threshold-lowered", THRESHOLDS_FILE, line, f"{name}: removed"))
    for (path, option), (value, written, line) in new.options.items():
        if value is None:
            continue
        was = old.options.get((path, option), (None, False, 0))[0]
        if was is None and written:
            findings.append(Finding("threshold-lowered", path, line, f"{option}: default written as {value:g}; use None or a MIN_* constant"))
        elif was is not None and value < was:
            findings.append(Finding("threshold-lowered", path, line, f"{option}: {was:g} -> {value:g}"))
    return findings


def _is_entry(text: str) -> bool:
    """An ignore-file line that ignores something: not blank, not a comment."""
    stripped = text.strip()
    return bool(stripped) and not stripped.startswith("#")


def _glob(text: str) -> str:
    """The glob in a .constraintsignore line; text after '#' is the Exceptions ID it cites."""
    return text.split("#", 1)[0].strip()


def _cited_exception(entry: Line, gitleaksignore_text: str | None) -> str | None:
    """The Exceptions ID an ignore-file entry cites, or None.

    A .constraintsignore entry cites it inline after '#'. gitleaks reads no
    inline comments, so a .gitleaksignore entry cites the ID in the nearest
    comment line above it.
    """
    if entry.file == IGNORE_FILE:
        comments = [entry.text.partition("#")[2]]
    else:
        above = (gitleaksignore_text or "").splitlines()[: entry.line - 1]
        comments = [line for line in reversed(above) if line.lstrip().startswith("#")][:1]
    ids = [found for comment in comments for found in EXCEPTION_ID.findall(comment)]
    return ids[0] if ids else None


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
    """A CONSTRAINTS.md line whose numbers went down, or a floor bullet that disappeared.

    A removed line is matched to every added line with the same key (_row_key); if any of them
    has a lower number in the same place, it's flagged, so a raised copy can't hide a lowered one.
    """
    after = [a for a in added if a.file == CONSTRAINTS]
    findings = []
    for r in (r for r in removed if r.file == CONSTRAINTS):
        key = _row_key(r.text)
        matches = [a for a in after if _row_key(a.text) == key]
        if not matches:
            if r.text.lstrip().startswith("- "):
                findings.append(Finding("threshold-lowered", r.file, r.line, f"removed: {r.text.strip()[:110]}"))
            continue
        old = NUMBER.findall(r.text)
        lower = next((m for m in matches if any(float(n) < float(o) for o, n in zip(old, NUMBER.findall(m.text)))), None)
        if lower is not None:
            findings.append(
                Finding("threshold-lowered", r.file, r.line, f"{r.text.strip()[:55]} -> {lower.text.strip()[:55]}")
            )
    return findings


# A bullet's bold label, "- **60% mutation score on touched functions**: ...".
BULLET_LABEL = re.compile(r"^\s*- \*\*(?P<label>.+?)\*\*")


def _row_key(text: str) -> str:
    """What stays the same when a CONSTRAINTS.md line's numbers change.

    A table row: its first cell. A bullet with a bold label: the label with each number replaced
    by `#`, so its numbers keep their places and moving one out of the label changes the key. Any
    other line: the line itself, so editing it reads as removing it.
    """
    bullet = BULLET_LABEL.match(text)
    if bullet:
        return f"- **{NUMBER.sub('#', bullet['label'])}**"
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


def _git(root: Path, *args: str) -> str | None:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def collect(base: str, root: Path = ROOT) -> tuple[list[Line], list[Line], list[str]] | None:
    """Added lines, removed lines and deleted files in `root` since the merge base with `base`; None if git can't say."""
    merge_base = _git(root, "merge-base", base, "HEAD")
    if not merge_base:
        return None
    merge_base = merge_base.strip()
    added, removed = parse_diff(_git(root, "diff", "--unified=0", merge_base, "--") or "")
    for path in filter(None, (_git(root, "ls-files", "--others", "--exclude-standard") or "").splitlines()):
        try:
            text = (root / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        added += [Line(path, i, t) for i, t in enumerate(text.splitlines(), 1)]
    deleted = (_git(root, "diff", "--name-only", "--diff-filter=D", merge_base, "--") or "").split()
    return added, removed, deleted


def watched_sources(base: str, root: Path = ROOT) -> tuple[dict[str, str | None], dict[str, str | None]] | None:
    """Each eval-threshold file's text at the merge base with `base` and in the working tree; None if git can't say."""
    merge_base = _git(root, "merge-base", base, "HEAD")
    if not merge_base:
        return None
    before = {path: _git(root, "show", f"{merge_base.strip()}:{path}") for path in EVAL_THRESHOLD_FILES}
    after = {path: (root / path).read_text(encoding="utf-8") if (root / path).is_file() else None for path in EVAL_THRESHOLD_FILES}
    return before, after


def _unique(findings: list[Finding]) -> list[Finding]:
    """`findings` without repeats of the same rule, file and text (both layers can flag one lowering)."""
    seen: set[tuple[str, str, str]] = set()
    kept = []
    for f in findings:
        if (f.rule, f.file, f.text) not in seen:
            seen.add((f.rule, f.file, f.text))
            kept.append(f)
    return kept


def load_ignore(root: Path = ROOT) -> list[str]:
    """Globs from .constraintsignore; text after '#' is the Exceptions ID it cites."""
    path = root / IGNORE_FILE
    if not path.is_file():
        return []
    return [g for g in (_glob(line) for line in path.read_text().splitlines()) if g]


def main(argv: list[str] | None = None) -> int:
    """Print findings and return the exit code (0 clean, 1 violations, 2 couldn't run)."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", default="origin/main", help="branch the change will merge into")
    p.add_argument(
        "--repo", type=Path, default=ROOT, help="repository to check (default: this script's); CI checks a PR with main's copy"
    )
    args = p.parse_args(argv)
    root = args.repo.resolve()
    diff = collect(args.base, root)
    sources = watched_sources(args.base, root)
    if diff is None or sources is None:
        print(f"floor-guard: no merge base against {args.base}", file=sys.stderr)
        return 2
    messages = _git(root, "log", "--format=%B", f"{args.base}..HEAD") or ""
    gitleaks_ignore = root / GITLEAKS_IGNORE_FILE
    findings = find_violations(
        *diff,
        ignore=load_ignore(root),
        skip_reasons=skip_reasons_from(messages),
        approved_exceptions=approvals_from(messages),
        gitleaksignore_text=gitleaks_ignore.read_text() if gitleaks_ignore.is_file() else None,
    )
    findings = _unique(findings + eval_threshold_findings(*sources))
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
