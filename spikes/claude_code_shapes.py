"""Spike, Phase 5 Task 36: what Claude Code emits for skills and subagents.

Each case stages a small git project, runs `claude -p` once under the same
isolation as Whitney's claude-code runs, and saves what came back: the exit
code, the stream-json events and stderr. --fixtures turns the saved runs into
the sanitised fixtures under tests/data/claude-code-events/.

Run from the repo root, with ANTHROPIC_API_KEY set. Every case calls a real
model, a cent or two a run on Haiku.

    .venv/bin/python spikes/claude_code_shapes.py --out /tmp/whitney-cc-captures
    .venv/bin/python spikes/claude_code_shapes.py --out /tmp/whitney-cc-captures --case skill-description
    .venv/bin/python spikes/claude_code_shapes.py --out /tmp/whitney-cc-captures --fixtures tests/data/claude-code-events
    .venv/bin/python spikes/claude_code_shapes.py --out /tmp/whitney-cc-captures --fixtures tests/data/claude-code-events --rewrite

The command and the run are Whitney's own (whitney.claude_code._claude_command
and _run): the same flags, empty temporary HOME, environment, git isolation
and redaction of the API key as a real eval. The sanitising is the Kilo
spike's (skill_agent_shapes.py).

Raw output stays in --out, outside the repo. Only --fixtures output is meant
to be committed.
"""

import argparse
import asyncio
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from skill_agent_shapes import APP_PY_V2, BASE_FILES, COMMITS, LONG_TEXT, _init_repo, _trim, sanitise, sanitise_emails

import whitney.claude_code as claude_code
from whitney.agent import staged_project
from whitney.tasks import DEFAULT_HARNESS_PERMISSIONS

MODEL = "claude-haiku-4-5"
REPO = Path(__file__).resolve().parents[1]
TIMEOUT = 300
PERMISSIONS = DEFAULT_HARNESS_PERMISSIONS["claude-code"]
# Claude Code names a session's folder after the project's path, each character that isn't a letter or digit as "-".
PROJECT_SLUG_PLACEHOLDER = "-tmp-kilo-eval-XXXXXXXX-project"
UID_PLACEHOLDER = "claude-UID"  # its temporary folder is /tmp/claude-<uid>/
REQUEST_ID_PLACEHOLDER = "REQUEST-ID-PLACEHOLDER"


@dataclass
class Case:
    """One Claude Code run to capture.

    Attributes:
        id: names the output folder and the fixture.
        about: one line for the write-up.
        prompt: the message.
        files: project files, on top of BASE_FILES.
    """

    id: str
    about: str
    prompt: str
    files: dict[str, str] = field(default_factory=dict)


def skill_md(name: str, description: str, body: str) -> str:
    """A SKILL.md with Claude Code's frontmatter."""
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"


def agent_md(name: str, description: str, body: str) -> str:
    """An agent file with Claude Code's frontmatter."""
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"


RELEASE_NOTES_SKILL = {
    ".claude/skills/release-notes/SKILL.md": skill_md(
        "release-notes",
        "Write release notes for this project. Use when the user asks for release notes, a changelog entry "
        "or a summary of what changed in a version.",
        "# Release notes\n\nMarker: CANARY-SKILL-RN-4821\n\nWrite the notes as a bulleted list under the heading "
        '"## What changed". Start the reply with the line "RN-FORMAT-4821".',
    )
}
# A subagent with a name that says nothing, so only its description can match a prompt.
Q7_AGENT = {
    ".claude/agents/q7-helper.md": agent_md(
        "q7-helper",
        "Counts the lines in a file. Use this agent whenever someone needs to know how many lines a file has.",
        "You count lines in files. Run `wc -l <file>` with the Bash tool, then reply with exactly one line: "
        '"Q7-COUNT-5390 <number> <file>".',
    )
}
# A subagent whose job matches the skill, so it may load the skill itself.
NOTES_AGENT = {
    ".claude/agents/n4-writer.md": agent_md(
        "n4-writer",
        "Writes release notes. Use this agent whenever someone asks for release notes.",
        "You write release notes for the project you are in. Use the release-notes skill, and follow it.",
    )
}
READ_PROMPT = "Read app.py, then say in one sentence what it does. Reply in chat only; don't create or change files."

