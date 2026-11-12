"""Collect safe text configuration and instruction files for eval workspaces.

There is one project loader per target: `load_project_files` for kilo and
`load_claude_code_project_files` for claude-code. `load_project_files_for`
picks between them.
"""

import fnmatch
import json
import os
import re
import sys
from collections import deque
from collections.abc import Mapping
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
# The claude-code loader stages what Claude Code reads, as its memory docs say
# (https://code.claude.com/docs/en/memory.md, read 2026-11-11; tasks/plan-phase5.md, decision 8):
# CLAUDE.md and .claude/CLAUDE.md together, and AGENTS.md and .claude/AGENTS.md only when neither
# CLAUDE.md is there. Each is staged under its own name. CLAUDE.local.md is personal and usually
# not committed, so it's never staged, only named. The project's skills and agents, .claude/skills/
# and .claude/agents/, are staged too: Claude Code 2.1.322 offers both (docs/spike-findings.md,
# "Phase 5 Task 36"). So is the `permissions` block of .claude/settings.json, alone in a file of
# that name: a rule there refuses a call (docs/spike-findings.md, "Permissions"). The file's other
# keys stay out, because hooks run commands on the machine running the evals (spec open question 8;
# decision 8). The rest of .claude/ stays out too, except a file an instruction file imports (IMPORT below).
CLAUDE_CODE_DIR = ".claude"
CLAUDE_CODE_ASSET_DIRS = (f"{CLAUDE_CODE_DIR}/skills", f"{CLAUDE_CODE_DIR}/agents")
CLAUDE_SETTINGS = f"{CLAUDE_CODE_DIR}/settings.json"
CLAUDE_PERMISSIONS = "permissions"  # the one key of CLAUDE_SETTINGS that is staged
# The keys of that block that are staged, each a list of rules. Its others, such as defaultMode and
# additionalDirectories, could let a run do more than the eval's own grant allows on the machine running it.
CLAUDE_RULE_LISTS = ("allow", "ask", "deny")
AGENTS_MD = "AGENTS.md"
CLAUDE_MD = "CLAUDE.md"
CLAUDE_LOCAL_MD = "CLAUDE.local.md"
DOT_CLAUDE_MD = f"{CLAUDE_CODE_DIR}/{CLAUDE_MD}"
DOT_AGENTS_MD = f"{CLAUDE_CODE_DIR}/{AGENTS_MD}"
CLAUDE_MD_FILES = (CLAUDE_MD, DOT_CLAUDE_MD)
AGENTS_MD_FILES = (AGENTS_MD, DOT_AGENTS_MD)
CLAUDE_CODE_INSTRUCTIONS = (*CLAUDE_MD_FILES, *AGENTS_MD_FILES)
# Root files the report names when they're left out, in this order.
CLAUDE_CODE_ROOT_FILES = (CLAUDE_MD, AGENTS_MD, CLAUDE_LOCAL_MD)
# files_not_staged names this many paths and counts the rest, so a large .claude/
# (Claude Code can keep whole worktrees there) can't swell the log or the report.
NOT_STAGED_LIMIT = 50
# Why files_not_staged_reasons says a name was left out.
IGNORED_REASON = "Claude Code doesn't read AGENTS.md beside a CLAUDE.md"
LOCAL_REASON = "personal and not committed; on its owner's machine it also stops Claude Code reading AGENTS.md"
UNSAFE_REASON = "a symlink or outside the project"
REST_REASON = f"rest of {CLAUDE_CODE_DIR}/"
WHOLE_REASON = "a symlink, or can't be read"
SETTINGS_KEY_REASON = f"only the {CLAUDE_PERMISSIONS} block of the settings is staged"
SETTINGS_REASONS = {"hooks": f"hooks run commands, so {SETTINGS_KEY_REASON}"}  # a key with a reason of its own
NOT_SETTINGS_REASON = f"isn't a JSON object, so its {CLAUDE_PERMISSIONS} can't be read"
RULE_LISTS_REASON = f"only the {', '.join(CLAUDE_RULE_LISTS[:-1])} and {CLAUDE_RULE_LISTS[-1]} lists of the {CLAUDE_PERMISSIONS} block are staged"
NOT_PERMISSIONS_REASON = f"its {CLAUDE_PERMISSIONS} block isn't an object"
# Claude Code expands `@path` imports in each instruction file it reads, relative to the file holding
# the import, up to four hops deep, and not in code spans or fenced blocks (its memory docs, read
# 2026-11-12; docs/spike-findings.md, "Phase 5 Task 31 follow-up: CLAUDE.md imports"). So the loader
# stages each imported file inside the project, by the rules above, or a run would give the model less
# than the project does. A path ends at the first space not escaped with a backslash.
IMPORT_MAX_HOPS = 4
IMPORT = re.compile(r"(?<!\S)@((?:\\ |\S)+)")
CODE_SPAN = re.compile(r"`[^`]*`")
# Claude Code skips the fenced blocks the CommonMark way: a fence of three or more backticks or tildes,
# indented at most three spaces, closes only on a line of the same character at least as long, with
# nothing after it. Block-level HTML comments are stripped before the text is sent (memory docs,
# 2026-11-12), so an import in one isn't followed: an assumption, since the docs don't say which comes
# first and no run has tried it. Indented code blocks are read like other text (docs/load-order.md, "Imports").
FENCE = re.compile(r"^ {0,3}(`{3,}(?!.*`)|~{3,})")
COMMENT_START = re.compile(r"^ {0,3}<!--")
COMMENT_END = "-->"
# Claude Code "skips a file over 4 MiB and loads none of it", and each import counts as a file (memory docs, 2026-11-12).
IMPORT_MAX_BYTES = 4 * 1024 * 1024
# Under .claude/, an import may stage only what the loader would stage there itself (CLAUDE.md, AGENTS.md,
# skills and agents) and Markdown files directly in it, which Claude Code reads as nothing else. The rest,
# settings.json and settings.local.json above all, Claude Code reads as its own configuration on the
# machine running the eval (`--setting-sources project`), so an import of it is left out, and the loader's
# cut-down settings are the only settings staged. Names are compared ignoring case, as macOS's disk does.
IMPORTABLE_IN_CLAUDE_DIR = tuple(f"{folder}/" for folder in CLAUDE_CODE_ASSET_DIRS)
CLAUDE_CONFIG_FILES = (".mcp.json",)
# Names a file holding a key or login usually has, beyond the .env files _is_safe_file keeps out. Real
# Claude Code would send such an import, but a staged file is copied into the run's log, so it's left out.
SECRET_NAMES = (
    "*.pem", "*.key", "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", ".npmrc", ".pypirc", ".netrc", ".envrc",
    "*.env", "credentials*", "*.p12", "*.pfx",
)
# Why files_not_staged_reasons says an import, named "<file>: @<path>", was left out.
OUTSIDE_IMPORT_REASON = (
    "outside the project: Claude Code loads it only once a person approves the project's external imports, "
    "which an eval's run never has"
)
ENV_IMPORT_REASON = "an imported .env file, which is never staged"
# Staging the file wouldn't help: the staged importer still names the original path, outside the run's project.
ABSOLUTE_IMPORT_REASON = (
    "written as an absolute path, which in a run's temporary project points outside it, "
    "so the model wouldn't get it; write the import relative to the file holding it"
)
CONFIG_IMPORT_REASON = (
    "Claude Code reads it as configuration, such as settings, hooks, commands or MCP servers, "
    "which an import never stages"
)
GIT_IMPORT_REASON = "inside .git/, which is never staged"
SECRET_IMPORT_REASON = "its name looks like a key's or a login's, and a staged file is copied into the run's log"
LARGE_IMPORT_REASON = "over 4 MiB, which Claude Code skips"
# The end of the message refusing an import reached through a symlink.
LINK_REFUSAL = (
    "Whitney never stages a file through a link, so the copy can't reach outside the project. "
    "Replace the link with the file it points to"
)


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



