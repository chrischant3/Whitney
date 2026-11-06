"""Collect safe text configuration and instruction files for eval workspaces.

There is one project loader per target: `load_project_files` for kilo and
`load_claude_code_project_files` for claude-code. `load_project_files_for`
picks between them.
"""

import fnmatch
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from whitney.static_checks import parse_jsonc

CONFIG_PATHS = (
    "kilo.jsonc",
    "kilo.json",
    ".kilo/kilo.jsonc",
    ".kilo/kilo.json",
    ".kilocode/kilo.jsonc",
    ".kilocode/kilo.json",
)
ROOT_INSTRUCTIONS = ("AGENTS.md", "CLAUDE.md", "CONTEXT.md")
KILO_ASSET_DIRS = ("agent", "agents", "command", "commands", "instructions", "mode", "modes", "skill", "skills")
KILO_CONFIG_DIRS = (".kilo", ".kilocode")
# Kilo 7.8.18 also reads these without an `instructions` entry (docs/load-order.md, "Rules" and
# "Skills, agents and commands", checked with `kilo debug config` and `kilo debug skill`), so the
# Kilo loader stages them too, or a staged project would load less than the real one. A rules
# folder counts only for the .md files directly in it, as Kilo reads it; a mode's rules load in
# every mode (kilo debug config lists them all).
KILO_RULE_MODES = ("code", "architect", "ask", "debug", "orchestrator")
KILO_RULE_DIRS = tuple(
    f"{config_dir}/{name}" for config_dir in KILO_CONFIG_DIRS for name in ("rules", *(f"rules-{mode}" for mode in KILO_RULE_MODES))
)
KILO_LEGACY_RULE_FILES = (".kilocoderules", *(f".kilocoderules-{mode}" for mode in KILO_RULE_MODES))
KILO_SKILL_DIRS = (".claude/skills", ".agents/skills")
# Kilo also turns the .md files directly in these folders into commands, and .kilocodemodes's
# modes into agents (kilo debug config, docs/load-order.md, "Workflows and custom modes").
KILO_WORKFLOW_DIRS = tuple(f"{config_dir}/workflows" for config_dir in KILO_CONFIG_DIRS)
KILO_MODES_FILE = ".kilocodemodes"
# Skills can carry fonts and images, so a file in a skill folder that isn't UTF-8 is left out and
# named in the report instead of stopping the run.
KILO_SKILL_FOLDERS = (*(f"{d}/{name}" for d in KILO_CONFIG_DIRS for name in ("skill", "skills")), *KILO_SKILL_DIRS)
NOT_UTF8_REASON = "not UTF-8 text"
# skills.paths names more skill folders, which can be anywhere on the machine, so it is stripped
# from the staged config as the connection keys are, and named in the report.
SKILLS_KEY, SKILL_PATHS_KEY = "skills", "paths"
SKILL_PATHS_REASON = "left out of the staged config: its folders can be outside the project"
# A brace pattern such as {a,b}{c,d}... doubles with each group, so its expansion is capped.
MAX_BRACE_ALTERNATIVES = 1024
EXCLUDED_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache", ".tox"}
SAFE_ENV_FILES = {".env.example", ".env.sample", ".env.template"}
CONNECTION_CONFIG_KEYS = {"provider", "enabled_providers", "disabled_providers", "mcp", "plugin"}
# Kilo 7.8.18 expands an `instructions` entry with node-glob, matching dot files, not following
# symlinked folders, returning files only, and comparing names without regard to case on macOS and
# Windows (docs/load-order.md, "Instructions globs"). Whitney expands *, ?, **, [...], [!...] and
# {a,b} the same way, and refuses the rest of node-glob's syntax rather than stage other files than
# Kilo would read: extglobs such as !(a), ranges such as {1..3}, a brace group without a comma,
# [^...], [:class:], backslash escapes and a leading !.
UNEXPANDED_GLOB = re.compile(r"^!|[!@+*?]\(|\{[^{},]*\}|\{[^{}]*\.\.[^{}]*\}|\[\^|\[:|\\")
GLOB_CASE_BLIND = sys.platform in ("darwin", "win32")
# Whitney's claude-code evals and their baseline are about AGENTS.md, so the loader stages one
# instruction file, always as AGENTS.md: the first of these that is a regular file inside the
# project. A CLAUDE.md beside a staged AGENTS.md stays out: in one run on 2026-11-06 Claude Code
# ignored AGENTS.md once .claude/CLAUDE.md existed (docs/results/2026-11-06-claude-code-canary.md),
# which would make the results about a file the agent never read (tasks/plan-phase5.md,
# decision 5, as revised). The rest of .claude/ stays out too: settings.json can hold hooks,
# which run commands, and which of its keys are safe to copy is still open (spec open question 8).
CLAUDE_CODE_DIR = ".claude"
AGENTS_MD = "AGENTS.md"
CLAUDE_MD = "CLAUDE.md"
DOT_CLAUDE_MD = f"{CLAUDE_CODE_DIR}/{CLAUDE_MD}"
CLAUDE_CODE_ROOT_INSTRUCTIONS = (AGENTS_MD, CLAUDE_MD)
CLAUDE_CODE_INSTRUCTIONS = (*CLAUDE_CODE_ROOT_INSTRUCTIONS, DOT_CLAUDE_MD)
# files_not_staged names this many paths and counts the rest, so a large .claude/
# (Claude Code can keep whole worktrees there) can't swell the log or the report.
NOT_STAGED_LIMIT = 50
# Why files_not_staged_reasons says a name was left out.
BESIDE_REASON = "beside {staged}"
UNSAFE_REASON = "a symlink or outside the project"
REST_REASON = f"rest of {CLAUDE_CODE_DIR}/"
WHOLE_REASON = "a symlink, or can't be read"