CASES = [
    Case(
        "skill-description",
        "A skill in .claude/skills/ and a prompt that matches its description without naming it",
        "Write short release notes for the latest version of this project. Reply in chat only; don't create or change files.",
        RELEASE_NOTES_SKILL,
    ),
    Case(
        "skill-named",
        "The same skill, named in the prompt",
        "Use the release-notes skill to write short release notes for the latest version of this project. "
        "Reply in chat only; don't create or change files.",
        RELEASE_NOTES_SKILL,
    ),
    Case(
        "skill-not-loaded",
        "The same skill, and a prompt that has nothing to do with it but needs a tool call",
        READ_PROMPT,
        RELEASE_NOTES_SKILL,
    ),
    Case(
        "no-tool-call",
        "The same skill, and a prompt that needs no tool call",
        "What is 17 + 25? Reply with the number only.",
        RELEASE_NOTES_SKILL,
    ),
    Case(
        "subagent-description",
        "A subagent in .claude/agents/ and a prompt that matches its description without naming it",
        "How many lines does app.py have? Hand the counting to a subagent, then tell me the answer.",
        Q7_AGENT,
    ),
    Case(
        "subagent-not-delegated",
        "The same subagent, and a prompt that has nothing to do with it but needs a tool call",
        READ_PROMPT,
        Q7_AGENT,
    ),
    Case(
        "subagent-loads-skill",
        "A subagent told to use the skill, and a prompt that asks for a hand-off",
        "Use the n4-writer subagent to write release notes for the latest version of this project, then show me what "
        "it wrote. Don't load any skill yourself: the subagent does that. Reply in chat only; don't create or change files.",
        {**RELEASE_NOTES_SKILL, **NOTES_AGENT},
    ),
    Case(
        "skill-missing",
        "A Skill call that names a skill that isn't there",
        "Call the Skill tool once with the skill name no-such-skill-5521, then tell me what the tool returned. Do nothing else.",
        RELEASE_NOTES_SKILL,
    ),
    Case(
        "agent-missing",
        "An Agent call that names a subagent that isn't there",
        "Call the Agent tool once with subagent_type no-such-agent-5521 and the prompt 'count the lines in app.py', "
        "then tell me what the tool returned. Do nothing else.",
        Q7_AGENT,
    ),
]


def _claude_version() -> str:
    return subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()


