"""Collect safe text configuration and instruction files for eval workspaces.

There is one project loader per target: `load_project_files` for kilo and
`load_claude_code_project_files` for claude-code. `load_project_files_for`
picks between them.
"""

import json
from pathlib import Path

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
EXCLUDED_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache", ".tox"}
SAFE_ENV_FILES = {".env.example", ".env.sample", ".env.template"}
CONNECTION_CONFIG_KEYS = {"provider", "enabled_providers", "disabled_providers", "mcp", "plugin"}
# Claude Code reads these as project instructions (tasks/plan-phase5.md, decision 5),
# though in one run on 2026-11-06 it ignored AGENTS.md when .claude/CLAUDE.md was there
# too (docs/results/2026-11-06-claude-code-canary.md). The rest of .claude/ stays out:
# settings.json can hold hooks, which run commands, and which of its keys are safe to
# copy is still open (spec open question 8).
CLAUDE_CODE_DIR = ".claude"
CLAUDE_CODE_ROOT_INSTRUCTIONS = ("AGENTS.md", "CLAUDE.md")
CLAUDE_CODE_INSTRUCTIONS = (*CLAUDE_CODE_ROOT_INSTRUCTIONS, f"{CLAUDE_CODE_DIR}/CLAUDE.md")
# files_not_staged names this many paths and counts the rest, so a large .claude/
# (Claude Code can keep whole worktrees there) can't swell the log or the report.
NOT_STAGED_LIMIT = 50


def load_template_files(template_dir: Path) -> dict[str, str]:
    """Load every UTF-8 text file under a template, preserving relative paths."""
    root = Path(template_dir).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"template path is not a directory: {root}")
    return {relative: _read_text(path) for relative, path in _walk_files(root, root)}


def load_project_files(project_dir: Path) -> dict[str, str]:
    """Load a project's Kilo config and instruction assets, never its source tree.

    Provider connections and runtime integrations remain sourced from the user's
    global Kilo config. Project assets are copied into a temporary eval project.
    """
    root = _project_root(project_dir)
    config_paths = [relative for relative in CONFIG_PATHS if _is_project_file(root, relative)]
    if not config_paths:
        raise ValueError(f"project has no Kilo config ({', '.join(CONFIG_PATHS)}): {root}")

    paths = {relative for relative in (*ROOT_INSTRUCTIONS, *config_paths) if _is_project_file(root, relative)}
    for config_dir in KILO_CONFIG_DIRS:
        for directory in KILO_ASSET_DIRS:
            paths.update(
                f"{config_dir}/{directory}/{relative}"
                for relative, _ in _walk_files(root / config_dir / directory, root)
            )

    files = {relative: _read_text(root / relative) for relative in sorted(paths)}
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
        files[relative] = json.dumps(config, indent=2)

    for pattern in sorted(instruction_patterns):
        candidate = Path(pattern)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"project instruction path must stay inside the project: {pattern!r}")
        for match in root.glob(pattern):
            if match.is_file() and not match.is_symlink() and _is_safe_file(match):
                resolved = match.resolve()
                if resolved.is_relative_to(root):
                    files[match.relative_to(root).as_posix()] = _read_text(match)

    return files


def load_claude_code_project_files(project_dir: Path) -> dict[str, str]:
    """Load the instruction files Claude Code reads from a project, and nothing else.

    Loads root AGENTS.md and CLAUDE.md and .claude/CLAUDE.md, under the same
    rules as load_project_files: a file that is a symlink, or that resolves
    outside the project, is left out. A .claude folder that is itself a
    symlink is never followed, even to a folder inside the project. No Kilo
    config is needed.

    Args:
        project_dir: the project folder. It is read, never written.

    Returns:
        Each instruction file found, project-relative path to its text.

    Raises:
        FileNotFoundError: project_dir doesn't exist.
        ValueError: project_dir isn't a folder, holds none of the files
            (the message names them and says what is skipped), or a file
            isn't UTF-8.
    """
    root = _project_root(project_dir)
    paths = _claude_code_paths(root)
    if not paths:
        raise ValueError(
            f"project has no Claude Code instructions ({', '.join(CLAUDE_CODE_INSTRUCTIONS)}; "
            f"symlinks and files outside the project are skipped): {root}"
        )
    return {relative: _read_text(root / relative) for relative in paths}


def load_project_files_for(target: str, project_dir: Path) -> dict[str, str]:
    """Load a project with the loader for `target`'s harness.

    Args:
        target: "kilo" or "claude-code", as whitney.targets names them.
        project_dir: the project folder. It is read, never written.

    Returns:
        What load_project_files (kilo) or load_claude_code_project_files
        (claude-code) returns.

    Raises:
        KeyError: an unknown target.
        FileNotFoundError, ValueError: as the chosen loader raises them.
    """
    loaders = {"kilo": load_project_files, "claude-code": load_claude_code_project_files}
    return loaders[target](project_dir)


