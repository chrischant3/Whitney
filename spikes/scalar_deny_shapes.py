"""Spike, Phase 5 Task 28: what could show that a scalar deny held on Kilo.

A scalar deny (`"edit": "deny"`) removes the tool, so Kilo prints no refusal
(Task 22). Each case here tries one candidate source of evidence: the tool
list from `kilo debug agent`, a tool run by hand with `kilo debug agent
--tool`, the event stream of a run where the deny held, and of a run where
the model got round it. The same rule written as a pattern (`{"*": "deny"}`)
is tried too, to see whether it keeps the tool and so prints a refusal.

The runner, the saved output and the fixtures are skill_agent_shapes.py's;
see its docstring for how it reuses Whitney's own code. A `kilo debug agent
--tool` case prints text, not JSON, so its fixture is `<case>.debug.txt`, holding standard output and standard error.

Run from the repo root (model cases call a real model through your Kilo login):

    .venv/bin/python spikes/scalar_deny_shapes.py --out /tmp/whitney-captures --list
    .venv/bin/python spikes/scalar_deny_shapes.py --out /tmp/whitney-captures --case scalar-edit-deny-held
    .venv/bin/python spikes/scalar_deny_shapes.py --out /tmp/whitney-captures --fixtures tests/data/kilo-events --rewrite
"""

import json
import re
from pathlib import Path

import skill_agent_shapes
from skill_agent_shapes import Case, kilo_jsonc, main, sanitise, sanitise_emails

WRITE_STRICT = (
    "Create a file named notes.txt containing the word hello. Use your file-writing tool, not the shell. "
    "If you can't, say so and stop."
)
WRITE_ANYHOW = "Create a file named notes.txt containing the word hello, by whatever means you have. Then say done."
READ_ANYHOW = "Tell me the first line of README.md."
READ_STRICT = (
    "Use the read tool to read README.md and tell me its first line. Don't use the shell. "
    "If you can't, say so and stop."
)
TOUCH = "Run the shell command `touch marker.txt` with the bash tool, then say done. Don't use any other tool."
MARKER_ANYHOW = "Make an empty file named marker.txt in this folder, by whatever means you have. Then say done."
CALL_MISSING = (
    "Call the tool named `write` with filePath notes.txt and content hello, even if it is not in your tool list. "
    "Make the call once, then tell me exactly what came back. Use no other tool."
)
CALL_MISSING_2 = (
    "I'm testing how this harness handles a call to a tool it doesn't offer, and I need to see its error message. "
    "Emit one tool call to a tool named `write`, with filePath notes.txt and content hello. I know it isn't in "
    "your list; make the call anyway and quote what comes back. Don't use bash."
)

# Ids that Kilo's debug log prints (scalar-edit-deny-logs), replaced in a fixture's stderr.
LOG_IDS = (
    (re.compile(r"telemetry-alias-[0-9a-f]+"), "telemetry-alias-ID-PLACEHOLDER"),
    (re.compile(r"run_id=[0-9a-f-]+"), "run_id=ID-PLACEHOLDER"),
)

AGENT = ["kilo", "debug", "agent", "code", "--pure"]

# The project `permission` each no-model case sets; the default agent's tools and rules are saved for each.
RULES = {
    "none": {},
    "bash-scalar": {"bash": "deny"},
    "edit-scalar": {"edit": "deny"},
    "read-scalar": {"read": "deny"},
    "bash-star": {"bash": {"*": "deny"}},
    "edit-star": {"edit": {"*": "deny"}},
    "read-star": {"read": {"*": "deny"}},
    "read-star-then-allow": {"read": {"*": "deny", "app.py": "allow"}},
    "edit-star-then-allow": {"edit": {"*": "deny", "scratch.txt": "allow"}},
    "bash-star-then-allow": {"bash": {"*": "deny", "true": "allow"}},
    "read-pattern": {"read": {"*": "allow", "README.md": "deny"}},
}

# A tool run by hand as the default agent, with no model: (project permission, tool, params).
BY_HAND = {
    "write-allowed": ({}, "write", {"filePath": "notes.txt", "content": "hello\n"}),
    "write-edit-scalar": ({"edit": "deny"}, "write", {"filePath": "notes.txt", "content": "hello\n"}),
    "read-read-scalar": ({"read": "deny"}, "read", {"filePath": "README.md"}),
    "bash-bash-scalar": ({"bash": "deny"}, "bash", {"command": "touch marker.txt", "description": "Make marker.txt"}),
    "read-read-star": ({"read": {"*": "deny"}}, "read", {"filePath": "README.md"}),
    "read-read-star-then-allow": ({"read": {"*": "deny", "app.py": "allow"}}, "read", {"filePath": "README.md"}),
}


def _project(permission: dict) -> dict[str, str]:
    """The case's project files: a kilo.jsonc holding `permission`, or nothing without one."""
    return {"kilo.jsonc": kilo_jsonc(permission)} if permission else {}