def load_template_files(template_dir: Path) -> dict[str, str]:
    """Load every UTF-8 text file under a template, preserving relative paths."""
    root = Path(template_dir).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"template path is not a directory: {root}")
    return {relative: _read_text(path) for relative, path in _walk_files(root, root)}


def load_project_files(project_dir: Path, *, evals_path: Path | None = None) -> dict[str, str]:
    """Load a project's Kilo config and instruction assets, never its source tree.

    Provider connections and runtime integrations remain sourced from the user's
    global Kilo config. Project assets are copied into a temporary eval project.

    Args:
        project_dir: the project folder. It is read, never written.
        evals_path: the evals root; nothing under it is staged or read, even
            when an `instructions` pattern matches it.

    Raises:
        FileNotFoundError: project_dir doesn't exist.
        ValueError: project_dir isn't a folder or is inside evals_path, has no
            Kilo config, an `instructions` pattern reaches outside it or can't
            be expanded as Kilo expands it, or a staged file isn't UTF-8.
    """
    return _load_kilo(_project_root(project_dir), evals_path)[0]


def _load_kilo(root: Path, evals_path: Path | None) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """What load_project_files stages, and (name, reason) for each thing it left out that the report names."""
    evals = _evals_root(root, evals_path)
    config_paths = [relative for relative in CONFIG_PATHS if _is_kilo_file(root, relative, evals)]
    if not config_paths:
        raise ValueError(f"project has no Kilo config ({', '.join(CONFIG_PATHS)}): {root}")

    files_at_root = (*ROOT_INSTRUCTIONS, *config_paths, *KILO_LEGACY_RULE_FILES, KILO_MODES_FILE)
    paths = {relative for relative in files_at_root if _is_kilo_file(root, relative, evals)}
    skill_files = set()
    folders = [f"{config_dir}/{directory}" for config_dir in KILO_CONFIG_DIRS for directory in KILO_ASSET_DIRS]
    for folder in (*folders, *KILO_SKILL_DIRS):
        found = {f"{folder}/{relative}" for relative, _ in _walk_files(root / folder, root, evals)}
        (skill_files if folder in KILO_SKILL_FOLDERS else paths).update(found)
    for folder in (*KILO_RULE_DIRS, *KILO_WORKFLOW_DIRS):
        paths.update(_direct_md_files(root, folder, evals))

    files, left_out = {}, []
    for relative in sorted(paths | skill_files):
        if relative in paths:
            files[relative] = _read_text(root / relative)
            continue
        try:
            files[relative] = (root / relative).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            left_out.append((relative, NOT_UTF8_REASON))
    instruction_patterns = set()
    for relative in config_paths:
        config = parse_jsonc(files[relative])
        instructions = config.get("instructions")
        if isinstance(instructions, str):
            instruction_patterns.add(instructions)
        elif isinstance(instructions, list):
            instruction_patterns.update(item for item in instructions if isinstance(item, str))
        for key in CONNECTION_CONFIG_KEYS:
            config.pop(key, None)
        skills = config.get(SKILLS_KEY)
        if isinstance(skills, dict) and SKILL_PATHS_KEY in skills:
            del skills[SKILL_PATHS_KEY]
            left_out.append((f"{relative}: {SKILLS_KEY}.{SKILL_PATHS_KEY}", SKILL_PATHS_REASON))
            if not skills:
                del config[SKILLS_KEY]
        files[relative] = json.dumps(config, indent=2)

    for pattern in sorted(instruction_patterns):
        # _instruction_matches never looks in the evals folder or through a symlinked one.
        for match in _instruction_matches(root, pattern, evals):
            relative = match.relative_to(root).as_posix()
            if _is_project_file(root, relative) and _is_safe_file(match):
                files[relative] = _read_text(match)

    return files, sorted(left_out)


