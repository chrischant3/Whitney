"""Spike, Phase 5 Task 16: what Kilo emits for skills, subagents, --agent and --command.

Each case stages a small git project, runs Kilo once under the same isolation
as Whitney's local runs, and saves what came back: the exit code, the JSON
event stream, stderr and the project's state afterwards. --fixtures turns
the saved runs into the sanitised fixtures under tests/data/kilo-events/.

Run from the repo root. Cases listed as "model" call a real model through
your Kilo login, about 2 to 4 cents a run on Haiku; the others run
`kilo debug ...` and cost nothing. `--list` shows which is which.

    .venv/bin/python spikes/skill_agent_shapes.py --out /tmp/whitney-captures
    .venv/bin/python spikes/skill_agent_shapes.py --out /tmp/whitney-captures --case skill-description
    .venv/bin/python spikes/skill_agent_shapes.py --out /tmp/whitney-captures --fixtures tests/data/kilo-events
    .venv/bin/python spikes/skill_agent_shapes.py --out /tmp/whitney-captures --fixtures tests/data/kilo-events --rewrite

With --fixtures, each case's sanitised fixture is written as soon as its run
is saved; add --rewrite to rebuild them from saved runs without running Kilo.

How it reuses Whitney's runner rather than rebuilding it:
- The project is staged by whitney.agent.staged_project, and Kilo runs through
  whitney.agent._run_local_process: the same check for instruction files above
  the project, temporary HOME (kilo_home, holding only a copy of the Kilo
  login), environment allow-list, git isolation and redaction as a real eval.
- The KILO_CONFIG overlay is built as whitney.agent.kilo does it, from
  local_config_overlay, global_provider_settings, referenced_env and
  whitney.tasks.HARNESS_PERMISSIONS.
- Two module functions are patched for the length of one case only:
  _kilo_command, to add `--agent` or `--command` before the `--` that ends
  the options, or to run `kilo debug ...` instead of `kilo run`; and
  kilo_home, to add files to the temporary HOME (the user-global skill,
  agent and command folders). Neither change touches the environment Kilo
  gets.

Raw output stays in --out, outside the repo. Only --fixtures output is meant
to be committed.
"""

import argparse
import asyncio
import contextlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from unittest import mock

from inspect_ai.agent import AgentState
from inspect_ai.model import ChatMessageUser

import whitney.agent as kilo_agent
from whitney.git_env import isolated_git_env
from whitney.tasks import HARNESS_PERMISSIONS

MODEL = "kilo/anthropic/claude-haiku-4.5"
REPO = Path(__file__).resolve().parents[1]
MIN_USER_NAME = 4  # a shorter user name would be replaced inside ordinary words
TIMEOUT = 300

# Placeholders the fixtures use instead of this machine's temporary folders.
PROJECT_PLACEHOLDER = "/tmp/kilo-eval-XXXXXXXX/project"
HOME_PLACEHOLDER = "/tmp/kilo-eval-home-XXXXXXXX"
TMP_PLACEHOLDER = "/tmp/"
USER_HOME_PLACEHOLDER = "/home/user"
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
KEEP_EMAILS = {"agent@example.invalid"}  # the fixed git identity from whitney.git_env
LONG_TEXT = 1200  # characters kept of a long string in a fixture
# step_finish carries the gateway's request id, which names the regions the request went through.
REQUEST_ID_KEYS = ("vercelID",)
REQUEST_ID_PLACEHOLDER = "REQUEST-ID-PLACEHOLDER"

APP_PY = "def total(items):\n    return sum(items)\n"
APP_PY_V2 = APP_PY + "\n\ndef refund(amount):\n    return -amount\n"
BASE_FILES = {"README.md": "# Till\n\nA tiny till for the spike.\n", "app.py": APP_PY}
COMMITS = ["Add the till with total()", "Add refund()"]  # see _init_repo