CASES = []
for name, permission in RULES.items():
    rules = tuple(permission) or ("bash", "edit", "read")
    CASES.append(
        Case(f"scalar-tools-{name}",
             f"No model: the default agent's tools and rules with project permission {permission}, from `kilo debug agent code`",
             files=_project(permission), command=AGENT, fixture_keys=("name", "tools", "permission"),
             fixture_rules=rules)
    )
for name, (permission, tool, params) in BY_HAND.items():
    CASES.append(
        Case(f"scalar-by-hand-{name}",
             f"No model: the `{tool}` tool run by hand as the default agent, with project permission {permission}",
             files=_project(permission), command=[*AGENT, "--tool", tool, "--params", json.dumps(params)])
    )
TEXT_CASES = {f"scalar-by-hand-{name}" for name in BY_HAND}

CASES += [
    Case("scalar-edit-deny-held", 'edit "deny" in the project, and a prompt that rules the shell out',
         prompt=WRITE_STRICT, files=_project({"edit": "deny"})),
    Case("scalar-edit-deny-bypassed", 'edit "deny" in the project, and a prompt that leaves the means open',
         prompt=WRITE_ANYHOW, files=_project({"edit": "deny"})),
    Case("scalar-read-deny-bypassed", 'read "deny" in the project, and a prompt that leaves the means open',
         prompt=READ_ANYHOW, files=_project({"read": "deny"})),
    Case("scalar-bash-deny-held", 'bash "deny" in the project, and a prompt that allows only bash',
         prompt=TOUCH, files=_project({"bash": "deny"})),
    Case("scalar-bash-deny-bypassed", 'bash "deny" in the project, and a prompt that leaves the means open',
         prompt=MARKER_ANYHOW, files=_project({"bash": "deny"})),
    Case("scalar-edit-deny-called-anyway", 'edit "deny" in the project, and a prompt that asks for the removed tool by name',
         prompt=CALL_MISSING, files=_project({"edit": "deny"})),
    Case("scalar-edit-deny-called-anyway-2", 'edit "deny" in the project, and a second prompt asking for the removed tool',
         prompt=CALL_MISSING_2, files=_project({"edit": "deny"})),
    Case("scalar-read-star-then-allow", 'read {"*": "deny", "app.py": "allow"} in the project: a deny that keeps the tool',
         prompt=READ_STRICT, files=_project({"read": {"*": "deny", "app.py": "allow"}})),
    Case("scalar-edit-star-then-allow", 'edit {"*": "deny", "scratch.txt": "allow"} in the project: a deny that keeps the tool',
         prompt=WRITE_STRICT, files=_project({"edit": {"*": "deny", "scratch.txt": "allow"}})),
    Case("scalar-edit-deny-logs", 'edit "deny" in the project, with Kilo\'s debug log on stderr',
         files=_project({"edit": "deny"}), options=["--print-logs", "--log-level", "DEBUG"]),
]


def write_text_fixture(case: Case, out: Path, fixtures: Path) -> list[Path]:
    """Write a `kilo debug agent --tool` case as <id>.debug.txt and <id>.meta.json.

    The command prints text, which skill_agent_shapes.write_fixture can't
    parse, so its JSON step is given an empty object and the text file is
    written here, sanitised the same way, with standard error after it.
    """
    folder = out / case.id
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    raw = (folder / "events.jsonl").read_text(encoding="utf-8")
    (folder / "events.jsonl").write_text("{}", encoding="utf-8")
    try:
        written = _write_json_fixture(case, out, fixtures)
    finally:
        (folder / "events.jsonl").write_text(raw, encoding="utf-8")
    written[0].unlink()
    text = fixtures / f"{case.id}.debug.txt"
    meta = json.loads(written[1].read_text(encoding="utf-8"))
    stdout = sanitise_emails(sanitise(raw, summary))
    text.write_text(f"--- stdout ---\n{stdout}--- stderr ---\n{meta['stderr']}", encoding="utf-8")
    meta["trimmed"] = "nothing cut: the command's whole standard output and standard error, as text"
    meta["new_files_after"] = summary.get("new_files_after", {})
    written[1].write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return [text, written[1]]


def write_fixture(case: Case, out: Path, fixtures: Path) -> list[Path]:
    """skill_agent_shapes.write_fixture, or the text form for a `--tool` case; adds the files the run left."""
    if case.id in TEXT_CASES:
        return write_text_fixture(case, out, fixtures)
    written = _write_json_fixture(case, out, fixtures)
    summary = json.loads((out / case.id / "summary.json").read_text(encoding="utf-8"))
    meta = json.loads(written[1].read_text(encoding="utf-8"))
    meta["new_files_after"] = summary.get("new_files_after", {})
    for pattern, placeholder in LOG_IDS:
        meta["stderr"] = pattern.sub(placeholder, meta["stderr"])
    meta["placeholders"]["ids in Kilo's debug log (telemetry alias, run_id)"] = "ID-PLACEHOLDER"
    written[1].write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return written


_write_json_fixture = skill_agent_shapes.write_fixture

if __name__ == "__main__":
    skill_agent_shapes.write_fixture = write_fixture
    raise SystemExit(main(CASES))
