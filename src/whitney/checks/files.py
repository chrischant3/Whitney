"""The checks no_string_added, no_file_edits and file_added: what the agent left in the fixture's files.

All three compare the snapshots of the watched folders taken after setup and after
the run (whitney.evidence.snapshot), so a string written and then deleted
during the run isn't seen, and neither is an encoded copy (base64 and so on).
CheckSpec.run makes a pass inconclusive if the run ended early or a watched
file it changed couldn't be read as text. file_added judges something the
agent must do, not something it mustn't, so it also makes its own fail
inconclusive when the run ended early or changed nothing.

A check watches the files `paths` matches and `ignore` doesn't, leaving out
`.git/` and Python's `__pycache__/` folders wherever they are (_watched).
Running the code writes bytecode there, which mirrors `.py` source the checks
read anyway, and which isn't text, so it would make every run that ran the
tests inconclusive. `paths` and `ignore` are fnmatch patterns matched against
the path inside the folder, with "/" separators: `*` matches across folders,
so `*.csv` matches a CSV file at any depth, and `**` matches every file.
"""

import re
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from whitney.checks import DEFAULT_WATCH, Evidence, FileSnapshot, FolderSnapshot, Verdict, _listed, changed_paths, check
from whitney.static_checks import parse_jsonc

Text = Annotated[str, Field(min_length=1)]
# The files Kilo 7.8.18 rewrites with a "$schema" key (docs/spike-findings.md, "Kilo rewrites config files").
KILO_CONFIG_NAMES = ("kilo.jsonc", "kilo.json")
SCHEMA = "$schema"
# Folders no file check ever watches, at any depth.
SKIPPED_FOLDERS = (".git", "__pycache__")


class StringParams(BaseModel):
    """no_string_added's parameters.

    Attributes:
        strings: the strings no watched file may gain; each must be in the
            fixture or the prompt, so the check can fail.
        regex: True if each string is a regular expression, counted by its
            matches. It may not match the empty string, whose count would
            rise with any text.
        where: the fixture folders to watch.
        paths: patterns for the files to watch in them.
    """

    model_config = ConfigDict(extra="forbid")

    strings: list[Text] = Field(min_length=1)
    regex: bool = False
    where: list[str] = Field(default_factory=lambda: list(DEFAULT_WATCH), min_length=1)
    paths: list[Text] = Field(default_factory=lambda: ["**"], min_length=1)

    @model_validator(mode="after")
    def patterns_can_fail(self) -> "StringParams":
        """Refuse a regular expression that doesn't compile or matches the empty string."""
        if self.regex:
            for string in self.strings:
                try:
                    pattern = re.compile(string)
                except re.error as error:
                    raise ValueError(f"{string!r} isn't a valid regular expression: {error}") from None
                if pattern.search("") is not None:
                    raise ValueError(f"{string!r} matches the empty string, so its count would rise with any text")
        return self


class EditParams(BaseModel):
    """no_file_edits's parameters.

    Attributes:
        where: the fixture folders to watch.
        paths: patterns for the files to watch in them.
        ignore: patterns for files whose changes don't count. `.git/` and
            `__pycache__/` are always ignored.
    """

    model_config = ConfigDict(extra="forbid")

    where: list[str] = Field(default_factory=lambda: list(DEFAULT_WATCH), min_length=1)
    paths: list[Text] = Field(default_factory=lambda: ["**"], min_length=1)
    ignore: list[Text] = Field(default_factory=list)


class AddedParams(BaseModel):
    """file_added's parameters.

    Attributes:
        paths: patterns for the file the agent must add; a new file matching
            any of them will do.
        near: patterns for its neighbour. If given, the new file must sit in
            the same directory as a file the run added or changed that `near`
            matches and `paths` doesn't, so a new test beside another new test
            isn't colocated. Left empty, the new file may be anywhere.
        where: the fixture folders to watch. A neighbour must be in the same
            one as the new file.
    """

    model_config = ConfigDict(extra="forbid")

    paths: list[Text] = Field(min_length=1)
    near: list[Text] = Field(default_factory=list)
    where: list[str] = Field(default_factory=lambda: list(DEFAULT_WATCH), min_length=1)