def _json_kind(value) -> str:
    """What a parsed JSON value is, in words, for a message about a config that isn't an object."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    return {list: "an array", str: "a string"}.get(type(value), "a number")


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
        if not isinstance(config, dict):
            raise ValueError(f"{relative} holds {_json_kind(config)}, not a JSON object, so Kilo can't read it as a config")
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
    """Load the instruction files, skills and agents Claude Code would read, each under its own name, and nothing else.

    Claude Code reads root CLAUDE.md and .claude/CLAUDE.md together, and
    AGENTS.md and .claude/AGENTS.md only when neither CLAUDE.md is there
    (see CLAUDE_CODE_DIR). A CLAUDE.md that is there but can't be staged
    still hides AGENTS.md, because Claude Code would see it. The same rules
    as load_project_files apply: a file that is a symlink, or that resolves
    outside the project, is skipped. A .claude folder that is itself a
    symlink is never followed. Every file under .claude/skills/ and
    .claude/agents/ (CLAUDE_CODE_ASSET_DIRS) is staged by the same rules,
    with .env files and the excluded folders left out as for Kilo; one that
    isn't UTF-8 is left out and named. files_not_staged names what was left
    out, and files_renamed a file whose name differs only in case. No Kilo
    config is needed, and a project with skills or agents needs no
    instruction file.

    .claude/settings.json is staged cut down to the allow, ask and deny
    lists of its `permissions` block, so the project's ask and deny rules
    apply and nothing else in the file does: its hooks never run. Claude
    Code itself ignores the allow rules of a folder nobody has trusted,
    which an eval's project never is (docs/spike-findings.md, "Phase 5
    Task 37"). A project with only that needs no
    instruction file either.

    Each file an instruction file imports with `@path`, and each file those
    import, up to IMPORT_MAX_HOPS deep, is staged too, by the same rules, so
    a CLAUDE.md that is only `@AGENTS.md` stages AGENTS.md beside it. An
    import only adds a file: one outside the project, of a file Claude Code
    reads as configuration (most of .claude/, .mcp.json), under .git/, of
    CLAUDE.local.md, a .env file, a secret-looking name or a file over 4 MiB
    is left out and named by files_not_staged, and the cut-down settings
    above are the only settings staged.

    Args:
        project_dir: the project folder. It is read, never written.
        evals_path: the evals root, whose files are never staged.

    Returns:
        {name: text} for each staged file: the instruction files, named as
        in CLAUDE_CODE_INSTRUCTIONS, then the files they import, then the
        skills' and agents' files, then CLAUDE_SETTINGS if the project's has
        a `permissions` block.

    Raises:
        FileNotFoundError: project_dir doesn't exist.
        ValueError: project_dir isn't a folder or is inside evals_path, has
            nothing to stage (the message names the files and says what is
            skipped, and names a CLAUDE.md that hides AGENTS.md but can't be
            staged), a file to stage is under evals_path, can't be read, or
            isn't UTF-8, or an imported file Claude Code would read can't be
            staged safely (the message names the file holding the import).
    """
    root = _project_root(project_dir)
    evals = _evals_root(root, evals_path)
    sources = _claude_code_sources(root)
    assets = {**_claude_code_assets(root, evals)[0], **_claude_code_settings(root, evals)[0]}
    if not sources and not assets:
        raise ValueError(
            f"project has no Claude Code instructions to stage ({', '.join(CLAUDE_CODE_INSTRUCTIONS)}; "
            "AGENTS.md is read only without a CLAUDE.md, and symlinks and files outside the project are skipped), "
            f"no skills or agents ({', '.join(f'{folder}/' for folder in CLAUDE_CODE_ASSET_DIRS)}), "
            f"and no {CLAUDE_PERMISSIONS} in {CLAUDE_SETTINGS}: "
            f"{root}{_hidden_by_claude_md(root)}"
        )
    for source in sources:
        if _under(root / source, evals):
            # Leaving it out would test instructions Claude Code wouldn't be given alone, so the run stops instead.
            raise ValueError(
                f"{root / source} is an instruction file to stage, but it is under the evals path {evals.path}, "
                "whose files are never staged"
            )
    instructions = {source: _read_instruction(root / source) for source in sources}
    imports = _claude_code_imports(root, instructions, evals)
    if imports.refused:  # Claude Code would read the file, so a run without it would test less than the project
        raise ValueError("; ".join(imports.refused))
    # Assets last: should an import ever name a skill, an agent or the settings, the loader's own copy wins.
    return {**instructions, **imports.files, **assets}


@dataclass
class _Imports:
    """What the instruction files' `@path` imports add: files to stage, (name, reason) for each left out, and refusals."""

    files: dict[str, str]
    left_out: list[tuple[str, str]]
    refused: list[str]