@dataclass
class Case:
    """One Kilo run to capture.

    Attributes:
        id: names the output folder and the fixture.
        about: one line for the write-up.
        prompt: the message; for `--command` it's the command's arguments.
        files: project files, on top of BASE_FILES.
        home_files: files to add to the temporary HOME, relative to it.
        options: extra `kilo run` options, placed before `--`.
        command: run this instead of `kilo run` (a `kilo debug ...` call), so no model is called.
        harness_permissions: the overlay's permissions, Whitney's by default.
        keep: event types the fixture keeps (all of them when empty).
        fixture_keys: for a `kilo debug ...` case, the keys its fixture keeps
            (of the output object, or of each item of an output list).
        fixture_rules: for `kilo debug agent`, the permissions whose rules
            its fixture keeps (all of them when empty).
    """

    id: str
    about: str
    prompt: str = "Reply with exactly the word OK."
    files: dict[str, str] = field(default_factory=dict)
    home_files: dict[str, str] = field(default_factory=dict)
    options: list[str] = field(default_factory=list)
    command: list[str] | None = None
    harness_permissions: dict | None = field(default_factory=lambda: dict(HARNESS_PERMISSIONS))
    keep: tuple[str, ...] = ()
    fixture_keys: tuple[str, ...] = ()
    fixture_rules: tuple[str, ...] = ()

    @property
    def calls_model(self) -> bool:
        """False for `kilo debug ...` cases."""
        return self.command is None


def kilo_jsonc(permission: dict) -> str:
    """A project kilo.jsonc holding only `permission`."""
    return json.dumps({"$schema": "https://app.kilo.ai/config.json", "permission": permission}, indent=2) + "\n"


def skill_md(name: str, description: str, body: str) -> str:
    """A SKILL.md with Kilo's frontmatter."""
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"


def agent_md(description: str, mode: str, body: str) -> str:
    """An agent file with Kilo's frontmatter; the file name is the agent's name."""
    return f"---\ndescription: {description}\nmode: {mode}\n---\n\n{body}\n"


RELEASE_NOTES_SKILL = skill_md(
    "release-notes",
    "Write release notes for this project. Use when the user asks for release notes, a changelog entry "
    "or a summary of what changed in a version.",
    "# Release notes\n\nMarker: CANARY-SKILL-RN-4821\n\nWrite the notes as a bulleted list under the heading "
    '"## What changed". Start the reply with the line "RN-FORMAT-4821".',
)

# One skill per candidate folder, each with its own name and marker, for the canary method.
SKILL_FOLDERS = {
    "canary-kilo": ".kilo/skills",
    "canary-kilo-singular": ".kilo/skill",
    "canary-kilocode": ".kilocode/skills",
    "canary-kilocode-singular": ".kilocode/skill",
    "canary-claude": ".claude/skills",
    "canary-agents": ".agents/skills",
    "canary-opencode": ".opencode/skills",
}
HOME_SKILL_FOLDERS = {
    "canary-global-xdg": ".config/kilo/skills",
    "canary-global-kilo": ".kilo/skills",
    "canary-global-kilocode": ".kilocode/skills",
    "canary-global-claude": ".claude/skills",
    "canary-global-agents": ".agents/skills",
}


def canary_skill(name: str) -> str:
    """A skill whose description and body both carry a marker made from its name."""
    marker = f"CANARY-{name.upper()}-3307"
    return skill_md(name, f"Canary skill {marker}. Use only when asked for {name} by name.", f"Marker: {marker}")


FOLDER_FILES = {f"{folder}/{name}/SKILL.md": canary_skill(name) for name, folder in SKILL_FOLDERS.items()}
HOME_FOLDER_FILES = {f"{folder}/{name}/SKILL.md": canary_skill(name) for name, folder in HOME_SKILL_FOLDERS.items()}