def run_case(case: Case, out: Path) -> dict:
    """Run one case under Whitney's isolation and save the raw output in out/<case id>/.

    Returns:
        The saved summary: argv, exit code, the project folder (removed by now) and what the run record says.
    """
    folder = out / case.id
    folder.mkdir(parents=True, exist_ok=True)
    argv = [*claude_code._claude_command(MODEL, PERMISSIONS), "--", case.prompt]
    with staged_project({**BASE_FILES, **case.files}) as project:
        _init_repo(project)
        run = asyncio.run(claude_code._run(argv, project, os.environ["ANTHROPIC_API_KEY"], TIMEOUT))
        project_dir = str(project)
    summary = {
        "case": case.id,
        "about": case.about,
        "date": date.today().isoformat(),
        "claude_code_version": _claude_version(),
        "model": MODEL,
        "argv": argv,
        "project_files": {**BASE_FILES, **case.files},
        "exit_code": run["exit_code"],
        "ended_early": run["ended_early"],
        "permission_denials": run["permission_denials"],
        "project_dir": project_dir,
    }
    (folder / "events.jsonl").write_text(run["events"], encoding="utf-8")
    (folder / "stderr.txt").write_text(run["stderr"], encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def sanitise_claude(text: str, summary: dict) -> str:
    """`text` sanitised as the Kilo spike does, and of what only Claude Code prints: the project's path as a session folder's name, and the user's id."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", summary["project_dir"])
    bare = slug.removeprefix("-private")  # macOS's /tmp is /private/tmp, and Claude Code names the folder either way
    text = text.replace("-private" + bare, PROJECT_SLUG_PLACEHOLDER).replace(bare, PROJECT_SLUG_PLACEHOLDER)
    return sanitise(text, summary).replace(f"claude-{os.getuid()}", UID_PLACEHOLDER)


def _without_request_ids(value):
    """`value` with every `request_id` replaced: it is the API's own id for the call."""
    if isinstance(value, dict):
        return {key: REQUEST_ID_PLACEHOLDER if key == "request_id" else _without_request_ids(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_request_ids(item) for item in value]
    return value


def write_fixture(case: Case, out: Path, fixtures: Path) -> list[Path]:
    """Turn out/<case id>/ into sanitised fixtures: <id>.jsonl (events) and <id>.meta.json.

    Each event is parsed and written again with json.dumps, so the lines are
    re-serialised, not byte-for-byte what Claude Code printed. Lines that
    aren't JSON are left out and counted in the meta's skipped_lines.
    """
    folder = out / case.id
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    events_text = sanitise_claude((folder / "events.jsonl").read_text(encoding="utf-8"), summary)
    stderr = sanitise_emails(sanitise_claude((folder / "stderr.txt").read_text(encoding="utf-8"), summary))
    fixtures.mkdir(parents=True, exist_ok=True)
    lines, skipped = [], 0
    for line in events_text.splitlines():
        try:
            lines.append(json.dumps(_without_request_ids(_trim(sanitise_emails(json.loads(line))))))
        except json.JSONDecodeError:
            skipped += bool(line.strip())
    path = fixtures / f"{case.id}.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    meta = {
        "case": case.id,
        "about": case.about,
        "date": summary["date"],
        "claude_code_version": summary["claude_code_version"],
        "model": summary["model"],
        "argv": json.loads(sanitise_claude(json.dumps(summary["argv"]), summary)),
        "base_files": BASE_FILES,
        "commits": [{"message": COMMITS[0], "files": "every project file"}, {"message": COMMITS[1], "app.py": APP_PY_V2}],
        "project_files": {name: text for name, text in summary["project_files"].items() if name not in BASE_FILES},
        "exit_code": summary["exit_code"],
        "ended_early": summary["ended_early"],
        "permission_denials": json.loads(sanitise_claude(json.dumps(summary["permission_denials"]), summary)),
        "stderr": stderr,
        "skipped_lines": skipped,
        "trimmed": f"strings over {LONG_TEXT} characters cut; event types kept: all",
        "placeholders": {
            "as tests/data/kilo-events": "the project folder, temporary paths, the user's home folder and name, email addresses",
            "the project's path as a session folder's name": PROJECT_SLUG_PLACEHOLDER,
            "Claude Code's temporary folder, claude-<user id>": UID_PLACEHOLDER,
            "request_id": REQUEST_ID_PLACEHOLDER,
            "the API key": "[redacted]",
        },
    }
    meta_path = fixtures / f"{case.id}.meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return [path, meta_path]


def main(argv: list[str] | None = None) -> int:
    """Run the chosen cases, or write fixtures from runs already saved."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, required=True, help="folder for raw output, outside the repo")
    parser.add_argument("--case", action="append", help="run only this case (repeatable)")
    parser.add_argument("--list", action="store_true", help="list the cases and exit")
    parser.add_argument("--fixtures", type=Path, help="write sanitised fixtures here (from saved runs with --rewrite)")
    parser.add_argument("--rewrite", action="store_true", help="with --fixtures: rewrite them from saved runs, and run nothing")
    args = parser.parse_args(argv)
    if args.out.resolve().is_relative_to(REPO):
        parser.error(f"--out must be outside the repo ({REPO}), so raw output is never committed")
    chosen = [c for c in CASES if not args.case or c.id in args.case]
    if args.list:
        for case in CASES:
            print(f"{case.id:24} {case.about}")
        return 0
    if args.rewrite and not args.fixtures:
        parser.error("--rewrite needs --fixtures")
    for case in chosen:
        if not args.rewrite:
            summary = run_case(case, args.out)
            print(f"{case.id}: exit {summary['exit_code']}, saved in {args.out / case.id}")
        if args.fixtures and (args.out / case.id / "summary.json").is_file():
            for path in write_fixture(case, args.out, args.fixtures):
                print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