def _import_paths(text: str) -> list[str]:
    """The paths `text` imports with @path, outside code spans, fenced blocks and block-level HTML comments.

    Each is as written, with escaped spaces unescaped.
    """
    paths, fence, in_comment = [], None, False
    for line in text.splitlines():
        if in_comment:
            in_comment = COMMENT_END not in line
        elif fence is not None:
            if re.fullmatch(rf" {{0,3}}{fence[:1]}{{{len(fence)},}}[ \t]*", line):  # the same character, at least as long
                fence = None
        elif opened := FENCE.match(line):
            fence = opened.group(1)
        elif COMMENT_START.match(line):
            in_comment = COMMENT_END not in line  # CommonMark: a first line that also holds the end is the whole block
        else:
            paths += [match.replace("\\ ", " ") for match in IMPORT.findall(CODE_SPAN.sub(" ", line))]
    return paths


def _claude_code_imports(root: Path, instructions: Mapping[str, str], evals: "_EvalsFolder | None") -> _Imports:
    """Follow the imports of `instructions` ({name: text}, the staged instruction files) up to IMPORT_MAX_HOPS.

    An import Claude Code wouldn't load in a run, or that Whitney keeps out
    (see _import_left_out), is left out and named "<importing file>: @<path>".
    One Claude Code would read that Whitney can't stage safely (a symlink,
    inside an excluded folder, under the evals path, unreadable or not UTF-8)
    is refused. A path that names no file, or a folder, is passed over, as
    nothing is there to read.
    """
    found = _Imports({}, [], [])
    seen, queue = set(instructions), deque((name, text, 0) for name, text in instructions.items())
    while queue:
        importer, text, hops = queue.popleft()
        for written in _import_paths(text):
            path = _import_path(root, importer, written)
            if path is not None and (not _is_present(path) or (path.is_dir() and not path.is_symlink())):
                continue  # nothing there to read; a symlinked folder goes on to be refused
            reason = _import_left_out(root, path) or (ABSOLUTE_IMPORT_REASON if os.path.isabs(written) else None)
            if reason:
                found.left_out.append((f"{importer}: @{written}", reason))
                continue
            relative = path.relative_to(root).as_posix()
            if relative in seen:
                continue
            seen.add(relative)
            try:
                found.files[relative] = _imported_text(root, relative, evals)
            except _Refused as refusal:
                found.refused.append(f"{root / importer} imports @{written}, which Claude Code would read, but Whitney doesn't stage it: {refusal}")
                continue
            if hops + 1 < IMPORT_MAX_HOPS:  # a file at the last hop is staged, but its own imports aren't followed
                queue.append((relative, found.files[relative], hops + 1))
    return found


