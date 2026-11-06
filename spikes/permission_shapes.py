"""Spike, Phase 5 Task 22 (spec slice 9): how Kilo shows each permission refusal.

Each case sets one permission in the project's kilo.jsonc and asks the agent
to do the thing it governs, under the same isolation as Whitney's local runs
and with Whitney's harness overlay (HARNESS_PERMISSIONS: bash and edit
allowed). The merge cases make no model call: they run `kilo debug config`
and `kilo debug agent code` to show how the overlay's `bash` combines with a
project's `bash` patterns. The tools cases make none either: they show which
tools `kilo debug agent code` offers the default agent under each rule.

The runner, the saved output and the fixtures are skill_agent_shapes.py's;
see its docstring for how it reuses Whitney's own code.

Run from the repo root (model cases call a real model through your Kilo login):

    .venv/bin/python spikes/permission_shapes.py --out /tmp/whitney-captures
    .venv/bin/python spikes/permission_shapes.py --out /tmp/whitney-captures --case bash-deny
    .venv/bin/python spikes/permission_shapes.py --out /tmp/whitney-captures --fixtures tests/data/kilo-events
    .venv/bin/python spikes/permission_shapes.py --out /tmp/whitney-captures --fixtures tests/data/kilo-events --rewrite
"""

from skill_agent_shapes import Case, kilo_jsonc, main

TOUCH = "Run the shell command `touch marker.txt` with the bash tool, then say done. Don't use any other tool."
WRITE = (
    "Create a file named notes.txt containing the word hello. Use your file-writing tool, not the shell. "
    "If you can't, say so and stop."
)
READ = (
    "Use the read tool to read README.md and tell me its first line. Don't use the shell. "
    "If you can't, say so and stop."
)

# (overlay permissions, project permissions) for the merge cases.
MERGES = {
    "merge-overlay-scalar-project-patterns": (
        {"bash": "allow", "edit": "allow"},
        {"bash": {"*": "ask", "touch *": "deny"}},
    ),
    "merge-both-patterns": (
        {"bash": {"*": "allow"}, "edit": "allow"},
        {"bash": {"touch *": "deny"}},
    ),
    "merge-overlay-specific-project-catchall": (
        {"bash": {"touch *": "deny"}, "edit": "allow"},
        {"bash": {"*": "allow"}},
    ),
    "merge-overlay-scalar-project-no-catchall": (
        {"bash": "allow", "edit": "allow"},
        {"bash": {"touch *": "deny"}},
    ),
    "merge-same-pattern-both": (
        {"bash": {"*": "allow", "touch *": "allow"}, "edit": "allow"},
        {"bash": {"touch *": "deny"}},
    ),
    "merge-overlay-patterns-project-scalar": (
        {"bash": {"*": "allow", "touch *": "allow"}, "edit": "allow"},
        {"bash": "ask"},
    ),
}

CASES = [
    Case("bash-deny", 'bash "deny" in the project', prompt=TOUCH, files={"kilo.jsonc": kilo_jsonc({"bash": "deny"})}),
    Case(
        "bash-ask-pattern",
        'bash {"*": "allow", "touch *": "ask"} in the project, headless without --auto',
        prompt=TOUCH,
        files={"kilo.jsonc": kilo_jsonc({"bash": {"*": "allow", "touch *": "ask"}})},
    ),
    Case(
        "bash-deny-pattern",
        'bash {"*": "allow", "touch *": "deny"} in the project',
        prompt=TOUCH,
        files={"kilo.jsonc": kilo_jsonc({"bash": {"*": "allow", "touch *": "deny"}})},
    ),
    Case("edit-deny", 'edit "deny" in the project', prompt=WRITE, files={"kilo.jsonc": kilo_jsonc({"edit": "deny"})}),
    Case(
        "edit-ask",
        'edit "ask" in the project, headless without --auto',
        prompt=WRITE,
        files={"kilo.jsonc": kilo_jsonc({"edit": "ask"})},
    ),
    Case("read-deny", 'read "deny" in the project', prompt=READ, files={"kilo.jsonc": kilo_jsonc({"read": "deny"})}),
    Case(
        "bash-nonzero-exit",
        "A bash command that runs and exits non-zero, under the overlay's bash allow",
        prompt="Run `ls does-not-exist` once with the bash tool and tell me its exit code. Don't run anything else.",
    ),
]
for name, (overlay, project) in MERGES.items():
    files = {"kilo.jsonc": kilo_jsonc(project)}
    about = f"No model: overlay permission {overlay} with project permission {project}"
    CASES += [
        Case(f"{name}-config", about + ", resolved by `kilo debug config`", files=files, harness_permissions=overlay,
             command=["kilo", "debug", "config", "--pure"], fixture_keys=("permission", "permission_origins")),
        Case(f"{name}-agent", about + ", as the default agent's rules from `kilo debug agent code`", files=files,
             harness_permissions=overlay, command=["kilo", "debug", "agent", "code", "--pure"],
             fixture_keys=("name", "permission"), fixture_rules=("bash",)),
    ]

# Which tools the default agent is offered under each rule (no model): `kilo debug agent` lists them.
TOOL_LISTS = {
    "tools-default": {},
    "tools-bash-deny": {"bash": "deny"},
    "tools-bash-deny-pattern": {"bash": {"*": "allow", "touch *": "deny"}},
    "tools-edit-deny": {"edit": "deny"},
    "tools-read-deny": {"read": "deny"},
}
for name, project in TOOL_LISTS.items():
    CASES.append(
        Case(name, f"No model: the default agent's tools with project permission {project}, from `kilo debug agent code`",
             files={"kilo.jsonc": kilo_jsonc(project)} if project else {},
             command=["kilo", "debug", "agent", "code", "--pure"], fixture_keys=("name", "tools"))
    )


if __name__ == "__main__":
    raise SystemExit(main(CASES))