def load_claude_code_project_files(project_dir: Path, *, evals_path: Path | None = None) -> dict[str, str]:
    """Load one instruction file for Claude Code, always as AGENTS.md, and nothing else.

    The file is root AGENTS.md, else root CLAUDE.md, else .claude/CLAUDE.md
    (CLAUDE_CODE_INSTRUCTIONS gives the reason). files_renamed says when it
    wasn't AGENTS.md, and files_not_staged names the ones passed over. The
    same rules as load_project_files apply: a file that is a symlink, or
    that resolves outside the project, is skipped. A .claude folder that is
    itself a symlink is never followed. No Kilo config is needed.

    Args:
        project_dir: the project folder. It is read, never written.
        evals_path: the evals root, whose files are never staged.

    Returns:
        {"AGENTS.md": the chosen file's text}.

    Raises:
        FileNotFoundError: project_dir doesn't exist.
        ValueError: project_dir isn't a folder or is inside evals_path, holds
            none of the files (the message names them and says what is
            skipped), the chosen file is under evals_path, or it isn't UTF-8.
    """
    root = _project_root(project_dir)
    evals = _evals_root(root, evals_path)
    source = _claude_code_source(root)
    if source is None:
        raise ValueError(
            f"project has no Claude Code instructions ({', '.join(CLAUDE_CODE_INSTRUCTIONS)}; "
            f"symlinks and files outside the project are skipped): {root}"
        )
    if _under(root / source, evals):
        # Falling back to the next file would make the report's not-staged reasons wrong, so the run stops instead.
        raise ValueError(
            f"{root / source} is the instruction file to stage, but it is under the evals path {evals.path}, "
            "whose files are never staged"
        )
    return {AGENTS_MD: _read_text(root / source)}


def load_project_files_for(target: str, project_dir: Path, *, evals_path: Path | None = None) -> dict[str, str]:
    """Load a project with the loader for `target`'s harness.

    Args:
        target: "kilo" or "claude-code", as whitney.targets names them.
        project_dir: the project folder. It is read, never written.
        evals_path: the evals root, whose files are never staged.

    Returns:
        What load_project_files (kilo) or load_claude_code_project_files
        (claude-code) returns.

    Raises:
        KeyError: an unknown target.
        FileNotFoundError, ValueError: as the chosen loader raises them.
    """
    loaders = {"kilo": load_project_files, "claude-code": load_claude_code_project_files}
    return loaders[target](project_dir, evals_path=evals_path)