def _import_path(root: Path, importer: str, written: str) -> Path | None:
    """Where an import in `importer` points, normalised, or None if that is outside the project."""
    if written.startswith("~"):
        return None
    path = Path(os.path.normpath((root / importer).parent / written))  # an absolute `written` replaces the folder
    return path if path.is_relative_to(root) else None


def _import_left_out(root: Path, path: Path | None) -> str | None:
    """Why an import of `path`, a file or link in the project (None: outside it), is left out and named, or None to stage it."""
    if path is None:
        return OUTSIDE_IMPORT_REASON
    parts = [part.casefold() for part in path.relative_to(root).parts]
    relative = "/".join(parts)
    if parts[0] == ".git":
        return GIT_IMPORT_REASON
    if relative in CLAUDE_CONFIG_FILES or (parts[0] == CLAUDE_CODE_DIR and not _importable_in_claude_dir(relative)):
        return CONFIG_IMPORT_REASON
    if relative == CLAUDE_LOCAL_MD.casefold():
        return LOCAL_REASON
    if not _is_safe_file(path):
        return ENV_IMPORT_REASON
    if any(fnmatch.fnmatchcase(parts[-1], pattern) for pattern in SECRET_NAMES):
        return SECRET_IMPORT_REASON
    if path.lstat().st_size > IMPORT_MAX_BYTES:
        return LARGE_IMPORT_REASON
    return None


def _importable_in_claude_dir(relative: str) -> bool:
    """True for a casefolded path under .claude/ that an import may stage (see IMPORTABLE_IN_CLAUDE_DIR)."""
    if relative.startswith(IMPORTABLE_IN_CLAUDE_DIR):
        return True
    return relative.count("/") == 1 and relative.endswith(".md")


class _Refused(Exception):
    """An imported file Claude Code would read that the loader can't stage; the message says why."""


