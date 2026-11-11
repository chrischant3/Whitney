"""Refusals an eval file asked for, by switching off a skill or an agent in its own `harness_permissions`.

A team may want to test without a harness's built-in skills and agents, which
the model can use in place of the project's. Both harnesses refuse a call
that a deny rule names, and say so in the stream. This module reads those
refusals, so the report can show each rule did what it was set to do, and so
a Claude Code run isn't thrown out for a denial the eval file itself asked for.

Every field read here was captured from a real run on 2026-11-11
(docs/spike-findings.md, "Phase 5 Task 36b"; fixtures `skill-denied*`,
`agent-denied*`, `skill-denied-overlay`, `task-denied-overlay`):

- Claude Code 2.1.322 lists a refused `Skill(<name>)` call in a result
  event's `permission_denials`, and its tool result is "Skill execution
  blocked by permission rules". A refused `Agent(<name>)` call is in no
  `permission_denials`; its tool result is an error naming the rule.
- Kilo 7.8.27 prints the call with `state.status` "error", the refusal in
  `state.error`, and the rule and where it came from in
  `state.metadata.approval`. Whitney's overlay is the source "global".

A rule that removes the whole tool (Claude Code's bare `Skill`, Kilo's
`"skill": "deny"`) leaves no call to refuse, so it isn't a rule read here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

CLAUDE_RULE = re.compile(r"(Skill|Agent)\(([^()\s*]+)\)")
CLAUDE_KINDS = {"Skill": "skill", "Agent": "agent"}
AGENT_TOOL = "Agent"  # Claude Code's tool for handing work to a subagent
# Kilo's permission (which is also its tool's name) for each kind, and the input field that names it.
KILO_CALLS = {"skill": ("skill", "name"), "agent": ("task", "subagent_type")}
KILO_OVERLAY_SOURCE = "global"  # where Kilo says a rule from Whitney's overlay came from


@dataclass(frozen=True)
class Rule:
    """One deny rule of an eval file's own: the kind it denies ("skill" or "agent"), the name, and the rule as the report shows it."""

    kind: str
    name: str
    text: str


def asked(target: str, permissions: dict[str, Any]) -> list[Rule]:
    """The rules in an eval file's permissions for `target` that deny one named skill or agent.

    Args:
        target: "kilo" or "claude-code".
        permissions: the file's `harness_permissions` for that target, in the harness's own format.

    Returns:
        For Claude Code, each `Skill(<name>)` and `Agent(<name>)` in `deny`.
        For Kilo, each pattern set to "deny" under `skill` and `task`.
    """
    if target == "claude-code":
        deny = permissions.get("deny")
        matches = [CLAUDE_RULE.fullmatch(rule) for rule in deny if isinstance(rule, str)] if isinstance(deny, list) else []
        return [Rule(CLAUDE_KINDS[match.group(1)], match.group(2), match.group(0)) for match in matches if match]
    rules = []
    for kind, (permission, _) in KILO_CALLS.items():
        patterns = permissions.get(permission)
        if isinstance(patterns, dict):
            rules += [Rule(kind, pattern, f"{permission}: {pattern}") for pattern, action in patterns.items() if action == "deny"]
    return rules


def refused(target: str, rules: list[Rule], run: dict) -> list[dict]:
    """What each rule refused in one run.

    Args:
        target: "kilo" or "claude-code".
        rules: the eval file's own deny rules (asked).
        run: the run record the agent stored: its `events`, and for Claude Code its `permission_denials`.

    Returns:
        One entry per rule, in order: {"rule": its text, "calls": how many calls it refused,
        "said": the harness's words for the first, or None if it refused none}.
        A rule that refused nothing is still listed, so silence can't be taken for proof.
    """
    read = _claude_refusals if target == "claude-code" else _kilo_refusals
    out = []
    for rule in rules:
        words = read(rule, run)
        out.append({"rule": rule.text, "calls": len(words), "said": words[0] if words else None})
    return out


def expected(rules: list[Rule], denial: dict) -> bool:
    """True if a Claude Code permission denial is of a skill one of `rules` names, so the eval file asked for it."""
    tool_input = denial.get("tool_input")
    skill = tool_input.get("skill") if isinstance(tool_input, dict) else None
    return denial.get("tool_name") == "Skill" and any(rule.kind == "skill" and rule.name == skill for rule in rules)


def _events(run: dict) -> list[dict]:
    """The run's stream as objects, leaving out any line that isn't one."""
    out = []
    for line in str(run.get("events") or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            out.append(event)
    return out


def _claude_blocks(run: dict, kind: str) -> list[dict]:
    """Every content block of type `kind` in a Claude Code stream's assistant and user messages."""
    blocks = []
    for event in _events(run):
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        blocks += [block for block in content if isinstance(block, dict) and block.get("type") == kind] if isinstance(content, list) else []
    return blocks


def _text(content: object) -> str:
    """A tool result's content as text: the string, or its text blocks joined."""
    if isinstance(content, list):
        return "\n".join(block.get("text", "") for block in content if isinstance(block, dict))
    return content if isinstance(content, str) else ""


def _claude_refusals(rule: Rule, run: dict) -> list[str]:
    """Claude Code's words for each call `rule` refused, in order.

    A skill's refusal is a call listed in `permission_denials`. An agent's is
    an `Agent` call's error result that names the rule, because Claude Code
    lists it nowhere else. The call itself may name no agent: one with no
    `subagent_type` went to general-purpose and was refused under its rule
    (fixture agent-denied-builtin).
    """
    if rule.kind == "skill":
        refused_ids = {denial.get("tool_use_id") for denial in run.get("permission_denials") or [] if expected([rule], denial)}
    else:
        refused_ids = {block.get("id") for block in _claude_blocks(run, "tool_use") if block.get("name") == AGENT_TOOL}
    words = []
    for result in _claude_blocks(run, "tool_result"):
        said = _text(result.get("content"))
        if result.get("tool_use_id") in refused_ids and result.get("is_error") is True:
            if rule.kind == "skill" or f"permission rule '{rule.text}'" in said:
                words.append(said)
    return words


def _kilo_refusals(rule: Rule, run: dict) -> list[str]:
    """Kilo's words for each call the overlay's `rule` refused, in order."""
    tool, field = KILO_CALLS[rule.kind]
    wanted = {"source": KILO_OVERLAY_SOURCE, "rule": {"permission": tool, "pattern": rule.name, "action": "deny"}}
    words = []
    for event in _events(run):
        part = event.get("part")
        state = part.get("state") if isinstance(part, dict) else None
        if event.get("type") != "tool_use" or not isinstance(state, dict) or part.get("tool") != tool:
            continue
        metadata = state.get("metadata")
        approval = metadata.get("approval") if isinstance(metadata, dict) else None
        if state.get("status") == "error" and approval == wanted:
            words.append(str(state.get("error") or ""))
    return words