# The same method for agents and commands: one canary file per candidate folder.
AGENT_FOLDERS = {
    "canary-agent-kilo": ".kilo/agents",
    "canary-agent-kilo-singular": ".kilo/agent",
    "canary-agent-kilocode": ".kilocode/agents",
    "canary-agent-kilocode-singular": ".kilocode/agent",
    "canary-agent-claude": ".claude/agents",
    "canary-agent-opencode": ".opencode/agents",
}
HOME_AGENT_FOLDERS = {
    "canary-agent-global-xdg": ".config/kilo/agents",
    "canary-agent-global-kilo": ".kilo/agents",
    "canary-agent-global-claude": ".claude/agents",
}
COMMAND_FOLDERS = {
    "canary-command-kilo": ".kilo/commands",
    "canary-command-kilo-singular": ".kilo/command",
    "canary-command-kilocode": ".kilocode/commands",
    "canary-command-claude": ".claude/commands",
    "canary-command-opencode": ".opencode/commands",
}
HOME_COMMAND_FOLDERS = {
    "canary-command-global-xdg": ".config/kilo/commands",
    "canary-command-global-claude": ".claude/commands",
}


def canary_agent(name: str) -> str:
    """A subagent whose description and prompt carry a marker made from its name."""
    marker = f"CANARY-{name.upper()}-3307"
    return agent_md(f"Canary agent {marker}. Use only when asked for {name} by name.", "subagent", f"Marker: {marker}")


def canary_command(name: str) -> str:
    """A command whose description and template carry a marker made from its name."""
    marker = f"CANARY-{name.upper()}-3307"
    return f"---\ndescription: Canary command {marker}\n---\n\nReply with {marker}.\n"


AGENT_COMMAND_FILES = {
    **{f"{folder}/{name}.md": canary_agent(name) for name, folder in AGENT_FOLDERS.items()},
    **{f"{folder}/{name}.md": canary_command(name) for name, folder in COMMAND_FOLDERS.items()},
}
HOME_AGENT_COMMAND_FILES = {
    **{f"{folder}/{name}.md": canary_agent(name) for name, folder in HOME_AGENT_FOLDERS.items()},
    **{f"{folder}/{name}.md": canary_command(name) for name, folder in HOME_COMMAND_FOLDERS.items()},
}

# A subagent with a name that says nothing, so only its description can match a prompt.
Q7_AGENT = agent_md(
    "Counts the lines in a file. Use this agent whenever someone needs to know how many lines a file has.",
    "subagent",
    'You count lines in files. Run `wc -l <file>` with the bash tool, then reply with exactly one line: '
    '"Q7-COUNT-5390 <number> <file>".',
)
REVIEWER_AGENT = agent_md(
    "Reviews code and explains problems without changing files.",
    "primary",
    'You are a code reviewer. Start every reply with the line "P-REVIEWER-6612". Never edit files.',
)
# Phase 5 Task 21: can one agent be both run with --agent and delegated to? Only `mode: all` could be.
ALL_AGENT = agent_md(
    "Reviews code and explains problems without changing files. Use this agent whenever someone wants code reviewed.",
    "all",
    'You are a code reviewer. Start every reply with the line "A-REVIEWER-3307". Never edit files.',
)
SHOUT_COMMAND = (
    "---\ndescription: Repeat the arguments in capitals\n---\n\n"
    'Reply with exactly one line: "CMD-SHOUT-7731" followed by these words in capital letters: $ARGUMENTS\n'
)