def files_not_staged(target: str, project_dir: Path) -> list[str]:
    """Name what the project holds for `target`'s harness that its loader leaves out, for the report.

    Only claude-code leaves anything out today: a root AGENTS.md or CLAUDE.md
    skipped because it isn't a regular file inside the project, and the
    entries of the project's .claude/ folder apart from a staged
    .claude/CLAUDE.md. Entries are named one level down, a folder with a
    trailing "/", rather than file by file. A symlinked entry is named
    without being followed. A .claude/ that is a symlink, or that can't be
    read, is named as a whole, so nothing it points to is listed. Only names
    are read, never contents.

    Args:
        target: "kilo" or "claude-code".
        project_dir: the project folder.

    Returns:
        Project-relative paths, empty if nothing was left out: skipped root
        files first, then the sorted .claude/ entries. Past NOT_STAGED_LIMIT
        entries, the rest are counted in a last item, "and N more"; skipped
        root files are never cut.

    Raises:
        FileNotFoundError: project_dir doesn't exist (claude-code only).
        ValueError: project_dir isn't a folder (claude-code only).
    """
    if target != "claude-code":
        return []
    root = _project_root(project_dir)
    staged = _claude_code_paths(root)
    skipped = [
        relative
        for relative in CLAUDE_CODE_ROOT_INSTRUCTIONS
        if relative not in staged and ((root / relative).is_symlink() or (root / relative).exists())
    ]
    entries = sorted(_unstaged_claude_entries(root, staged))
    if len(entries) > NOT_STAGED_LIMIT:
        entries = [*entries[:NOT_STAGED_LIMIT], f"and {len(entries) - NOT_STAGED_LIMIT} more"]
    return [*skipped, *entries]


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


def _walk_files(root: Path, allowed_root: Path):
    if not root.is_dir():
        return
    allowed_root = allowed_root.resolve()
    if not root.resolve().is_relative_to(allowed_root):
        return
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file() or not _is_safe_file(path):
            continue
        resolved = path.resolve()
        if resolved.is_relative_to(allowed_root):
            yield path.relative_to(root).as_posix(), path


def _claude_code_paths(root: Path) -> list[str]:
    # A symlinked .claude is never followed: `.claude -> .` would stage CLAUDE.md twice.
    linked = (root / CLAUDE_CODE_DIR).is_symlink()
    return [relative for relative in CLAUDE_CODE_INSTRUCTIONS if _is_staged_claude_code_file(root, relative, linked)]


def _is_staged_claude_code_file(root: Path, relative: str, linked: bool) -> bool:
    if not relative.startswith(f"{CLAUDE_CODE_DIR}/"):
        return _is_project_file(root, relative)
    if linked:
        return False
    try:
        return _is_project_file(root, relative)
    except OSError:  # Python 3.11 and 3.12 raise inside an unreadable .claude/; 3.14 returns False
        return False


def _unstaged_claude_entries(root: Path, staged: list[str]) -> list[str]:
    folder = root / CLAUDE_CODE_DIR
    if folder.is_symlink():
        return [f"{CLAUDE_CODE_DIR}/"]
    if not folder.is_dir():
        return []
    try:
        entries = list(folder.iterdir())
    except OSError:  # an unreadable folder is named, not allowed to stop the task being built
        return [f"{CLAUDE_CODE_DIR}/"]
    # By file, not by name: on a case-insensitive disk .claude/claude.md is staged as .claude/CLAUDE.md.
    staged_inside = [root / relative for relative in staged if relative.startswith(f"{CLAUDE_CODE_DIR}/")]
    return [
        f"{CLAUDE_CODE_DIR}/{entry.name}" + ("/" if not entry.is_symlink() and entry.is_dir() else "")
        for entry in entries
        if entry.is_symlink() or not any(entry.samefile(path) for path in staged_inside)
    ]


def _project_root(project_dir: Path) -> Path:
    root = Path(project_dir).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"project path is not a directory: {root}")
    return root


def _is_project_file(root: Path, relative: str) -> bool:
    path = root / relative
    return path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root)


def _is_safe_file(path: Path) -> bool:
    if any(part in EXCLUDED_DIRS for part in path.parts):
        return False
    name = path.name.lower()
    return not (name == ".env" or name.startswith(".env.")) or name in SAFE_ENV_FILES


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"template/config asset must be UTF-8 text: {path}") from error