def _imported_text(root: Path, relative: str, evals: "_EvalsFolder | None") -> str:
    """The text of an imported file to stage.

    Raises:
        _Refused: it can't be staged safely.
    """
    path = root / relative
    if path.resolve() != path:  # root is resolved and `relative` normalised, so only a link makes them differ
        raise _Refused(_link_refusal(root, path))
    excluded = next((part for part in PurePosixPath(relative).parts if part in EXCLUDED_DIRS), None)
    if excluded:
        raise _Refused(f"it is inside {excluded}/, which is never staged")
    if _under(path, evals):
        raise _Refused("it is under the evals path, whose files are never staged")
    try:
        return _decoded(path)
    except UnicodeDecodeError:
        raise _Refused("it isn't UTF-8 text") from None
    except OSError as error:
        raise _Refused(f"it can't be read: {error.strerror}") from None


def _link_refusal(root: Path, path: Path) -> str:
    """Why an import reached through a symlink is refused, naming the link."""
    name = path.relative_to(root).as_posix()
    if not path.is_symlink():
        folder = next(parent for parent in path.parents if parent.is_symlink())
        return f"it is inside a symlinked folder, {folder.relative_to(root).as_posix()}. {LINK_REFUSAL}"
    if not path.exists():
        return f"it is a symlink to nothing, {name}. {LINK_REFUSAL}"
    if path.is_dir():
        return f"it is a symlink to a folder, {name}. {LINK_REFUSAL}"
    return "it is a symlink, or inside a symlinked folder. Replace the link with the file it points to"


def _decoded(path: Path) -> str:
    """The file's text, decoded as UTF-8.

    Raises:
        OSError: it can't be read.
        UnicodeDecodeError: it isn't UTF-8.
    """
    return path.read_bytes().decode()


def _claude_code_assets(root: Path, evals: "_EvalsFolder | None") -> tuple[dict[str, str], list[tuple[str, str]]]:
    """The files under CLAUDE_CODE_ASSET_DIRS to stage, and (name, reason) for each file or folder left out.

    A folder that is a symlink, or under one, holds nothing to stage, and
    files_not_staged names it. One that can't be read is left out whole and
    named, as is a file that isn't UTF-8.
    """
    files, left_out = {}, []
    for folder in CLAUDE_CODE_ASSET_DIRS:
        try:
            found = list(_walk_files(root / folder, root, evals))
        except OSError:
            left_out.append((f"{folder}/", WHOLE_REASON))
            continue
        for relative, path in found:
            try:
                files[f"{folder}/{relative}"] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                left_out.append((f"{folder}/{relative}", NOT_UTF8_REASON))
    return files, left_out


def claude_code_instructions(relative: str, staged: Mapping[str, str]) -> list[str]:
    """The staged Claude Code instruction files that stand for `relative`, for an eval file's source and variants.

    An eval file about a project's instructions, such as agents-md.json,
    names AGENTS.md, but a claude-code run stages whichever of
    CLAUDE_CODE_INSTRUCTIONS Claude Code would read. So AGENTS.md stands for
    all of the instruction files staged: the baseline that removes AGENTS.md
    removes them all. Every other name means that file only, so a variant
    can remove one file and an eval file about one is checked against it
    (tasks/plan-phase5.md, decision 8).

    Args:
        relative: a project-relative path, such as an eval file's source.
        staged: what the claude-code loader staged.

    Returns:
        The staged instruction files, in CLAUDE_CODE_INSTRUCTIONS order, or
        [] if `relative` isn't AGENTS.md.
    """
    if relative != AGENTS_MD:
        return []
    return [name for name in CLAUDE_CODE_INSTRUCTIONS if name in staged]