def files_renamed(target: str, project_dir: Path, staged: dict[str, str]) -> dict[str, str]:
    """Say which project file was staged under another name, for the report.

    Only claude-code renames: a CLAUDE.md or .claude/CLAUDE.md staged as
    AGENTS.md because the project has no usable AGENTS.md. The file is named
    as the folder spells it: names are looked up as the filesystem does, so
    on a case-insensitive disk a lowercase agents.md is staged, and named
    here, too. The project's own files are never renamed or changed, only
    the copy.

    Args:
        target: "kilo" or "claude-code".
        project_dir: the project folder, as given to the loader.
        staged: the files the variant stages, after any it drops; a
            baseline without AGENTS.md has no rename to name.

    Returns:
        {project-relative path: name it was staged as}, empty if nothing was renamed.

    Raises:
        FileNotFoundError, ValueError: as files_not_staged raises them (claude-code only).
    """
    if target != "claude-code" or AGENTS_MD not in staged:
        return {}
    root = _project_root(project_dir)
    spelled = _spelled(root, _claude_code_source(root))
    return {} if spelled == AGENTS_MD else {spelled: AGENTS_MD}


def files_not_staged(target: str, project_dir: Path, *, evals_path: Path | None = None) -> list[str]:
    """Name what the project holds for `target`'s harness that its loader leaves out, for the report.

    For kilo: each file in a skill folder that isn't UTF-8 text, and each
    config's skills.paths, named "<config>: skills.paths". For claude-code: each of root AGENTS.md, root
    CLAUDE.md and .claude/CLAUDE.md that exists but isn't the one file
    staged (see load_claude_code_project_files), and the other entries of
    the project's .claude/ folder. Names are spelled as on disk. Entries are
    named one level down, a folder with a trailing "/", rather than file by
    file. A symlinked entry is named without being followed. A .claude/
    that is a symlink, or that can't be read, is named as a whole, so
    nothing it points to is listed. Only names are read, never contents.

    Args:
        target: "kilo" or "claude-code".
        project_dir: the project folder.
        evals_path: the evals path the loader was given (kilo only).

    Returns:
        For kilo, the names sorted, cut at NOT_STAGED_LIMIT as below. For
        claude-code, project-relative paths, empty if nothing was left out: root files
        first, then .claude/CLAUDE.md, then the other .claude/ entries,
        sorted. Past NOT_STAGED_LIMIT of those others, the rest are counted
        in a last item, "and N more"; root files and .claude/CLAUDE.md are
        never cut.

    Raises:
        FileNotFoundError: project_dir doesn't exist.
        ValueError: project_dir isn't a folder. For kilo, a project
            load_project_files refuses names nothing instead.
    """
    return _not_staged(target, project_dir, evals_path)[0]


def files_not_staged_reasons(target: str, project_dir: Path, *, evals_path: Path | None = None) -> dict[str, str]:
    """Say why each name files_not_staged gives was left out, for the report.

    Args:
        target: "kilo" or "claude-code".
        project_dir: the project folder.
        evals_path: the evals path the loader was given (kilo only).

    Returns:
        {name: short reason} for every name files_not_staged lists except a
        last "and N more". The reasons are NOT_UTF8_REASON and
        SKILL_PATHS_REASON for kilo, and BESIDE_REASON, UNSAFE_REASON,
        REST_REASON and WHOLE_REASON for claude-code.

    Raises:
        FileNotFoundError, ValueError: as files_not_staged raises them.
    """
    return _not_staged(target, project_dir, evals_path)[1]