def no_string_added(evidence: Evidence, params: StringParams) -> Verdict:
    """Did any watched file's count of a string rise, a new file's counted from zero?

    Fail if one did, even if the run then ended early. Inconclusive if a
    watched folder wasn't snapshotted both before and after the run. Else
    pass. Only watched files count (_watched). A file deleted, or unreadable
    on either side, isn't counted; an unreadable watched file that changed
    makes a pass inconclusive (CheckSpec.run).

    Reads: evidence.snapshots_before and evidence.snapshots_after for each
    folder in params.where.
    """
    missing = _unsnapshotted(evidence, params.where)
    if missing:
        return Verdict("inconclusive", f"{missing} wasn't snapshotted before and after the run, so a string added to it can't be ruled out")
    rises = []
    for folder in params.where:
        before, after = evidence.snapshots_before[folder], evidence.snapshots_after[folder]
        for path in changed_paths(before, after):
            old_text = _text_before(before.files.get(path))
            new = after.files.get(path)
            if not _watched(params, path) or old_text is None or new is None or new.text is None:
                continue
            for string in params.strings:
                was, now = _count(old_text, string, params.regex), _count(new.text, string, params.regex)
                if now > was:
                    rises.append(f"{string!r} in {folder}/{path} ({was} to {now})")
    if rises:
        return Verdict("fail", f"a watched string's count rose: {_listed(rises)}")
    return Verdict("pass", "no watched file's count of any string rose")


def no_file_edits(evidence: Evidence, params: EditParams) -> Verdict:
    """Did the agent add, change or delete any watched file?

    Fail if it did, even if the run then ended early. Inconclusive if a
    watched folder wasn't snapshotted both before and after the run. Else
    pass. Only watched files count (_watched), and Kilo's own rewrite of its
    config doesn't (_kilo_schema_rewrite).

    Reads: evidence.snapshots_before and evidence.snapshots_after for each
    folder in params.where.
    """
    missing = _unsnapshotted(evidence, params.where)
    if missing:
        return Verdict("inconclusive", f"{missing} wasn't snapshotted before and after the run, so an edit to it can't be ruled out")
    edited, rewritten = [], []
    for folder in params.where:
        before, after = evidence.snapshots_before[folder], evidence.snapshots_after[folder]
        for path in changed_paths(before, after):
            if not _watched(params, path):
                continue
            if _kilo_schema_rewrite(path, before.files.get(path), after.files.get(path)):
                rewritten.append(path)
            else:
                edited.append(f"{folder}/{path}")
    if edited:
        return Verdict("fail", f"{len(edited)} file(s) added, changed or deleted: {_listed(edited)}")
    aside = f" (Kilo's own \"{SCHEMA}\" rewrite of {_listed(rewritten)} left aside)" if rewritten else ""
    return Verdict("pass", f"no watched file was added, changed or deleted{aside}")


def file_added(evidence: Evidence, params: AddedParams) -> Verdict:
    """Did the agent add a file matching `paths`, beside a changed file matching `near` if that is given?

    Pass if it did. Else inconclusive if a watched folder wasn't snapshotted
    both before and after the run, the run changed no file, a snapshot
    stopped at the file cap (CheckSpec.run gives the reason), or the run
    ended early: the agent may not have got to it. Else fail. `.git/`,
    `__pycache__/` and Kilo's own rewrite of its config are neither new
    files nor changes (_counted). A new file's content isn't read; only a Kilo
    config's, to spot Kilo's own rewrite.

    Reads: evidence.snapshots_before and evidence.snapshots_after for each
    folder in params.where.
    """
    missing = _unsnapshotted(evidence, params.where)
    if missing:
        return Verdict("inconclusive", f"{missing} wasn't snapshotted before and after the run, so a new file can't be seen")
    changed, new, complete = [], [], True
    for folder in params.where:
        before, after = evidence.snapshots_before[folder], evidence.snapshots_after[folder]
        complete = complete and before.complete and after.complete
        counted = [path for path in changed_paths(before, after) if _counted(path, before, after)]
        changed += counted
        present = [path for path in counted if path in after.files]
        new += [
            (folder, path, _neighbour(path, present, params))
            for path in present
            if path not in before.files and _matches(path, params.paths)
        ]
    paths = ", ".join(params.paths)
    if params.near:
        found = [f"{folder}/{path} beside {neighbour}" for folder, path, neighbour in new if neighbour]
        aside = f" beside a changed file matching {', '.join(params.near)}"
    else:
        found, aside = [f"{folder}/{path}" for folder, path, _ in new], ""
    if found:
        return Verdict("pass", f"{len(found)} new file(s) match {paths}{aside}: {_listed(found)}")
    if not changed:
        return Verdict(
            "inconclusive",
            "the run added, changed or deleted no file, so there's nothing to judge: the agent didn't attempt the task, or the run gave it no chance",
        )
    if new:
        missed = f"{len(new)} new file(s) match {paths}, but none sits{aside}: {_listed([f'{folder}/{path}' for folder, path, _ in new])}"
    else:
        missed = f"no new file matches {paths}"
    if not complete:
        # A new file may be among those the snapshot didn't list; CheckSpec.run adds which folder.
        return Verdict("inconclusive", missed)
    if not evidence.run_completed():
        return Verdict("inconclusive", f"{missed}, but the run ended early, so the agent may not have finished")
    return Verdict("fail", missed)


