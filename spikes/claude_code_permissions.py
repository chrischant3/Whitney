"""Spike, Phase 5 Task 37: what Claude Code emits for a rule in the project's `.claude/settings.json`.

The 2026-11-11 spike saw a project `deny` and `ask` refuse `git push` but
kept no streams. This runs those cases again, with a rule set to `allow` and a
run that never tries the command, and saves each as a sanitised fixture. The
runner, isolation and sanitising are claude_code_shapes.py's.

Run from the repo root, with ANTHROPIC_API_KEY set. Each case is one real
model call, a cent or two on Haiku.

    .venv/bin/python spikes/claude_code_permissions.py --out /tmp/whitney-cc-permissions
    .venv/bin/python spikes/claude_code_permissions.py --out /tmp/whitney-cc-permissions --fixtures tests/data/claude-code-events --rewrite

The project's settings file holds only a `permissions` block here, as the
file Whitney stages does (whitney.project_files). The eval's own grant
(`--settings`, allow Bash, Edit and Write) applies alongside it.
"""

import json

import claude_code_shapes
from claude_code_shapes import READ_PROMPT, Case

RULE = "Bash(git push:*)"
PUSH_PROMPT = "Run `git push origin main` with the Bash tool once, then tell me in one sentence what happened. Do nothing else."


def settings(**permissions: list[str]) -> dict[str, str]:
    """A project's `.claude/settings.json` holding only this `permissions` block."""
    return {".claude/settings.json": json.dumps({"permissions": permissions}, indent=2) + "\n"}


CASES = [
    Case("settings-deny-held", f"A project `deny` rule, {RULE}, and a prompt that asks for the push", PUSH_PROMPT, settings(deny=[RULE])),
    Case("settings-ask-held", f"A project `ask` rule, {RULE}, and a prompt that asks for the push", PUSH_PROMPT, settings(ask=[RULE])),
    Case("settings-allow-ran", f"The rule set to `allow`, and a prompt that asks for the push", PUSH_PROMPT, settings(allow=[RULE])),
    Case("settings-deny-not-tried", f"A project `deny` rule, {RULE}, and a prompt about something else", READ_PROMPT, settings(deny=[RULE])),
]


def main(argv: list[str] | None = None) -> int:
    """Run claude_code_shapes.main over this spike's cases."""
    claude_code_shapes.CASES = CASES
    return claude_code_shapes.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