CASES = [
    Case(
        "skill-description",
        "A skill in .kilo/skills/ and a prompt that matches its description without naming it",
        prompt="Write short release notes for the latest version of this project. Reply in chat only; don't create or change files.",
        files={".kilo/skills/release-notes/SKILL.md": RELEASE_NOTES_SKILL},
    ),
    Case(
        "skill-named",
        "The same skill, named in the prompt",
        prompt=(
            "Use the release-notes skill to write short release notes for the latest version of this project. "
            "Reply in chat only; don't create or change files."
        ),
        files={".kilo/skills/release-notes/SKILL.md": RELEASE_NOTES_SKILL},
    ),
    Case(
        "skill-folders-listing",
        "No model: which skill folders `kilo debug skill` finds, one canary skill per folder",
        files=FOLDER_FILES,
        home_files=HOME_FOLDER_FILES,
        command=["kilo", "debug", "skill", "--pure"],
        fixture_keys=("name", "description", "location"),
    ),
    Case(
        "skill-folders-run",
        "The same canary skills; the model is asked to load the four the brief names, by name",
        prompt=(
            "Call the skill tool once for each of these skill names, one after another: canary-kilo, "
            "canary-kilocode, canary-claude, canary-global-xdg. Then list which calls worked. Do nothing else."
        ),
        files=FOLDER_FILES,
        home_files=HOME_FOLDER_FILES,
    ),
    Case(
        "agent-command-folders-listing",
        "No model: which agent and command folders `kilo debug config` resolves, one canary file per folder",
        files=AGENT_COMMAND_FILES,
        home_files=HOME_AGENT_COMMAND_FILES,
        command=["kilo", "debug", "config", "--pure"],
        fixture_keys=("agent", "command"),
    ),
    Case(
        "subagent-debug",
        "No model: `kilo debug agent q7-helper`, the subagent the model runs use",
        files={".kilo/agents/q7-helper.md": Q7_AGENT},
        command=["kilo", "debug", "agent", "q7-helper", "--pure"],
        fixture_keys=("name", "description", "mode", "prompt", "permission"),
        fixture_rules=("task", "bash"),
    ),
    Case(
        "subagent-description",
        "A subagent in .kilo/agents/ and a prompt that matches its description without naming it",
        prompt="How many lines does app.py have? Hand the counting to a subagent, then tell me the answer.",
        files={".kilo/agents/q7-helper.md": Q7_AGENT},
    ),
    Case(
        "agent-option-primary",
        "kilo run --agent with a primary agent from .kilo/agents/",
        prompt="Read app.py, then say in one sentence what it does.",
        files={".kilo/agents/p-reviewer.md": REVIEWER_AGENT},
        options=["--agent", "p-reviewer"],
    ),
    Case(
        "agent-option-subagent",
        "kilo run --agent with an agent whose mode is subagent",
        prompt="How many lines does app.py have?",
        files={".kilo/agents/q7-helper.md": Q7_AGENT},
        options=["--agent", "q7-helper"],
    ),
    Case(
        "agent-option-missing",
        "kilo run --agent naming an agent that isn't there",
        options=["--agent", "no-such-agent"],
    ),
    Case(
        "command-option",
        "kilo run --command with a command in .kilo/commands/; the message is its arguments",
        prompt="hello world",
        files={".kilo/commands/shout.md": SHOUT_COMMAND},
        options=["--command", "shout"],
    ),
    Case(
        "command-option-missing",
        "kilo run --command naming a command that isn't there",
        options=["--command", "no-such-command"],
    ),
    Case(
        "agent-all-debug",
        "No model: `kilo debug agent a-reviewer`, an agent whose mode is all",
        files={".kilo/agents/a-reviewer.md": ALL_AGENT},
        command=["kilo", "debug", "agent", "a-reviewer", "--pure"],
        fixture_keys=("name", "description", "mode", "prompt", "permission"),
        fixture_rules=("task", "bash", "edit"),
    ),
    Case(
        "agent-option-all",
        "kilo run --agent with an agent whose mode is all",
        prompt="Read app.py, then say in one sentence what it does.",
        files={".kilo/agents/a-reviewer.md": ALL_AGENT},
        options=["--agent", "a-reviewer"],
    ),
    Case(
        "subagent-all-delegated",
        "The same mode-all agent, and a prompt that matches its description and asks for a hand-off without naming it",
        prompt="I'd like app.py reviewed. Hand the review to a subagent, then tell me what it said.",
        files={".kilo/agents/a-reviewer.md": ALL_AGENT},
    ),
    Case(
        "task-deny",
        'permission.task {"q7-helper": "deny"} in the project, and a prompt that names the subagent',
        prompt="Use the q7-helper subagent to count the lines in app.py, then tell me its answer.",
        files={".kilo/agents/q7-helper.md": Q7_AGENT, "kilo.jsonc": kilo_jsonc({"task": {"q7-helper": "deny"}})},
    ),
]


