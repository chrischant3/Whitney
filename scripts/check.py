"""Run the CONSTRAINTS.md checks for one stage of the work.

    python scripts/check.py fast    # after an edit: floor guard + secrets (seconds)
    python scripts/check.py task    # before handing work back: + tests (on every core), coverage, mutation of touched functions (~1 min)
    python scripts/check.py full    # before a pull request: + every mutant, the wheel installed in a venv, dependency vulnerabilities
    python scripts/check.py full --max-children 6   # the same with at most 6 mutmut workers, to spare memory

`--skip-mutation` (task or full) runs everything except mutmut and prints a warning
in place of the mutation scores. It is a stopgap while mutation testing is too slow
(decided 2026-11-09, tasks/plan-phase5.md): a skipped run doesn't show the mutation
floor or ratchet hold.

`task` judges the touched functions' mutants fresh and the rest from mutmut's cache;
`full` reruns every mutant against the current tests, after deleting mutmut's map of which
tests reach which function so that mutmut rebuilds it. A new worktree starts warm
from a copy of the main checkout's mutants/ (CONSTRAINTS.md, "How to run").

CONSTRAINTS.md is the source of truth; this script mirrors it. The floor and
secrets always block. The numbered checks warn until WARN_UNTIL, then block.
Ratchet values ("Measured, not yet enforced") are read from CONSTRAINTS.md.

Exit codes: 0 pass (warnings allowed), 1 a blocking check failed, 2 a tool
couldn't run.
"""

import argparse
import ast
import importlib.metadata
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PYTHON = sys.executable
CONSTRAINTS = ROOT / "CONSTRAINTS.md"

WARN_UNTIL = date(2026, 11, 16)  # numbered checks block from this date (CONSTRAINTS.md, "Enforced with numbers")
CHANGED_LINE_COVERAGE = 80.0
TOUCHED_MUTATION_SCORE = 60.0
RATCHET_TOLERANCE = 0.5  # percentage points
CACHED_SHARE_NOT_CHECKED = 0.05  # above this share of mutants left out, a cached score only warns
NO_MATCHING_MUTANTS = "Filtered for specific mutants, but nothing matches"  # mutmut 3.8's message

PASS, WARN, FAIL, ERROR = "pass", "warn", "FAIL", "ERROR"


def mutation_score(statuses: Iterable[str]) -> float | None:
    """Percent of mutants caught: (killed + timeout) / all except segfaults and skips.

    A segfault says the test process crashed under the mutant, which is
    evidence of neither a good nor a bad test, so it's left out. "no tests"
    counts as not caught. None if nothing is left to score.
    """
    statuses = [s for s in statuses if s not in ("segfault", "skipped")]
    if not statuses:
        return None
    return 100.0 * sum(s in ("killed", "timeout") for s in statuses) / len(statuses)


def touched_function_prefixes(path: Path, lines: set[int], src_root: Path = SRC) -> set[str]:
    """mutmut mutant-name prefixes for the functions in `path` that contain any of `lines`."""
    parts = path.relative_to(src_root).with_suffix("").parts
    # mutmut names a package's __init__.py mutants after the package itself.
    module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
    prefixes = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _spans(node, lines):
            prefixes.add(f"{module}.x_{node.name}__mutmut_")
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and _spans(item, lines):
                    prefixes.add(f"{module}.xǁ{node.name}ǁ{item.name}__mutmut_")
    return prefixes


def mutmut_patterns(prefixes: Iterable[str]) -> list[str]:
    """`mutmut run` arguments that select every mutant of the functions with these name prefixes.

    mutmut 3.8 matches each argument against mutant names with fnmatch, whole name only, so a
    prefix plus `*` selects that function's mutants and nothing in another module. It also
    matches each argument containing `*` against function names (a prefix without its
    `__mutmut_`) to choose the tests of its clean run; when none match it runs the whole suite,
    which took most of the stage's time. So each function also gets `*` plus its name: that
    matches the function itself and, since every mutant name ends in `__mutmut_<n>`, no mutant.
    """
    functions = {prefix.removesuffix("__mutmut_") for prefix in prefixes}
    return sorted({f"{prefix}*" for prefix in prefixes} | {f"*{function}" for function in functions})