def without_instructions(target: str, files: Mapping[str, str]) -> dict[str, str]:
    """`files` without the project's instructions, for a baseline that names no eval file's variants.

    Args:
        target: "kilo" or "claude-code".
        files: staged files, project-relative path to text.

    Returns:
        A copy without AGENTS.md, and for claude-code without every staged
        file of CLAUDE_CODE_INSTRUCTIONS (see claude_code_instructions).
    """
    dropped = claude_code_instructions(AGENTS_MD, files) if target == "claude-code" else [AGENTS_MD]
    return {path: text for path, text in files.items() if path not in dropped}


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

    Only claude-code renames, and only by case: names are looked up as the
    filesystem does, so on a case-insensitive disk a lowercase claude.md is
    staged as CLAUDE.md, and named here as the folder spells it. The
    project's own files are never renamed or changed, only the copy.

    Args:
        target: "kilo" or "claude-code".
        project_dir: the project folder, as given to the loader.
        staged: the files the variant stages, after any it drops; a
            baseline without the instruction files has no rename to name.

    Returns:
        {project-relative path as spelled on disk: name it was staged as}, empty if nothing was renamed.

    Raises:
        FileNotFoundError, ValueError: as files_not_staged raises them (claude-code only).
    """
    if target != "claude-code":
        return {}
    root = _project_root(project_dir)
    spelled = {source: _spelled(root, source) for source in _claude_code_sources(root) if source in staged}
    return {name: source for source, name in spelled.items() if name != source}


def files_not_staged(target: str, project_dir: Path, *, evals_path: Path | None = None) -> list[str]:
    """Name what the project holds for `target`'s harness that its loader leaves out, for the report.

    For kilo: each file in a skill folder that isn't UTF-8 text, and each
    config's skills.paths, named "<config>: skills.paths". For claude-code: each of
    CLAUDE_CODE_ROOT_FILES, .claude/CLAUDE.md and .claude/AGENTS.md that
    exists but wasn't staged (see load_claude_code_project_files), each
    `@path` import left out, named "<importing file>: @<path>" as written,
    and the other entries of the project's .claude/ folder. Names are spelled as on disk. Entries are
    named one level down, a folder with a trailing "/", rather than file by
    file. A symlinked entry is named without being followed. A .claude/
    that is a symlink, or that can't be read, is named as a whole, so
    nothing it points to is listed. .claude/skills/ and .claude/agents/ are
    staged, so they're named only for what was left out: a file that isn't
    UTF-8 text by its path, and a folder that is a symlink or can't be read.
    Outside those two folders only names are read, never contents.

    Args:
        target: "kilo" or "claude-code".
        project_dir: the project folder.
        evals_path: the evals path the loader was given.

    Returns:
        For kilo, the names sorted, cut at NOT_STAGED_LIMIT as below. For
        claude-code, project-relative paths, empty if nothing was left out: root files
        first, then the imports left out, then .claude/'s instruction files, then the other .claude/
        entries, sorted. Past NOT_STAGED_LIMIT of those others, the rest are
        counted in a last item, "and N more"; root files, imports and .claude/'s
        instruction files are never cut.

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
        evals_path: the evals path the loader was given.

    Returns:
        {name: short reason} for every name files_not_staged lists except a
        last "and N more". The reasons are NOT_UTF8_REASON and
        SKILL_PATHS_REASON for kilo, and IGNORED_REASON, LOCAL_REASON,
        UNSAFE_REASON, REST_REASON, WHOLE_REASON, NOT_UTF8_REASON, OUTSIDE_IMPORT_REASON,
        ABSOLUTE_IMPORT_REASON, ENV_IMPORT_REASON, CONFIG_IMPORT_REASON, GIT_IMPORT_REASON, SECRET_IMPORT_REASON and
        LARGE_IMPORT_REASON for claude-code.

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
        None if the file was staged (for claude-code, also under a name
        differing only in case, and for AGENTS.md when any instruction file
        was staged: see claude_code_instructions); otherwise the
        first rule that leaves it out, as a clause such as "it is a symlink, ...".

    Raises:
        FileNotFoundError, ValueError: project_dir is missing or isn't a folder.
    """
    root = _project_root(project_dir)
    path = PurePosixPath(relative)
    relative = path.as_posix()  # ./AGENTS.md and .//AGENTS.md name AGENTS.md
    if relative in staged or files_renamed(target, root, staged).get(relative) in staged:
        return None
    if target == "claude-code" and claude_code_instructions(relative, staged):
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
        reasons = files_not_staged_reasons(target, root)  # a file under the evals path was answered above
        if relative in reasons:
            return f"the claude-code loader stages only what Claude Code reads, and this file is left out: {reasons[relative]}"
        return (
            f"the claude-code loader stages only {', '.join(CLAUDE_CODE_INSTRUCTIONS)}, "
            f"the files under {' and '.join(f'{folder}/' for folder in CLAUDE_CODE_ASSET_DIRS)}, "
            f"and the {CLAUDE_PERMISSIONS} block of {CLAUDE_SETTINGS}"
        )
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
    sources = _claude_code_sources(root)
    imports = _claude_code_imports(root, _readable(root, sources), _evals_folder(evals_path))
    first = [
        (_spelled(root, relative), _instruction_reason(root, relative))
        for relative in CLAUDE_CODE_ROOT_FILES
        if relative not in sources and relative not in imports.files and _is_present(root / relative)
    ]
    first += imports.left_out  # in the order written: the report most needs to name them, so the cap never hides one
    others = []
    folder_instructions = {name.casefold() for name in (DOT_CLAUDE_MD, DOT_AGENTS_MD)}
    entries = [entry for entry in _unstaged_claude_entries(root, sources) if entry[0] not in imports.files]
    if not any(name == f"{CLAUDE_CODE_DIR}/" for name, _ in entries):  # a .claude/ named whole says it all
        evals = _evals_folder(evals_path)
        entries += _claude_code_assets(root, evals)[1]
        staged, about_settings = _claude_code_settings(root, evals)
        if staged or about_settings:  # the file is staged, or left out for a reason of its own
            entries = [entry for entry in entries if entry[0] != CLAUDE_SETTINGS] + about_settings
    for name, reason in sorted(entries):
        # .claude/'s instruction files, in any spelling, go ahead of the cap: the report most needs to name them.
        (first if name.casefold() in folder_instructions else others).append((name, reason))
    return _capped(first, others)


def _readable(root: Path, sources: list[str]) -> dict[str, str]:
    """{name: text} for each of `sources` that can be read as UTF-8; the loader, not the report, says why one can't."""
    return {source: text for source in sources if (text := _utf8_or_none(root / source)) is not None}


