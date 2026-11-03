"""Collect safe text configuration and instruction files for eval workspaces."""

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
    root = Path(project_dir).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"project path is not a directory: {root}")

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
        instructions = config.get("instructions", [])
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