def installed_requirements(distributions: Iterable) -> list[str]:
    """`name==version` for each installed distribution, once each, leaving out editable installs.

    An editable install is this repo's own source, not a published package to look up. Every
    copy of its name is left out, since an editable install can also leave an egg-info folder
    in src/ that records no direct_url.json.
    """
    distributions = list(distributions)
    editable = set()
    for dist in distributions:
        direct_url = dist.read_text("direct_url.json")
        if direct_url and json.loads(direct_url).get("dir_info", {}).get("editable"):
            editable.add(dist.metadata["Name"].lower())
    pins = {}
    for dist in distributions:
        name = dist.metadata["Name"].lower()
        if name not in editable:
            pins.setdefault(name, f"{name}=={dist.version}")
    return sorted(pins.values())


def _spans(node: ast.AST, lines: set[int]) -> bool:
    start = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
    return any(start <= n <= node.end_lineno for n in lines)


def read_ratchets(text: str) -> dict[str, float]:
    """Metric -> recorded percentage, from the "Measured, not yet enforced" table."""
    section = text.split("## Measured, not yet enforced", 1)[-1].split("\n## ", 1)[0]
    ratchets = {}
    for row in section.splitlines():
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) >= 2 and (m := re.fullmatch(r"(\d+(?:\.\d+)?)%", cells[1])):
            ratchets[cells[0]] = float(m.group(1))
    return ratchets


def ratchet_holds(today: float, recorded: float, tolerance: float = RATCHET_TOLERANCE) -> bool:
    """True unless today's value fell more than `tolerance` points below the recorded one."""
    return today >= recorded - tolerance


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, **kwargs)