def _utf8_or_none(path: Path) -> str | None:
    """The file's text, or None if it can't be read or isn't UTF-8."""
    try:
        return _decoded(path)
    except (OSError, UnicodeDecodeError):
        return None


def _capped(first: list[tuple[str, str]], others: list[tuple[str, str]]) -> tuple[list[str], dict[str, str]]:
    """The names and reasons files_not_staged gives: all of `first`, then `others` up to NOT_STAGED_LIMIT, counting the rest."""
    shown = first + others[:NOT_STAGED_LIMIT]
    names = [name for name, _ in shown]
    if len(others) > NOT_STAGED_LIMIT:
        names.append(f"and {len(others) - NOT_STAGED_LIMIT} more")
    return names, dict(shown)


def _instruction_reason(root: Path, relative: str) -> str:
    # Only reached for a file that wasn't staged, and isn't a symlinked .claude's: those are named as the folder.
    if relative == CLAUDE_LOCAL_MD:
        return LOCAL_REASON
    if _is_staged_claude_code_file(root, relative):
        return IGNORED_REASON  # stageable but not staged: an AGENTS.md beside a CLAUDE.md
    return UNSAFE_REASON


def _claude_code_sources(root: Path) -> list[str]:
    """The instruction files to stage: the group Claude Code reads, less any that can't be staged safely."""
    # A symlinked .claude is never followed, even to a folder inside the project: no staged file is reached by a link.
    linked = (root / CLAUDE_CODE_DIR).is_symlink()
    return [relative for relative in _claude_code_group(root) if _is_staged_claude_code_file(root, relative, linked)]


def _claude_code_group(root: Path) -> tuple[str, ...]:
    """CLAUDE_MD_FILES if either is there, even one that can't be staged, as Claude Code would see it; else AGENTS_MD_FILES."""
    return CLAUDE_MD_FILES if any(_is_there(root / relative) for relative in CLAUDE_MD_FILES) else AGENTS_MD_FILES


def _hidden_by_claude_md(root: Path) -> str:
    """For the no-instructions error: the CLAUDE.md files that hide AGENTS.md but can't be staged, or ""."""
    if _claude_code_group(root) != CLAUDE_MD_FILES:
        return ""
    linked = (root / CLAUDE_CODE_DIR).is_symlink()
    hidden = [relative for relative in CLAUDE_MD_FILES if _is_there(root / relative)]
    names = " and ".join(_spelled(root, relative) for relative in hidden)
    reasons = "; ".join(
        f"{CLAUDE_CODE_DIR}/ is a symlink" if linked and relative == DOT_CLAUDE_MD else UNSAFE_REASON
        for relative in hidden
    )
    return (
        f". {names} would be read by Claude Code instead of AGENTS.md, but Whitney doesn't stage "
        f"{'it' if len(hidden) == 1 else 'them'} ({reasons}). Replace a link with the file it points to, or remove it."
    )


def _read_instruction(path: Path) -> str:
    try:
        return _read_text(path)
    except OSError as error:  # an unreadable file would otherwise stop the run with a bare PermissionError
        raise ValueError(f"{path} is an instruction file to stage, but it can't be read: {error.strerror}") from error


