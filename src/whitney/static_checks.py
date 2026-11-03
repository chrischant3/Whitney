"""Model-free checks that a template is shaped the way the evals assume.

Run against the shipped templates, or a template folder for one target
(exit code 1 if anything is wrong):

    python -m whitney.static_checks
    python -m whitney.static_checks --target claude-code path/to/template
"""

import argparse
import json
import re
import sys
from pathlib import Path

CORE_RULE = "Never commit .env files or secrets"
SAFE_EXTERNAL_DIRECTORY = {"ask", "deny"}
SHIPPED_TEMPLATES = Path(__file__).resolve().parents[2] / "template"
TARGETS = ("kilo", "claude-code")


def check_template(template_dir: Path, target: str = "kilo") -> list[str]:
    """Return every problem found in a template folder; an empty list means it passes.

    Every target:
    - `AGENTS.md` exists at the root (where both harnesses load it) and still
      contains the core rule as its own list item, since teams edit this file.

    The `kilo` target also:
    - no `.kilo/AGENTS.md`, which Kilo doesn't load on its own (docs/load-order.md);
    - `kilo.jsonc` exists, parses as JSONC, and sets
      `permission.external_directory` to "ask" or "deny" (for the pattern
      form, the "*" entry).

    Raises:
        ValueError: target isn't one of TARGETS.
    """
    if target not in TARGETS:
        raise ValueError(f"target must be one of {', '.join(TARGETS)}; got {target!r}")
    template_dir = Path(template_dir)
    problems = _check_agents_md(template_dir)
    if target == "kilo":
        problems += _check_dot_kilo(template_dir) + _check_config(template_dir)
    return problems


def _check_agents_md(template_dir: Path) -> list[str]:
    agents_md = template_dir / "AGENTS.md"
    if not agents_md.is_file():
        return [f"{agents_md}: missing; the harness loads rules from AGENTS.md at the project root"]
    if not _has_list_item(agents_md.read_text(), CORE_RULE):
        return [f"{agents_md}: core rule '- {CORE_RULE}' has been removed"]
    return []


def _check_dot_kilo(template_dir: Path) -> list[str]:
    stray = template_dir / ".kilo" / "AGENTS.md"
    if stray.is_file():
        return [f"{stray}: Kilo doesn't load .kilo/AGENTS.md on its own; move its rules to the root AGENTS.md"]
    return []


def _has_list_item(markdown: str, rule: str) -> bool:
    """True if `rule` is a whole Markdown list item (any bullet, any case)."""
    wanted = rule.casefold()
    for line in markdown.splitlines():
        match = re.match(r"\s*[-*+]\s+(.*?)\s*$", line)
        if match and match.group(1).casefold() == wanted:
            return True
    return False


def _check_config(template_dir: Path) -> list[str]:
    config_path = template_dir / "kilo.jsonc"
    if not config_path.is_file():
        return [f"{config_path}: missing"]
    try:
        config = parse_jsonc(config_path.read_text())
    except json.JSONDecodeError as e:
        return [f"{config_path}: can't parse as JSONC ({e})"]

    setting = (config.get("permission") or {}).get("external_directory")
    action = setting.get("*") if isinstance(setting, dict) else setting
    if action not in SAFE_EXTERNAL_DIRECTORY:
        return [
            f"{config_path}: permission.external_directory must be \"ask\" or \"deny\" "
            f"(found {json.dumps(setting)})"
        ]
    return []


def parse_jsonc(text: str):
    """Parse JSON with comments and trailing commas, as Kilo's kilo.jsonc allows.

    Two string-aware passes (strip comments, then trailing commas) so values
    such as "https://..." are never mistaken for comments.
    """
    return json.loads(_drop_trailing_commas(_drop_comments(text)))


def _string_end(text: str, start: int) -> int:
    """Index just past the string literal that opens at `start`."""
    i = start + 1
    while i < len(text) and text[i] != '"':
        i += 2 if text[i] == "\\" else 1
    return i + 1


def _drop_comments(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        if text[i] == '"':
            end = _string_end(text, i)
            out.append(text[i:end])
            i = end
        elif text.startswith("//", i):
            newline = text.find("\n", i)
            i = len(text) if newline == -1 else newline
        elif text.startswith("/*", i):
            close = text.find("*/", i + 2)
            i = len(text) if close == -1 else close + 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _drop_trailing_commas(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        if text[i] == '"':
            end = _string_end(text, i)
            out.append(text[i:end])
            i = end
            continue
        if text[i] == "," and text[i + 1 :].lstrip()[:1] in ("}", "]"):
            i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: check each folder given as --target, or every shipped template; return 1 on any problem."""
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("folders", nargs="*", help="template folders to check (default: every shipped template)")
    p.add_argument("--target", choices=TARGETS, default="kilo", help="which harness the folders are for")
    args = p.parse_args(argv)

    if args.folders:
        checks = [(Path(folder), args.target) for folder in args.folders]
    else:
        checks = [(SHIPPED_TEMPLATES / target, target) for target in TARGETS]
    failed = False
    for folder, target in checks:
        problems = check_template(folder, target)
        for problem in problems:
            print(problem)
        print(f"{_display(folder)} ({target}): {'OK' if not problems else f'{len(problems)} problem(s)'}")
        failed = failed or bool(problems)
    return 1 if failed else 0


def _display(folder: Path) -> str:
    """The folder relative to the working directory when it's inside it, so output reads template/kilo."""
    try:
        return str(folder.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(folder)


if __name__ == "__main__":
    sys.exit(main())