@contextlib.contextmanager
def _home_with(files: dict[str, str], seen: dict) -> Iterator[kilo_agent.KiloHome]:
    """Whitney's kilo_home, plus `files` written into it; records the folder in `seen`."""
    original = kilo_agent.kilo_home

    @contextlib.contextmanager
    def home_with_files(*args, **kwargs):
        with original(*args, **kwargs) as home:
            root = Path(home.env["HOME"])
            seen["home"] = str(root)
            for relative, content in files.items():
                if Path(relative).is_absolute() or ".." in Path(relative).parts:
                    raise ValueError(f"home file path must be relative: {relative!r}")
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            yield home

    with mock.patch.object(kilo_agent, "kilo_home", home_with_files):
        yield


def _command_for(case: Case, seen: dict):
    """A replacement for whitney.agent._kilo_command that adds the case's options."""
    original = kilo_agent._kilo_command

    def command(state, model, auto):
        argv = list(case.command) if case.command else original(state, model, auto)
        if case.options:
            end = argv.index("--")
            argv[end:end] = case.options
        if "--auto" in argv:
            raise ValueError("--auto turns every ask into allow; the spikes never pass it")
        seen["argv"] = argv
        return argv

    return command


def _git(project: Path, *args: str) -> str:
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", **isolated_git_env(project)}
    return subprocess.run(["git", *args], cwd=project, env=env, capture_output=True, text=True, check=True).stdout


def _init_repo(project: Path) -> None:
    """Two commits, so the project has a history: everything staged, then refund() added to app.py."""
    _git(project, "init", "-q", "--template=")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", COMMITS[0])
    (project / "app.py").write_text(APP_PY_V2, encoding="utf-8")
    _git(project, "commit", "-q", "-am", COMMITS[1])


def _kilo_version() -> str:
    """`kilo --version`, run with an empty temporary HOME so the user's own Kilo setup isn't read."""
    with tempfile.TemporaryDirectory(prefix="kilo-version-home-") as home:
        env = {"PATH": os.environ.get("PATH", ""), "HOME": home, "XDG_CONFIG_HOME": f"{home}/.config",
               "XDG_DATA_HOME": f"{home}/.local/share", "XDG_STATE_HOME": f"{home}/.local/state",
               "XDG_CACHE_HOME": f"{home}/.cache"}
        return subprocess.run(["kilo", "--version"], capture_output=True, text=True, env=env).stdout.strip()