def _is_there(path: Path) -> bool:
    try:
        return _is_present(path)
    except OSError:  # inside a .claude/ that can't be read, which Claude Code couldn't read either
        return False


def _claude_code_settings(root: Path, evals: "_EvalsFolder | None") -> tuple[dict[str, str], list[tuple[str, str]]]:
    """The project's settings cut down to the rule lists of its `permissions` block, and (name, reason) for what that leaves out.

    Nothing is staged from a file that is a symlink, under the evals path,
    or has no `permissions` key: _unstaged_claude_entries names such a file
    with the rest of .claude/. One that can't be read as a JSON object, or
    whose `permissions` isn't one, is left out and named here with why.
    Each other key of a staged file is named as "<file> (<key>)", and each
    key of the block that isn't a list of rules (CLAUDE_RULE_LISTS) as
    "<file> (permissions.<key>)".
    """
    path = root / CLAUDE_SETTINGS
    linked = (root / CLAUDE_CODE_DIR).is_symlink()
    if not _is_staged_claude_code_file(root, CLAUDE_SETTINGS, linked) or _under(path, evals):
        return {}, []
    unreadable: tuple[dict[str, str], list[tuple[str, str]]] = ({}, [(CLAUDE_SETTINGS, NOT_SETTINGS_REASON)])
    try:
        settings = json.loads(path.read_bytes())
    except (OSError, ValueError):  # not UTF-8 and not JSON are both a ValueError
        return unreadable
    if not isinstance(settings, dict):
        return unreadable
    if CLAUDE_PERMISSIONS not in settings:
        return {}, []
    permissions = settings[CLAUDE_PERMISSIONS]
    if not isinstance(permissions, dict):
        return {}, [(CLAUDE_SETTINGS, NOT_PERMISSIONS_REASON)]
    rules = {key: value for key, value in permissions.items() if key in CLAUDE_RULE_LISTS and isinstance(value, list)}
    left_out = [
        (f"{CLAUDE_SETTINGS} ({key})", SETTINGS_REASONS.get(key, SETTINGS_KEY_REASON))
        for key in settings if key != CLAUDE_PERMISSIONS
    ]
    left_out += [(f"{CLAUDE_SETTINGS} ({CLAUDE_PERMISSIONS}.{key})", RULE_LISTS_REASON) for key in permissions if key not in rules]
    return {CLAUDE_SETTINGS: json.dumps({CLAUDE_PERMISSIONS: rules}, indent=2) + "\n"}, left_out


def _is_staged_claude_code_file(root: Path, relative: str, linked: bool = False) -> bool:
    if not relative.startswith(f"{CLAUDE_CODE_DIR}/"):
        return _is_project_file(root, relative)
    if linked:
        return False
    try:
        return _is_project_file(root, relative)
    except OSError:  # Python 3.11 and 3.12 raise inside an unreadable .claude/; 3.14 returns False
        return False


def _unstaged_claude_entries(root: Path, sources: list[str]) -> list[tuple[str, str]]:
    folder = root / CLAUDE_CODE_DIR
    whole = [(f"{CLAUDE_CODE_DIR}/", WHOLE_REASON)]
    if folder.is_symlink():
        return whole
    if not folder.is_dir():
        return []
    try:  # an unreadable folder or entry is named, not allowed to stop the task being built
        entries = list(folder.iterdir())
        names = [entry.name for entry in entries]
        instructions = {
            _on_disk_name(names, PurePosixPath(relative).name): relative
            for relative in (DOT_CLAUDE_MD, DOT_AGENTS_MD)
            if _is_present(root / relative)
        }
        items = []
        for entry in entries:
            relative = instructions.get(entry.name)
            if relative is None:
                is_folder = not entry.is_symlink() and entry.is_dir()
                name = f"{CLAUDE_CODE_DIR}/{entry.name}"
                if name not in CLAUDE_CODE_ASSET_DIRS:
                    items.append((f"{name}{'/' if is_folder else ''}", REST_REASON))
                elif not is_folder:  # a skills or agents that is a link, or a file, holds nothing to stage
                    items.append((name, UNSAFE_REASON if entry.is_symlink() else REST_REASON))
            elif relative not in sources:
                items.append((f"{CLAUDE_CODE_DIR}/{entry.name}", _instruction_reason(root, relative)))
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