def not_staged_reason(
    target: str, project_dir: Path, relative: str, staged: dict[str, str], *, evals_path: Path | None = None
) -> str | None:
    """Say which rule leaves a project file out of what `target`'s loader staged, for validation.

    Args:
        target: "kilo" or "claude-code".
        project_dir: the project folder, as given to the loader.
        relative: the file, relative to the project, with "/" separators.
        staged: what the loader staged from project_dir.
        evals_path: the evals path the loader was given.

    Returns:
        None if the file was staged (for claude-code, also under the name
        AGENTS.md); otherwise the first rule that leaves it out, as a clause
        such as "it is a symlink, ...".

    Raises:
        FileNotFoundError, ValueError: project_dir is missing or isn't a folder.
    """
    root = _project_root(project_dir)
    path = PurePosixPath(relative)
    relative = path.as_posix()  # ./AGENTS.md and .//AGENTS.md name AGENTS.md
    if relative in staged or files_renamed(target, root, staged).get(relative) in staged:
        return None
    if relative.startswith("~") or path.is_absolute() or ".." in path.parts:
        return "it isn't a path inside the project"
    full = root / relative
    if not _is_present(full):
        return "the project has no such file"
    if _under(full, _evals_folder(evals_path)):
        return "it is under the evals path, whose files are never staged"
    if _linked_below(root, full):
        return "it is a symlink, or inside a symlinked folder, and nothing is staged through a link"
    if not full.is_file():
        return "it is a folder, not a file"
    excluded = next((part for part in path.parts if part in EXCLUDED_DIRS), None)
    if excluded:
        return f"it is inside {excluded}/, which is never staged"
    if not _is_safe_file(full):
        return f".env files are never staged, except {', '.join(sorted(SAFE_ENV_FILES)[:-1])} and {sorted(SAFE_ENV_FILES)[-1]}"
    if target == "claude-code":
        reasons = files_not_staged_reasons(target, root)
        if relative in reasons:
            return f"the claude-code loader stages one instruction file, and this one is {reasons[relative]}"
        return f"the claude-code loader stages only one of {', '.join(CLAUDE_CODE_INSTRUCTIONS[:-1])} and {DOT_CLAUDE_MD}, as {AGENTS_MD}"
    left_out = dict(_load_kilo(root, evals_path)[1])
    if relative in left_out:
        return f"it is {left_out[relative]}"
    if any(path.is_relative_to(folder) for folder in (*KILO_RULE_DIRS, *KILO_WORKFLOW_DIRS)):
        return "Kilo reads only the .md files directly in the rules and workflows folders"
    return (
        "the kilo loader stages only Kilo config, root AGENTS.md, CLAUDE.md and CONTEXT.md, the .kilo and .kilocode "
        "asset, rules and workflows folders, .claude/skills/, .agents/skills/, .kilocodemodes, the legacy "
        ".kilocoderules files, and files an instructions pattern matches"
    )


def set_external_directory(files: dict[str, str], action: str) -> dict[str, str]:
    """Return a copy of project files with each Kilo config's test policy set."""
    updated = dict(files)
    config_paths = [path for path in CONFIG_PATHS if path in updated]
    if not config_paths:
        raise ValueError("no Kilo config file found in project assets")
    for path in config_paths:
        config = parse_jsonc(updated[path])
        config.setdefault("permission", {})["external_directory"] = action
        updated[path] = json.dumps(config, indent=2)
    return updated


def _walk_files(root: Path, allowed_root: Path, evals: "_EvalsFolder | None" = None):
    """Yield (path relative to `root`, path) for each safe regular file under `root`, in path order.

    Nothing is reached through a symlink, and the excluded folders and the
    evals folder are never looked in.
    """
    allowed_root = allowed_root.resolve()
    if not root.is_dir() or _linked_below(allowed_root, root) or _under(root, evals):
        return
    if not root.resolve().is_relative_to(allowed_root):
        return
    for path in sorted(_tree_files(root, evals)):
        if _is_safe_file(path) and path.resolve().is_relative_to(allowed_root):
            yield path.relative_to(root).as_posix(), path


def _tree_files(directory: Path, evals: "_EvalsFolder | None"):
    for child in _folder_entries(directory, evals):
        if child.is_symlink():
            continue
        if child.is_dir():
            yield from _tree_files(child, evals)
        elif child.is_file():
            yield child