def run_case(case: Case, out: Path) -> dict:
    """Run one case under Whitney's isolation and save the raw output in out/<case id>/.

    Returns:
        The saved summary: argv, exit code, project folder and temporary HOME
        (both removed by now), and the project's git status afterwards.
    """
    folder = out / case.id
    folder.mkdir(parents=True, exist_ok=True)
    seen: dict = {}
    overlay = kilo_agent.local_config_overlay(
        MODEL, case.harness_permissions, global_provider=kilo_agent.global_provider_settings()
    )
    state = AgentState(messages=[ChatMessageUser(content=case.prompt)])
    with (
        kilo_agent.staged_project({**BASE_FILES, **case.files}) as project,
        tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8") as config,
        _home_with(case.home_files, seen),
        mock.patch.object(kilo_agent, "_kilo_command", _command_for(case, seen)),
    ):
        _init_repo(project)
        json.dump(overlay, config)
        config.flush()
        env = {"KILO_CONFIG": config.name, **kilo_agent.referenced_env(overlay)}
        run = asyncio.run(kilo_agent._run_local_process(project, state, MODEL, False, env, TIMEOUT))
        status = _git(project, "status", "--porcelain", "--untracked-files=all")
        diff = _git(project, "diff")
        new_files = {
            line[3:]: (project / line[3:]).read_text(encoding="utf-8", errors="replace")
            for line in status.splitlines()
            if line.startswith("??") and (project / line[3:]).is_file()
        }
        project_dir = str(project)
    summary = {
        "case": case.id,
        "about": case.about,
        "date": date.today().isoformat(),
        "kilo_version": _kilo_version(),
        "model": MODEL if case.calls_model else None,
        "argv": seen.get("argv"),
        "overlay": overlay,
        "project_files": {**BASE_FILES, **case.files},
        "home_files": case.home_files,
        "exit_code": run["exit_code"],
        "project_dir": project_dir,
        "home_dir": seen.get("home"),
        "git_status_after": status,
        "git_diff_after": diff,
        "new_files_after": new_files,
    }
    (folder / "events.jsonl").write_text(run["events"], encoding="utf-8")
    (folder / "stderr.txt").write_text(run["stderr"], encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def sanitise(text: str, summary: dict) -> str:
    """`text` with this machine's paths and user name replaced by placeholders.

    Whitney's runner has already replaced the Kilo login's values with
    "[redacted]". This replaces the temporary project folder and HOME (in
    their /private/var and /var forms), any other path in the temporary
    folder, the user's own home folder, and the user name as a whole word.
    Email addresses are replaced by sanitise_emails, on parsed strings: on
    JSON text the pattern would swallow the "n" of an escaped newline.

    Raises:
        ValueError: the user name is shorter than MIN_USER_NAME characters.
    """
    replacements = []
    for key, placeholder in (("project_dir", PROJECT_PLACEHOLDER), ("home_dir", HOME_PLACEHOLDER)):
        value = summary.get(key)
        if value:
            bare = value.removeprefix("/private")
            replacements += [("/private" + bare, placeholder), (bare, placeholder)]
    tmp = tempfile.gettempdir().rstrip("/") + "/"
    replacements += [("/private" + tmp.removeprefix("/private"), TMP_PLACEHOLDER), (tmp.removeprefix("/private"), TMP_PLACEHOLDER)]
    replacements.append((str(Path.home()), USER_HOME_PLACEHOLDER))
    for old, new in replacements:
        text = text.replace(old, new)
    name = Path.home().name
    if len(name) < MIN_USER_NAME:
        raise ValueError(f"user name {name!r} is too short to replace safely; sanitise the fixtures by hand")
    return re.sub(rf"\b{re.escape(name)}\b", "user", text)


def sanitise_emails(value):
    """`value` with every email address in its strings, other than KEEP_EMAILS, replaced."""
    if isinstance(value, str):
        return EMAIL.sub(lambda m: m.group(0) if m.group(0) in KEEP_EMAILS else "user@example.invalid", value)
    if isinstance(value, dict):
        return {key: sanitise_emails(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitise_emails(item) for item in value]
    return value


def _trim(value):
    """`value` with every string longer than LONG_TEXT cut short, saying how much was cut.

    Also replaces the value of each REQUEST_ID_KEYS key with REQUEST_ID_PLACEHOLDER.
    """
    if isinstance(value, str) and len(value) > LONG_TEXT:
        return value[:LONG_TEXT] + f"...[trimmed {len(value) - LONG_TEXT} characters]"
    if isinstance(value, dict):
        return {key: REQUEST_ID_PLACEHOLDER if key in REQUEST_ID_KEYS else _trim(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_trim(item) for item in value]
    return value


def _debug_output(text: str):
    """The JSON a `kilo debug ...` command printed, skipping anything before it."""
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    return json.loads(text[min(starts):])


def _select(output, keys: tuple[str, ...], rules: tuple[str, ...] = ()):
    """`output` with only `keys`, from the object or from each item of a list; all of it when keys is empty.

    With `rules`, a `permission` list of rules keeps only the rules for those permissions, in order.
    """
    if keys:
        if isinstance(output, list):
            output = [{key: item[key] for key in keys if key in item} for item in output]
        else:
            output = {key: output[key] for key in keys if key in output}
    if rules and isinstance(output, dict) and isinstance(output.get("permission"), list):
        output = {**output, "permission": [rule for rule in output["permission"] if rule.get("permission") in rules]}
    return output


def write_fixture(case: Case, out: Path, fixtures: Path) -> list[Path]:
    """Turn out/<case id>/ into sanitised fixtures: <id>.jsonl (events) and <id>.meta.json.

    Each event is parsed and written again with json.dumps, so the lines
    are re-serialised, not byte-for-byte what Kilo printed. Lines that
    aren't JSON are left out and counted in the meta's skipped_lines.
    For a `kilo debug ...` case the events file is replaced by
    <id>.debug.json, holding the command's JSON output, cut to
    case.fixture_keys and trimmed the same way.
    """
    folder = out / case.id
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    events_text = sanitise((folder / "events.jsonl").read_text(encoding="utf-8"), summary)
    stderr = sanitise_emails(sanitise((folder / "stderr.txt").read_text(encoding="utf-8"), summary))
    fixtures.mkdir(parents=True, exist_ok=True)
    written = []
    skipped = 0
    if case.calls_model:
        lines = []
        for line in events_text.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                skipped += bool(line.strip())
                continue
            if not case.keep or event.get("type") in case.keep:
                lines.append(json.dumps(_trim(sanitise_emails(event))))
        path = fixtures / f"{case.id}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        path = fixtures / f"{case.id}.debug.json"
        output = _select(_debug_output(events_text), case.fixture_keys, case.fixture_rules)
        path.write_text(json.dumps(_trim(sanitise_emails(output)), indent=2) + "\n", encoding="utf-8")
    written.append(path)
    meta = {
        "case": case.id,
        "about": case.about,
        "date": summary["date"],
        "kilo_version": summary["kilo_version"],
        "model": summary["model"],
        "argv": json.loads(sanitise(json.dumps(summary["argv"]), summary)),
        # Only the overlay's permissions: the rest can hold provider settings from the user's global config.
        "overlay_permission": (summary["overlay"] or {}).get("permission"),
        "base_files": BASE_FILES,
        "commits": [{"message": COMMITS[0], "files": "every project file"},
                    {"message": COMMITS[1], "app.py": APP_PY_V2}],
        "project_files": {name: text for name, text in summary["project_files"].items() if name not in BASE_FILES},
        "home_files": summary["home_files"],
        "exit_code": summary["exit_code"],
        "stderr": stderr,
        "git_status_after": summary["git_status_after"],
        "git_diff_after": sanitise(summary.get("git_diff_after", ""), summary),
        "skipped_lines": skipped,
        "trimmed": (
            f"strings over {LONG_TEXT} characters cut; event types kept: {list(case.keep) or 'all'}"
            + (f"; output keys kept: {list(case.fixture_keys)}" if case.fixture_keys else "")
            + (f"; permission rules kept: {list(case.fixture_rules)}" if case.fixture_rules else "")
        ),
        "placeholders": {
            "project folder": PROJECT_PLACEHOLDER,
            "temporary HOME": HOME_PLACEHOLDER,
            "other temporary paths": TMP_PLACEHOLDER,
            "Kilo login values": kilo_agent.REDACTED,
            "user's home folder": USER_HOME_PLACEHOLDER,
            "user name": "user",
            "email addresses other than the git identity": "user@example.invalid",
            "gateway request id (step_finish vercelID)": REQUEST_ID_PLACEHOLDER,
        },
    }
    meta_path = fixtures / f"{case.id}.meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    written.append(meta_path)
    return written


def main(cases: list[Case], argv: list[str] | None = None) -> int:
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
    chosen = [c for c in cases if not args.case or c.id in args.case]
    if args.list:
        for case in cases:
            print(f"{case.id:28} {'model' if case.calls_model else 'no model':9} {case.about}")
        return 0
    if args.rewrite:
        if not args.fixtures:
            parser.error("--rewrite needs --fixtures")
        for case in chosen:
            if (args.out / case.id / "summary.json").is_file():
                for path in write_fixture(case, args.out, args.fixtures):
                    print(f"wrote {path}")
        return 0
    for case in chosen:
        summary = run_case(case, args.out)
        print(f"{case.id}: exit {summary['exit_code']}, saved in {args.out / case.id}")
        if args.fixtures:
            for path in write_fixture(case, args.out, args.fixtures):
                print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(CASES))
