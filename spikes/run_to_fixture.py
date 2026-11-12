"""Save one run from a `whitney run` log as a sanitised event fixture.

Some streams worth keeping come from a real `whitney run`, not from a spike
script: the run that printed `tool_progress`, and Task 36b's runs with a
skill or an agent switched off by the eval file's own `harness_permissions`.
This copies one sample's stream out of the Inspect log, sanitises it the way
the spikes do, and writes `<name>.jsonl` and `<name>.meta.json`.

    python spikes/run_to_fixture.py --logs <log dir> --name skill-denied \\
        --about "..." --out tests/data/claude-code-events

The log already has the run's key redacted (whitney.agent.redact_run). This
replaces the machine's paths, user name and email addresses, and for Claude
Code the request ids, in the events and in the meta file. It then refuses to
write anything if a local path, an `sk-` key, the user's name or an email
address is still there. Run gitleaks on the result before committing it.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
from pathlib import Path

import claude_code_shapes
from inspect_ai.log import list_eval_logs, read_eval_log
from skill_agent_shapes import EMAIL, KEEP_EMAILS, LONG_TEXT, PROJECT_PLACEHOLDER, _trim, sanitise, sanitise_emails

# A staged project's folder, as either harness prints it on macOS or Linux, or in Whitney's Docker sandbox.
PROJECT_DIR = re.compile(r"(?:/private)?(?:/var/folders/[^\"\\ ]+?/T|/tmp)/tmp[A-Za-z0-9_]+/project|/workspace/project(?=[/\"\\ ]|$)")


def the_run(logs: Path, sample_id: str | None) -> tuple[dict, dict, str]:
    """The one run in `logs` (its record, the log's metadata and its prompt), or the one for `sample_id`.

    Raises:
        SystemExit: no run, or more than one, matches.
    """
    found = []
    for info in list_eval_logs(str(logs)):
        log = read_eval_log(info)
        for sample in log.samples or []:
            run = next((v for v in (sample.store or {}).values() if isinstance(v, dict) and "events" in v), None)
            if run and sample_id in (None, str(sample.id)):
                prompt = sample.input if isinstance(sample.input, str) else str(sample.input)
                found.append((run, dict(log.eval.metadata or {}), prompt))
    if len(found) != 1:
        raise SystemExit(f"{len(found)} runs match in {logs}; name one with --sample")
    return found[0]


# The project's folder in Whitney's Docker sandbox. The container's paths hold nothing of this machine's, so they're kept.
DOCKER_PROJECT_DIR = "/workspace/project"


def sanitised(text: str, project_dir: str | None, target: str) -> str:
    """`text` with this machine's paths and names replaced, as the target's spike does it.

    The staged project's parent folder becomes the placeholder's parent too, so
    a path beside the project, such as the fixture's `outside/`, reads as the
    project's does. A Docker sandbox's paths are left as they are.
    """
    if project_dir == DOCKER_PROJECT_DIR:
        return sanitise(text, {})
    if project_dir:
        parent, placeholder_parent = project_dir.rsplit("/", 1)[0], PROJECT_PLACEHOLDER.rsplit("/", 1)[0]
        bare = parent.removeprefix("/private")
        text = text.replace("/private" + bare + "/", placeholder_parent + "/").replace(bare + "/", placeholder_parent + "/")
    summary = {"project_dir": project_dir}
    return claude_code_shapes.sanitise_claude(text, summary) if target == "claude-code" else sanitise(text, summary)


def fixture(run: dict, project_dir: str | None, target: str) -> tuple[str, int]:
    """The run's events, sanitised, one JSON object per line, and how many lines weren't JSON."""
    text = sanitised(run["events"], project_dir, target)
    lines, skipped = [], 0
    for line in text.splitlines():
        try:
            event = _trim(sanitise_emails(json.loads(line)))
        except json.JSONDecodeError:
            skipped += bool(line.strip())
            continue
        lines.append(json.dumps(claude_code_shapes._without_request_ids(event) if target == "claude-code" else event))
    return "\n".join(lines) + "\n", skipped


# What must never be in a fixture: a home folder, a per-user temporary folder, or an Anthropic-style `sk-` key.
# It knows no other kind of key, which is why gitleaks is still run on the result.
LEFT_BEHIND = re.compile(r"/Users/|/home/(?!user\b)|/root/|/var/folders/|(?<![A-Za-z])sk-[A-Za-z0-9_-]{20,}")


def clean(value: object, project_dir: str | None, target: str) -> object:
    """A parsed JSON `value` with paths and names replaced, emails replaced and long strings cut."""
    return _trim(sanitise_emails(json.loads(sanitised(json.dumps(value), project_dir, target))))


def check_nothing_left(text: str, where: str) -> None:
    """Stop if `text` still holds a local path, an `sk-` key, this user's name or an email address.

    The replacements come from this machine: its home folder, user name and
    temporary folder. A log made elsewhere, or a name at the start of a line
    of tool output, could pass through them unchanged. Nothing is written
    unless this finds nothing.

    Raises:
        SystemExit: naming what was found and where.
    """
    found = [match.group(0) for match in LEFT_BEHIND.finditer(text)]
    if getpass.getuser().lower() in text.lower():
        found.append("this user's name")
    found += [address for address in EMAIL.findall(text) if address not in KEEP_EMAILS and address != "user@example.invalid"]
    if found:
        raise SystemExit(f"{where} still holds {sorted(set(found))}; nothing was written")


def main(argv: list[str] | None = None) -> int:
    """Write the fixture and its meta file; return 0."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--logs", type=Path, required=True, help="the run's Inspect log folder")
    parser.add_argument("--sample", help="the scenario id, if the folder holds more than one run")
    parser.add_argument("--name", required=True, help="the fixture's name, without an extension")
    parser.add_argument("--about", required=True, help="one sentence on what the stream holds")
    parser.add_argument("--out", type=Path, required=True, help="the fixtures folder")
    args = parser.parse_args(argv)
    if Path(args.name).name != args.name:
        raise SystemExit("--name is a file name, not a path")

    run, metadata, prompt = the_run(args.logs, args.sample)
    target = metadata["target"]
    found = PROJECT_DIR.search(run["events"])
    project_dir = found.group(0) if found else None
    if target == "claude-code" and not project_dir:  # its init event always names the folder, and the session folder is named after it
        raise SystemExit("the stream doesn't name its project folder, so its paths can't be replaced")
    events, skipped = fixture(run, project_dir, target)
    meta = clean({
        "case": args.name,
        "about": args.about,
        "from": "a `whitney run`, saved by spikes/run_to_fixture.py",
        "target": target,
        "harness_version": metadata.get("harness_version"),
        "model": metadata.get("model"),
        "prompt": prompt,
        "harness_permissions": metadata.get("harness_permissions"),
        "staged_files": sorted(metadata.get("evaluation_assets") or {}),
        "exit_code": run.get("exit_code"),
        "ended_early": run.get("ended_early"),
        "permission_denials": run.get("permission_denials"),
        "skipped_lines": skipped,
        "trimmed": f"strings over {LONG_TEXT} characters cut; event types kept: all",
        "placeholders": "as the other fixtures in this folder",
    }, project_dir, target)
    meta_text = json.dumps(meta, indent=2) + "\n"
    check_nothing_left(events, f"{args.name}.jsonl")
    check_nothing_left(meta_text, f"{args.name}.meta.json")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{args.name}.jsonl").write_text(events, encoding="utf-8")
    (args.out / f"{args.name}.meta.json").write_text(meta_text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