def _instruction_matches(root: Path, pattern: str, evals: "_EvalsFolder | None" = None) -> list[Path]:
    """The paths under `root` that an `instructions` pattern matches, as Kilo matches them (see UNEXPANDED_GLOB).

    The excluded folders and the evals folder are never looked in.

    Raises:
        ValueError: the pattern reaches outside the project (absolute, "~/" or ".."), uses
            glob syntax that isn't expanded here, or expands to more than
            MAX_BRACE_ALTERNATIVES patterns.
    """
    try:
        expanded = _expand_braces(pattern)
    except _TooManyAlternatives:
        raise ValueError(
            f"project instruction pattern {pattern!r} expands to more than {MAX_BRACE_ALTERNATIVES} patterns; "
            "split it into smaller ones"
        ) from None
    for candidate in [pattern, *expanded]:
        if candidate.startswith("~") or Path(candidate).is_absolute() or ".." in PurePosixPath(candidate).parts:
            raise ValueError(f"project instruction path must stay inside the project: {pattern!r}")
    if UNEXPANDED_GLOB.search(pattern):
        raise ValueError(
            f"project instruction pattern {pattern!r} uses glob syntax Whitney doesn't expand as Kilo does; "
            "use *, ?, **, [...], [!...] and {a,b} only"
        )
    matches = {}
    for alternative in expanded:
        parts = []
        for part in alternative.split("/"):
            # "a/**/**/b" matches what "a/**/b" does; walking each "**" again would only repeat the work.
            if part not in ("", ".") and not (part == "**" and parts[-1:] == ["**"]):
                parts.append(part)
        matches.update(dict.fromkeys(_glob_parts(root, parts, evals)))
    return list(matches)


class _TooManyAlternatives(Exception):
    """A brace pattern expands past MAX_BRACE_ALTERNATIVES."""


def _expand_braces(pattern: str) -> list[str]:
    """`pattern` with each {a,b} group expanded, innermost first; one without a closing brace is kept as written.

    Raises:
        _TooManyAlternatives: past MAX_BRACE_ALTERNATIVES, checked as they're made, so a huge pattern stops early.
    """
    start = None
    for index, char in enumerate(pattern):
        if char == "{":
            start = index
        elif char == "}" and start is not None:
            head, options, tail = pattern[:start], pattern[start + 1 : index].split(","), pattern[index + 1 :]
            expanded = []
            for option in options:
                expanded += _expand_braces(head + option + tail)
                if len(expanded) > MAX_BRACE_ALTERNATIVES:
                    raise _TooManyAlternatives
            return expanded
    return [pattern]


def _glob_parts(directory: Path, parts: list[str], evals: "_EvalsFolder | None" = None):
    """Yield what the path segments `parts` match below `directory`; "**" matches any number of segments."""
    if not parts:
        yield directory
        return
    head, rest = parts[0], parts[1:]
    if head == "**":
        yield from _glob_parts(directory, rest, evals)  # no segments
    flags = re.IGNORECASE if GLOB_CASE_BLIND else 0
    for child in _folder_entries(directory, evals):
        if head == "**":
            yield from _glob_parts(child, parts, evals)  # one segment more; a file yields itself only when nothing follows
        elif re.fullmatch(fnmatch.translate(head), child.name, flags):
            yield from _glob_parts(child, rest, evals)


def _folder_entries(directory: Path, evals: "_EvalsFolder | None" = None) -> list[Path]:
    """The entries of `directory`, leaving out the excluded folders and the evals folder, which are never looked in.

    A symlinked folder is never looked in: a link to / or to a parent would have ** walk the disk, or loop.
    """
    if directory.is_symlink() or not directory.is_dir():
        return []
    return [
        child for child in sorted(directory.iterdir())
        if child.name not in EXCLUDED_DIRS and not (evals is not None and child.is_dir() and _is_evals(child, evals))
    ]