class Checks:
    """Runs checks, records outcomes, and prints a summary."""

    def __init__(
        self, base: str, today: date, run=None, mutants: Path | None = None, max_children: int | None = None
    ) -> None:
        """`run` runs a command in the repo root, as `_run` does, and `mutants` is mutmut's
        folder; tests pass stand-ins for both. `max_children` caps mutmut's workers; None
        leaves mutmut its default of one per core."""
        self.base = base
        self.max_children = max_children
        self.run = run or _run
        self.mutants = mutants or ROOT / "mutants"
        self.numbered_outcome = WARN if today < WARN_UNTIL else FAIL
        self.results: list[tuple[str, str, str]] = []

    def record(self, name: str, outcome: str, detail: str = "") -> None:
        self.results.append((name, outcome, detail))
        print(f"[{outcome}] {name}" + (f": {detail}" if detail else ""), flush=True)

    def numbered(self, name: str, ok: bool, detail: str) -> None:
        self.record(name, PASS if ok else self.numbered_outcome, detail)

    def exit_code(self) -> int:
        outcomes = {o for _, o, _ in self.results}
        return 2 if ERROR in outcomes else 1 if FAIL in outcomes else 0

    # fast ---------------------------------------------------------------

    def floor(self) -> None:
        result = _run([PYTHON, str(ROOT / "scripts" / "floor_guard.py"), "--base", self.base])
        outcome = {0: PASS, 1: FAIL}.get(result.returncode, ERROR)
        self.record("floor guard", outcome, (result.stdout + result.stderr).strip() if outcome != PASS else "")

    def secrets(self) -> None:
        # `gitleaks git --pre-commit` only sees tracked files, so new files get a `gitleaks dir` scan of their own.
        untracked = _run(["git", "ls-files", "--others", "--exclude-standard", "-z"]).stdout.split("\0")
        scans = [
            ("history", [["gitleaks", "git", "--redact", "--no-banner", "--exit-code", "1", "."]]),
            ("uncommitted", [["gitleaks", "git", "--redact", "--no-banner", "--exit-code", "1", "--pre-commit", "."]]),
            ("untracked", [["gitleaks", "dir", "--redact", "--no-banner", "--exit-code", "1", f] for f in untracked if f]),
        ]
        for label, commands in scans:
            outcome, detail = PASS, ""
            for command in commands:
                result = _run(command)
                if result.returncode != 0:
                    outcome = FAIL if result.returncode == 1 else ERROR
                    detail = f"{command[-1]}: {' '.join(result.stderr.strip().splitlines()[-1:])}"
                    break
            self.record(f"secrets ({label})", outcome, detail)

    # task ---------------------------------------------------------------

    def tests_and_coverage(self) -> float | None:
        """Run the whole suite on every core under coverage; return the project's line coverage in percent.

        `-n auto` is only here: mutmut runs its own workers, so its pytest runs stay on one process.
        pytest-cov combines the xdist workers' coverage into one coverage.xml.
        """
        result = self.run(
            [PYTHON, "-m", "pytest", "-q", "-n", "auto", "--cov=whitney", "--cov-report=xml:coverage.xml"]
        )
        if result.returncode != 0:
            self.record("tests", FAIL, result.stdout.strip().splitlines()[-1] if result.stdout else result.stderr[-300:])
            return None
        self.record("tests", PASS, result.stdout.strip().splitlines()[-1])
        # The terminal report rounds to whole percent; the XML has the exact rate.
        rate = re.search(r'<coverage[^>]*\bline-rate="([\d.]+)"', (ROOT / "coverage.xml").read_text())
        return 100.0 * float(rate.group(1)) if rate else None

    def changed_line_coverage(self) -> None:
        result = _run(
            [PYTHON, "-m", "diff_cover.diff_cover_tool", "coverage.xml", f"--compare-branch={self.base}",
             "--include-untracked", f"--fail-under={CHANGED_LINE_COVERAGE}"]
        )
        summary = next((l for l in result.stdout.splitlines() if l.startswith(("Coverage:", "No lines"))), "")
        if result.returncode not in (0, 1):
            self.record("changed-line coverage", ERROR, result.stderr.strip()[-300:])
        else:
            self.numbered(f"changed-line coverage ≥ {CHANGED_LINE_COVERAGE:.0f}%", result.returncode == 0, summary)

    def mutation(self, ratchets: dict[str, float], prefixes: set[str], stage: str) -> None:
        """Run mutmut for `stage`, then judge the touched functions and the project ratchet.

        `full` reruns every mutant against the current tests. `task` reruns only the mutants of
        the functions named by `prefixes`, so its project score comes from mutmut's cache: the
        touched functions' fresh results plus every other function's last result.
        """
        # With any name given, mutmut reruns every mutant it selects; with none it keeps every
        # cached verdict, so a weaker test would never be measured. "*" selects every mutant
        # and, for mutmut's clean run, every test.
        selection = mutmut_patterns(prefixes) if stage == "task" else ["*"]
        if stage == "full":
            # mutmut 3.8.1 adds a test to its map of which tests reach which function only when
            # the test is new, so an existing test that starts reaching a function is never run
            # against that function's mutants. Deleting the map makes mutmut rebuild all of it.
            (self.mutants / "mutmut-stats.json").unlink(missing_ok=True)
        if selection:
            cap = ["--max-children", str(self.max_children)] if self.max_children else []
            run = self.run([PYTHON, "-m", "mutmut", "run", *cap, *selection])
            output = run.stdout + run.stderr
            # mutmut stops with an AssertionError when no mutant matches the selection, as when the
            # touched functions have no mutants. In task there is nothing to judge then; in full,
            # no mutants at all means mutmut found nothing to mutate, which is a broken setup.
            no_mutants_touched = run.returncode != 0 and stage == "task" and NO_MATCHING_MUTANTS in output
            if run.returncode != 0 and not no_mutants_touched:
                self.record("mutation testing", ERROR, output.strip()[-300:])
                return
        results = self.run([PYTHON, "-m", "mutmut", "results", "--all", "true"])
        if results.returncode != 0:
            self.record("mutation results", ERROR, (results.stdout + results.stderr).strip()[-300:])
            return
        statuses = dict(line.strip().rsplit(": ", 1) for line in results.stdout.splitlines() if ": " in line)
        self._project_mutation_ratchet(statuses, ratchets, cached=stage == "task")
        self._touched_mutation_score(statuses, prefixes)

    def _touched_mutation_score(self, statuses: dict[str, str], prefixes: set[str]) -> None:
        """Judge the touched functions' mutants against TOUCHED_MUTATION_SCORE.

        A touched function with no mutants is named, not judged. mutmut hashes in its .meta
        only the functions it made mutants of, so a hashed function with no mutant under its
        prefix means the prefix is wrong, and that is an error rather than a pass.
        """
        name = f"mutation score (touched functions) ≥ {TOUCHED_MUTATION_SCORE:.0f}%"
        without_mutants = []
        for prefix in sorted(prefixes):
            function = prefix.removesuffix("__mutmut_")
            module, _, key = function.rpartition(".")
            hashed = self._mutated_functions(module)
            if hashed is None:
                self.record("mutation testing", ERROR, f"mutmut has no record of {module}, so the prefix for {function} is wrong")
                return
            has_mutants = any(n.startswith(prefix) for n in statuses)
            if key in hashed and not has_mutants:
                self.record("mutation testing", ERROR, f"mutmut made mutants of {function} but none match {prefix}*")
                return
            if not has_mutants:
                without_mutants.append(function)
        touched = [s for n, s in statuses.items() if any(n.startswith(p) for p in prefixes)]
        score = mutation_score(touched)
        if score is None:
            reason = f"no mutants in the touched functions: {', '.join(without_mutants)}" if prefixes else "no touched functions"
            self.record(name, PASS, reason)
            return
        survivors = sorted(n for n, s in statuses.items() if s in ("survived", "no tests") and any(n.startswith(p) for p in prefixes))
        detail = f"{score:.1f}% of {len(touched)} mutants" + (
            f"; survivors include {', '.join(survivors[:5])} (see `mutmut show <name>`)" if survivors else ""
        ) + (f"; no mutants in {', '.join(without_mutants)}" if without_mutants else "")
        self.numbered(name, score >= TOUCHED_MUTATION_SCORE, detail)

    def _mutated_functions(self, module: str) -> set[str] | None:
        """The functions mutmut made mutants of in `module`, from its .meta; None if it has none."""
        path = self.mutants / "src" / Path(*module.split("."))
        for meta in (path.with_name(path.name + ".py.meta"), path / "__init__.py.meta"):
            if meta.is_file():
                return set(json.loads(meta.read_text(encoding="utf-8")).get("hash_by_function_name", {}))
        return None

    def _project_mutation_ratchet(self, statuses: dict[str, str], ratchets: dict[str, float], cached: bool) -> None:
        """Judge the project mutation score against its ratchet.

        A cached score is still enforced, not just reported: every result in it is one mutmut
        measured, so a fall in it is a finding until `full` measures otherwise. Mutants mutmut
        has not checked since their function changed have no result yet, so they are left out
        of a cached score and counted. When more than CACHED_SHARE_NOT_CHECKED of them are left
        out, or there are no results at all, a score that holds is only a warning.
        """
        recorded = ratchets.get("Mutation score (project)")
        if recorded is None:
            return
        name = "mutation score (project, cached; run `check.py full` to refresh)" if cached else "mutation score (project)"
        not_checked = sum(s == "not checked" for s in statuses.values()) if cached else 0
        project = mutation_score(s for s in statuses.values() if not (cached and s == "not checked"))
        if project is None:
            if cached:
                self.record(f"{name} must not fall", WARN, "no cached mutation results; run `check.py full`")
            else:
                self.record(f"{name} must not fall", ERROR, "mutmut reported no results")
            return
        detail = f"{project:.1f}% (recorded {recorded:.1f}%)" + (f"; {not_checked} not checked, left out" if not_checked else "")
        holds = ratchet_holds(project, recorded)
        if holds and not_checked > CACHED_SHARE_NOT_CHECKED * len(statuses):
            covered = len(statuses) - not_checked
            self.record(f"{name} must not fall", WARN,
                        f"{detail}; cached score covers {covered} of {len(statuses)} mutants; run `check.py full`")
            return
        self.numbered(f"{name} must not fall", holds, detail)

    def touched_prefixes(self) -> set[str]:
        """mutmut mutant-name prefixes of the src/ functions the diff since the merge base touches."""
        sys.path.insert(0, str(ROOT / "scripts"))
        from floor_guard import collect

        diff = collect(self.base)
        if diff is None:
            return set()
        lines_by_file: dict[str, set[int]] = {}
        for line in diff[0]:
            if line.file.startswith("src/") and line.file.endswith(".py"):
                lines_by_file.setdefault(line.file, set()).add(line.line)
        prefixes = set()
        for file, lines in lines_by_file.items():
            if (ROOT / file).is_file():
                prefixes |= touched_function_prefixes(ROOT / file, lines)
        return prefixes

    def coverage_ratchet(self, total: float | None, ratchets: dict[str, float]) -> None:
        recorded = ratchets.get("Project coverage")
        if total is not None and recorded is not None:
            self.numbered("project coverage must not fall", ratchet_holds(total, recorded), f"{total:.1f}% (recorded {recorded:.1f}%)")

    # full ---------------------------------------------------------------

    def installed_package(self) -> None:
        """Run the tests that install the built wheel in a temporary venv (tests/test_packaging.py).

        They are marked `packaging` and left out of the default run, which builds the wheel
        and reads its contents but doesn't install it.
        """
        result = self.run([PYTHON, "-m", "pytest", "-q", "-m", "packaging", "tests/test_packaging.py"])
        outcome = {0: PASS, 1: FAIL}.get(result.returncode, ERROR)
        last = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else result.stderr.strip()[-300:]
        self.record("installed package", outcome, last)

    def dependencies(self, distributions: Iterable | None = None) -> None:
        """Scan the installed distributions for advisories at high or above.

        The list is built in-process rather than from `pip freeze`, which a uv venv may not have.
        An empty list is an error: osv-scanner would otherwise fail on it, or a later version pass it.
        """
        requirements_list = installed_requirements(importlib.metadata.distributions() if distributions is None else distributions)
        if not requirements_list:
            self.record("dependency vulnerabilities", ERROR, "found no installed distributions to scan")
            return
        with tempfile.TemporaryDirectory() as tmp:
            requirements = Path(tmp) / "requirements.txt"
            requirements.write_text("\n".join(requirements_list) + "\n")
            # --no-resolve: an installed environment is already the full transitive set.
            result = self.run(["osv-scanner", "scan", "--no-resolve", "-L", f"requirements.txt:{requirements}"])
        if result.returncode not in (0, 1):
            self.record("dependency vulnerabilities", ERROR, result.stderr.strip()[-300:])
            return
        high = re.search(r"\((\d+) Critical, (\d+) High", result.stdout)
        count = int(high.group(1)) + int(high.group(2)) if high else 0
        self.numbered("dependency vulnerabilities (high or above)", count == 0, f"{count} found" if count else "none")