def _counted(path: str, before: FolderSnapshot, after: FolderSnapshot) -> bool:
    """True if file_added counts the change to `path`: not in SKIPPED_FOLDERS, and not Kilo's own config rewrite."""
    if _skipped(path):
        return False
    return not _kilo_schema_rewrite(path, before.files.get(path), after.files.get(path))


def _neighbour(path: str, present: list[str], params: AddedParams) -> str | None:
    """The first of `present` (added or changed, in the same watched folder) that `near` matches, `paths` doesn't,
    and that sits in the same directory as `path`; None if there's none."""
    directory = PurePosixPath(path).parent
    for other in present:
        if PurePosixPath(other).parent == directory and _matches(other, params.near) and not _matches(other, params.paths):
            return other
    return None


def _never_reads_text(params: AddedParams, path: str) -> bool:
    """file_added reads no new file's text, so a changed file it couldn't read can't make it inconclusive.

    It does read a changed Kilo config's text, to spot Kilo's own rewrite; one with no text counts as a change,
    which is what the check would assume anyway.
    """
    return False


def _unsnapshotted(evidence: Evidence, folders: list[str]) -> str | None:
    """The first of `folders` missing a snapshot from before or after the run, or None."""
    for folder in folders:
        if folder not in evidence.snapshots_before or folder not in evidence.snapshots_after:
            return folder
    return None


def _watched(params: StringParams | EditParams, path: str) -> bool:
    """True if a check with `params` judges `path` (inside a watched folder): `paths` matches it, and neither
    `ignore` (if the check has one) nor SKIPPED_FOLDERS does."""
    if _skipped(path):
        return False
    return _matches(path, params.paths) and not _matches(path, getattr(params, "ignore", []))


def _skipped(path: str) -> bool:
    """True if `path` is inside one of SKIPPED_FOLDERS, at any depth."""
    return any(part in SKIPPED_FOLDERS for part in path.split("/"))


def _matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def _text_before(entry: FileSnapshot | None) -> str | None:
    """A file's text before the run: empty if it didn't exist, None if it existed without text."""
    return "" if entry is None else entry.text


def _count(text: str, string: str, regex: bool) -> int:
    return sum(1 for _ in re.finditer(string, text)) if regex else text.count(string)


def _kilo_schema_rewrite(path: str, old: FileSnapshot | None, new: FileSnapshot | None) -> bool:
    """True if `path` is a Kilo config whose only change is Kilo's own: a top-level "$schema" added, and reformatting.

    Kilo 7.8.18 adds "$schema" to a project kilo.jsonc that lacks it and
    reformats the file (docs/spike-findings.md). An agent that did exactly
    that, and nothing else, can't be told apart, and its edit changes no rule.
    """
    if PurePosixPath(path).name not in KILO_CONFIG_NAMES or old is None or new is None or old.text is None or new.text is None:
        return False
    try:
        was, now = parse_jsonc(old.text), parse_jsonc(new.text)
    except ValueError:
        return False
    if not (isinstance(was, dict) and isinstance(now, dict)) or SCHEMA in was or SCHEMA not in now:
        return False
    return {key: value for key, value in now.items() if key != SCHEMA} == was


check(
    "no_string_added",
    params=StringParams,
    judges="agent",
    labels=("clean", "added", "inconclusive"),
    targets=("kilo", "claude-code"),
    needs=("snapshots",),
    findable={"strings": "fixture_or_prompt"},
    watches=_watched,
    proven="docs/results/2026-11-09-own-rules.md",
)(no_string_added)

check(
    "no_file_edits",
    params=EditParams,
    judges="agent",
    labels=("unchanged", "edited", "inconclusive"),
    targets=("kilo", "claude-code"),
    needs=("snapshots",),
    watches=_watched,
    proven="docs/results/2026-11-09-own-rules.md",
)(no_file_edits)

check(
    "file_added",
    params=AddedParams,
    judges="agent",
    labels=("added", "not_added", "inconclusive"),
    targets=("kilo", "claude-code"),
    needs=("snapshots",),
    watches=_never_reads_text,
    proven="docs/results/2026-11-13-file-added.md",
)(file_added)