def _is_evals(folder: Path, evals: "_EvalsFolder") -> bool:
    try:
        info = folder.stat()
    except OSError:
        return False
    return (info.st_dev, info.st_ino) == evals.identity


def _direct_md_files(root: Path, folder: str, evals: "_EvalsFolder | None") -> list[str]:
    """The project-relative paths of the files Kilo reads from a rules or workflows `folder`: regular *.md files directly in it."""
    directory = root / folder
    if not directory.is_dir() or _linked_below(root, directory) or _under(directory, evals):
        return []
    return [
        f"{folder}/{path.name}"
        for path in sorted(directory.iterdir())
        if path.name.endswith(".md") and _is_project_file(root, f"{folder}/{path.name}") and _is_safe_file(path)
    ]


def _linked_below(root: Path, path: Path) -> bool:
    """True if `path`, or a folder between `root` and it, is a symlink, so nothing staged is reached through a link."""
    while path != root and path != path.parent:  # the second stops at / for a path not under root
        if path.is_symlink():
            return True
        path = path.parent
    return False


def _is_kilo_file(root: Path, relative: str, evals) -> bool:
    """True if the Kilo loader may stage `relative`: a regular file in the project, reached through no symlink, not under the evals path."""
    path = root / relative
    return _is_project_file(root, relative) and not _linked_below(root, path) and not _under(path, evals)


def _is_present(path: Path) -> bool:
    return path.is_symlink() or path.exists()  # a broken link is still named


def _not_staged(target: str, project_dir: Path, evals_path: Path | None) -> tuple[list[str], dict[str, str]]:
    root = _project_root(project_dir)
    if target != "claude-code":
        try:
            left_out = _load_kilo(root, evals_path)[1]
        except ValueError:  # a project the loader refuses stages nothing, so the refusal, not this list, says why
            return [], {}
        return _capped([], left_out)
    source = _claude_code_source(root)
    first = [
        (_spelled(root, relative), _instruction_reason(root, relative, source))
        for relative in CLAUDE_CODE_ROOT_INSTRUCTIONS
        if relative != source and _is_present(root / relative)
    ]
    others = []
    for name, reason in sorted(_unstaged_claude_entries(root, source)):
        # .claude/CLAUDE.md, in any spelling, goes ahead of the cap: the report most needs to name it.
        (first if name.casefold() == DOT_CLAUDE_MD.casefold() else others).append((name, reason))
    return _capped(first, others)


def _capped(first: list[tuple[str, str]], others: list[tuple[str, str]]) -> tuple[list[str], dict[str, str]]:
    """The names and reasons files_not_staged gives: all of `first`, then `others` up to NOT_STAGED_LIMIT, counting the rest."""
    shown = first + others[:NOT_STAGED_LIMIT]
    names = [name for name, _ in shown]
    if len(others) > NOT_STAGED_LIMIT:
        names.append(f"and {len(others) - NOT_STAGED_LIMIT} more")
    return names, dict(shown)


def _instruction_reason(root: Path, relative: str, source: str | None) -> str:
    # Only reached for a file that isn't a symlinked .claude's: those are named as the folder.
    if _is_staged_claude_code_file(root, relative):
        return BESIDE_REASON.format(staged=_spelled(root, source))
    return UNSAFE_REASON


def _claude_code_source(root: Path) -> str | None:
    # A symlinked .claude is never followed, even to a folder inside the project: no staged file is reached by a link.
    linked = (root / CLAUDE_CODE_DIR).is_symlink()
    usable = (relative for relative in CLAUDE_CODE_INSTRUCTIONS if _is_staged_claude_code_file(root, relative, linked))
    return next(usable, None)  # lazily, so .claude/ is only looked in when no root file will do


def _is_staged_claude_code_file(root: Path, relative: str, linked: bool = False) -> bool:
    if not relative.startswith(f"{CLAUDE_CODE_DIR}/"):
        return _is_project_file(root, relative)
    if linked:
        return False
    try:
        return _is_project_file(root, relative)
    except OSError:  # Python 3.11 and 3.12 raise inside an unreadable .claude/; 3.14 returns False
        return False