def _at_least_one(text: str) -> int:
    number = int(text)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, not {number}")
    return number


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("stage", choices=("fast", "task", "full"))
    p.add_argument("--base", default="origin/main", help="branch the change will merge into")
    p.add_argument("--skip-mutation", action="store_true",
                   help="task or full: run everything except mutation testing, and warn that it was skipped")
    p.add_argument(
        "--max-children", type=_at_least_one, default=None,
        help="at most this many mutmut workers (default: one per core); fewer keep a laptop responsive",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the named stage and return the exit code."""
    args = parse_args(argv)
    checks = Checks(args.base, date.today(), max_children=args.max_children)
    ratchets = read_ratchets(CONSTRAINTS.read_text(encoding="utf-8"))

    checks.floor()
    checks.secrets()
    if args.stage in ("task", "full"):
        total = checks.tests_and_coverage()
        checks.changed_line_coverage()
        checks.coverage_ratchet(total, ratchets)
        if args.skip_mutation:
            checks.record("mutation testing", WARN, "skipped (--skip-mutation); the mutation scores weren't judged")
        else:
            checks.mutation(ratchets, checks.touched_prefixes(), args.stage)
    if args.stage == "full":
        checks.installed_package()
        checks.dependencies()

    code = checks.exit_code()
    print({0: "\nchecks: pass", 1: "\nchecks: blocked", 2: "\nchecks: a tool couldn't run"}[code])
    return code


if __name__ == "__main__":
    sys.exit(main())