def _unstaged_claude_entries(root: Path, source: str | None) -> list[tuple[str, str]]:
    folder = root / CLAUDE_CODE_DIR
    whole = [(f"{CLAUDE_CODE_DIR}/", WHOLE_REASON)]
    if folder.is_symlink():
        return whole
    if not folder.is_dir():
        return []
    try:  # an unreadable folder or entry is named, not allowed to stop the task being built
        entries = list(folder.iterdir())
        names = [entry.name for entry in entries]
        instruction = _on_disk_name(names, CLAUDE_MD) if _is_present(folder / CLAUDE_MD) else None
        items = []
        for entry in entries:
            if entry.name != instruction:
                suffix = "/" if not entry.is_symlink() and entry.is_dir() else ""
                items.append((f"{CLAUDE_CODE_DIR}/{entry.name}{suffix}", REST_REASON))
            elif source != DOT_CLAUDE_MD:
                items.append((f"{CLAUDE_CODE_DIR}/{entry.name}", _instruction_reason(root, DOT_CLAUDE_MD, source)))
        return items
    except OSError:
        return whole


def _spelled(root: Path, relative: str) -> str:
    # Names are looked up as the filesystem does, so on a case-insensitive disk "CLAUDE.md"
    # finds claude.md. The report names the file as the folder spells it.
    path = PurePosixPath(relative)
    try:
        names = [entry.name for entry in (root / path.parent).iterdir()]
    except OSError:  # a folder that can't be listed keeps the name the file was found by
        return relative
    return (path.parent / _on_disk_name(names, path.name)).as_posix()


def _on_disk_name(names: list[str], wanted: str) -> str:
    # Called only for a name the filesystem found, so one of `names` matches it, ignoring case.
    matches = [name for name in names if name.casefold() == wanted.casefold()]
    return wanted if wanted in matches else matches[0]


@dataclass(frozen=True)
class _EvalsFolder:
    """The evals path, resolved, and its identity on disk (device and inode)."""

    path: Path
    identity: tuple[int, int]


def _evals_folder(evals_path: Path | None) -> _EvalsFolder | None:
    """`evals_path` as an _EvalsFolder, or None if there is none: a folder that isn't there holds nothing."""
    if evals_path is None:
        return None
    path = Path(evals_path).expanduser().resolve()
    try:
        info = path.stat()
    except OSError:
        return None
    return _EvalsFolder(path, (info.st_dev, info.st_ino))


def _evals_root(root: Path, evals_path: Path | None) -> _EvalsFolder | None:
    """The evals path, for _under.

    Raises:
        ValueError: the project is inside it, so every file would be left out.
    """
    evals = _evals_folder(evals_path)
    if _under(root, evals):
        raise ValueError(f"project path {root} is inside the evals path {evals.path}, whose files are never staged")
    return evals


def _under(path: Path, evals: _EvalsFolder | None) -> bool:
    """True if `path` is the evals folder or inside it.

    Folders are compared by identity, not by spelling: on a case-insensitive
    disk Evals/ is evals/, and a project path may be spelled either way.
    """
    # A folder that isn't there, or can't be looked at, can't be the evals folder.
    return evals is not None and any(_is_evals(folder, evals) for folder in (path, *path.parents))


def _project_root(project_dir: Path) -> Path:
    root = Path(project_dir).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"project path is not a directory: {root}")
    return root


def _is_project_file(root: Path, relative: str) -> bool:
    path = root / relative
    return path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root)


def _is_safe_file(path: Path) -> bool:
    """False for a .env file, except SAFE_ENV_FILES.

    The excluded folders are kept out where the project is walked (_folder_entries) and in
    not_staged_reason, by their names inside the project only: the folders above it, which may
    be called node_modules or .venv, say nothing about its files.
    """
    name = path.name.lower()
    return not (name == ".env" or name.startswith(".env.")) or name in SAFE_ENV_FILES


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"template/config asset must be UTF-8 text: {path}") from error
