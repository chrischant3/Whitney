"""Spike, Phase 5 Task 31 follow-ups (2026-11-12): decisions 6 and 7.

Decision 6, the Kilo Gateway proxy's base URL. With Whitney's logging proxy as
the `kilo` provider's `baseURL` in the KILO_CONFIG overlay (`--pure` kept),
which form of the URL makes Kilo's model-list calls and its chat call reach
the right upstream path? Each form is tried with `kilo models kilo` (no model
call) and with one `kilo run`. The proxy forwards every path unchanged to
https://api.kilo.ai, so its records show exactly where each call went.

Decision 7, `CLAUDE.md` imports. For each project, Claude Code is run once in
the project folder itself ("real": every file there, as a team's own run
would see it) and once in what Whitney's claude-code loader stages from it
("whitney": `load_claude_code_project_files`, then `staged_project`, as
`whitney run --project-path` does). Both go through `claude_code._run`, so
both get the same temporary HOME, `--setting-sources project` and git
isolation. The proxy (ANTHROPIC_BASE_URL) records the request, and the
analysis says which marker reached the model in each.

The proxy is prompt_capture's: it records a request's path, its header
*names* and its body, never a header's value. Kilo's plugin route is not
used: its chat.params hook is handed the key.

    .venv/bin/python spikes/capture_followups.py --list
    .venv/bin/python spikes/capture_followups.py --out <scratch> --case gw-models
    .venv/bin/python spikes/capture_followups.py --out <scratch> --analyse
    .venv/bin/python spikes/capture_followups.py --out <scratch> --fixtures tests/data/prompt-capture
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from unittest import mock

import prompt_capture as pc
from inspect_ai.agent import AgentState
from inspect_ai.model import ChatMessageUser
from skill_agent_shapes import BASE_FILES, _home_with, _init_repo

import whitney.agent as kilo_agent
import whitney.claude_code as claude_code
from whitney.project_files import load_claude_code_project_files
from whitney.tasks import DEFAULT_HARNESS_PERMISSIONS

# ---- decision 6: the Gateway base URL ----

# The forms tried: the path Whitney's credentials default to, the origin alone, and Kilo's own OpenRouter-style path.
GATEWAY_FORMS = {"path": "/api/gateway", "origin": "", "openrouter": "/api/openrouter"}


def _gateway_overlay(base_url: str) -> dict:
    return kilo_agent.local_config_overlay(
        pc.GATEWAY_MODEL, DEFAULT_HARNESS_PERMISSIONS["kilo"], None, base_url, global_provider=kilo_agent.global_provider_settings()
    )


def run_gateway_models(folder: Path) -> dict:
    """No model: `kilo models kilo --pure` once per base URL form, through the proxy; what was requested and answered."""
    results = {}
    for form, path in GATEWAY_FORMS.items():
        sub = folder / form
        with contextlib.ExitStack() as stack:
            base = stack.enter_context(pc.logging_proxy(sub / "proxy", pc.GATEWAY_UPSTREAM)) + path
            project = stack.enter_context(kilo_agent.staged_project({**BASE_FILES, **pc.KILO_FILES}))
            config = stack.enter_context(tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8"))
            stack.enter_context(_home_with({}, {}))
            stack.enter_context(mock.patch.object(kilo_agent, "_kilo_command", lambda *a, **k: ["kilo", "models", "kilo", "--pure"]))
            _init_repo(project)
            json.dump(_gateway_overlay(base), config)
            config.flush()
            state = AgentState(messages=[ChatMessageUser(content="unused")])
            run = asyncio.run(kilo_agent._run_local_process(project, state, "unused", False, {"KILO_CONFIG": config.name}, pc.TIMEOUT))
        lines = [line for line in run["events"].splitlines() if line.startswith("kilo/")]
        results[form] = {"base_path": path, "exit_code": run["exit_code"], "models_listed": len(lines),
                         "haiku_listed": "kilo/anthropic/claude-haiku-4.5" in lines, "requests": _proxy_paths(sub / "proxy"),
                         "stderr_tail": run["stderr"][-600:]}
    return results


def run_gateway_run(form: str, folder: Path) -> dict:
    """One `kilo run --pure` on the Gateway route with the proxy's base URL in `form`."""
    with mock.patch.object(pc, "GATEWAY_PATH", GATEWAY_FORMS[form]):
        case = pc.Case(f"gw-run-{form}", f"Gateway, --pure kept, base URL form {form!r}", "kilo",
                       files=pc.KILO_FILES, model=pc.GATEWAY_MODEL, proxy=pc.GATEWAY_UPSTREAM)
        result = pc.run_kilo(case, folder)
    return {**result, "base_path": GATEWAY_FORMS[form], "requests": _proxy_paths(folder / "proxy")}


def _proxy_paths(proxy: Path) -> list[dict]:
    found = []
    for path in sorted(proxy.glob("*.request.json")):
        record = json.loads(path.read_text())
        found.append({"method": record["method"], "path": record["path"], "status": record["status"], "body_bytes": record["body_bytes"]})
    return found


# ---- decision 7: CLAUDE.md imports ----

# Each marker names where it was put, so the request shows which file reached the model.
M = {
    "claude_md_own": "IMPORT-CM-OWN-4101",
    "agents_md": "IMPORT-AGENTS-4102",
    "rules": "IMPORT-RULES-4103",
    "hop2": "IMPORT-HOP2-4104",
    "hop3": "IMPORT-HOP3-4105",
    "hop4": "IMPORT-HOP4-4106",
    "hop5": "IMPORT-HOP5-4107",
    "hop6": "IMPORT-HOP6-4108",
    "code_span": "IMPORT-SPAN-4110",
    "fenced": "IMPORT-FENCED-4111",
    "home": "IMPORT-HOME-4112",
    "absolute_outside": "IMPORT-ABS-OUT-4113",
    "relative_outside": "IMPORT-REL-OUT-4114",
    "dot_claude_md_own": "IMPORT-DOTCM-4115",
    "from_dot_claude": "IMPORT-FROM-DOT-4116",
    "claude_rules_dir": "IMPORT-RULESDIR-4117",
    "env_file": "IMPORT-ENV-4118",
    "symlink": "IMPORT-LINK-4119",
    "agents_nested": "IMPORT-AGNEST-4120",
    "absolute_inside": "IMPORT-ABS-IN-4121",
    "agents_only_import": "IMPORT-AGONLY-4122",
}


def _md(marker: str, *imports: str) -> str:
    return f"# Notes\n\n- Marker: {marker}\n" + "".join(f"- See @{path}\n" for path in imports)


def _p1_files(project: Path, outside: Path) -> dict[str, str]:
    """Every import form at once: own text, AGENTS.md, a chain past four hops, code spans, outside paths, .claude/."""
    return {
        "CLAUDE.md": (
            f"# Project\n\n- Own marker: {M['claude_md_own']}\n- Shared rules: @AGENTS.md\n- Team rules: @docs/rules.md\n"
            f"- Literal, not an import: `@docs/span.md`\n\n```\n@docs/fenced.md\n```\n\n"
            f"- Home: @~/home-rule.md\n- Absolute outside: @{outside / 'abs.md'}\n- Relative outside: @../outside/rel.md\n"
            f"- Absolute inside: @{project / 'docs' / 'abs-in.md'}\n- Env: @config/.env.shared\n- Link: @docs/link.md\n"
        ),
        "AGENTS.md": _md(M["agents_md"], "docs/agents-extra.md"),
        "docs/agents-extra.md": _md(M["agents_nested"]),
        "docs/rules.md": _md(M["rules"], "sub/two.md"),          # hop 1, its import relative to docs/
        "docs/sub/two.md": _md(M["hop2"], "three.md"),           # hop 2
        "docs/sub/three.md": _md(M["hop3"], "four.md"),          # hop 3
        "docs/sub/four.md": _md(M["hop4"], "five.md"),           # hop 4
        "docs/sub/five.md": _md(M["hop5"], "six.md"),            # hop 5
        "docs/sub/six.md": _md(M["hop6"]),                       # hop 6
        "docs/span.md": _md(M["code_span"]),
        "docs/fenced.md": _md(M["fenced"]),
        "docs/abs-in.md": _md(M["absolute_inside"]),
        "docs/target.md": _md(M["symlink"]),
        "config/.env.shared": f"SHARED_MARKER={M['env_file']}\n",
        ".claude/CLAUDE.md": f"# Folder instructions\n\n- Marker: {M['dot_claude_md_own']}\n- From here: @../docs/from-dot.md\n",
        "docs/from-dot.md": _md(M["from_dot_claude"]),
        ".claude/rules/style.md": _md(M["claude_rules_dir"]),
    }


@dataclass
class ImportCase:
    """One Claude Code project, run as itself ("real") or as Whitney stages it ("whitney")."""

    id: str
    about: str
    project: str  # "p1", "p2" or "p3"
    mode: str  # "real" or "whitney"
    notes: list[str] = field(default_factory=list)


def _project_files(name: str, project: Path, outside: Path) -> dict[str, str]:
    if name == "p1":
        return _p1_files(project, outside)
    if name == "p2":  # CLAUDE.md that is only an import of AGENTS.md
        return {"CLAUDE.md": "@AGENTS.md\n", "AGENTS.md": _md(M["agents_md"])}
    if name == "p3":  # AGENTS.md alone, importing a file
        return {"AGENTS.md": _md(M["agents_md"], "docs/agents-only.md"), "docs/agents-only.md": _md(M["agents_only_import"])}
    raise ValueError(name)


def _build_project(name: str, root: Path) -> tuple[Path, Path]:
    """The project on disk, in `root`/project, with the outside files in `root`/outside; returns both."""
    project, outside = root / "project", root / "outside"
    outside.mkdir(parents=True)
    (outside / "abs.md").write_text(_md(M["absolute_outside"]), encoding="utf-8")
    (outside / "rel.md").write_text(_md(M["relative_outside"]), encoding="utf-8")
    for relative, text in {**BASE_FILES, **_project_files(name, project, outside)}.items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if name == "p1":
        (project / "docs" / "link.md").symlink_to("target.md")
    _init_repo(project)
    return project, outside


def _with_home_file(folder: Path):
    """A replacement for claude_code.run_process: the proxy as ANTHROPIC_BASE_URL, and ~/home-rule.md in the temporary HOME."""
    original = claude_code.run_process

    async def run(command, cwd, env, timeout, name, stdin=None):
        (Path(env["HOME"]) / "home-rule.md").write_text(_md(M["home"]), encoding="utf-8")
        return await original(command, cwd, {**env, "ANTHROPIC_BASE_URL": folder.read_text().strip()}, timeout, name, stdin)

    return run


def run_import_case(case: ImportCase, folder: Path) -> dict:
    """Build the project, then run claude -p in it (real) or in the loader's staged copy (whitney), through the proxy."""
    root = folder / "tree"
    project, _ = _build_project(case.project, root)
    argv = [*claude_code._claude_command(pc.CLAUDE_MODEL, DEFAULT_HARNESS_PERMISSIONS["claude-code"]), "--", pc.PONG]
    staged, refused = None, None
    with contextlib.ExitStack() as stack:
        url_file = folder / "proxy-url.txt"
        url_file.write_text(stack.enter_context(pc.logging_proxy(folder / "proxy", pc.ANTHROPIC_UPSTREAM)))
        stack.enter_context(mock.patch.object(claude_code, "run_process", _with_home_file(url_file)))
        if case.mode == "real":
            where = project
        else:
            try:
                staged = load_claude_code_project_files(project)
            except ValueError as error:
                refused = str(error)
                return {"mode": case.mode, "refused": refused}
            where = stack.enter_context(kilo_agent.staged_project({**BASE_FILES, **staged}))
            _init_repo(where)
        run = asyncio.run(claude_code._run(argv, where, os.environ["ANTHROPIC_API_KEY"], pc.TIMEOUT))
    url_file.unlink()
    (folder / "events.jsonl").write_text(run["events"], encoding="utf-8")
    return {"mode": case.mode, "project": case.project, "staged": sorted(staged) if staged is not None else None,
            "exit_code": run["exit_code"], "final_text": run.get("final_text"), "claude_code_version": _claude_version()}


def _claude_version() -> str:
    import claude_code_shapes

    return claude_code_shapes._claude_version()


IMPORT_CASES = [
    ImportCase("imp-p1-real", "Every import form, Claude Code in the project folder", "p1", "real"),
    ImportCase("imp-p1-whitney", "Every import form, Claude Code in what the loader staged", "p1", "whitney"),
    ImportCase("imp-p2-whitney", "CLAUDE.md is only @AGENTS.md, in what the loader staged (the real run is prompt_capture's cc-claude-md-import)", "p2", "whitney"),
    ImportCase("imp-p3-real", "AGENTS.md alone importing a file, in the project folder", "p3", "real"),
    ImportCase("imp-p3-whitney", "AGENTS.md alone importing a file, in what the loader staged", "p3", "whitney"),
    ImportCase("imp-p2-whitney-fixed", "As imp-p2-whitney, after the loader stages imports", "p2", "whitney"),
    ImportCase("imp-p3-whitney-fixed", "As imp-p3-whitney, after the loader stages imports", "p3", "whitney"),
]


def analyse_import(folder: Path) -> dict:
    """Which markers the first model request carried, and under which `Contents of` headers."""
    requests = [json.loads(p.read_text()) for p in sorted((folder / "proxy").glob("*.request.json"))]
    first = next((r["body"] for r in requests if r["method"] == "POST" and isinstance(r["body"], dict) and r["body"].get("messages")), None)
    if first is None:
        return {"requests": len(requests)}
    text = pc._text_of(first)
    headers = sorted({line.split("Contents of ", 1)[1].split(" (")[0] for line in text.splitlines() if "Contents of " in line})
    project = str((folder / "tree" / "project").resolve())
    return {
        "requests": len(requests),
        "markers_sent": sorted(name for name, marker in M.items() if marker in text),
        "contents_headers": [h.replace(project, "<project>").replace(str(folder.resolve()), "<case>") for h in headers],
    }


# ---- running ----

CASE_IDS = ["gw-models", *(f"gw-run-{form}" for form in GATEWAY_FORMS), *(c.id for c in IMPORT_CASES)]


def run(case_id: str, out: Path) -> dict:
    folder = out / case_id
    if folder.exists():
        raise SystemExit(f"{folder} exists; every case is run once, so remove it by hand to run it again")
    folder.mkdir(parents=True)
    if case_id == "gw-models":
        result = run_gateway_models(folder)
    elif case_id.startswith("gw-run-"):
        result = run_gateway_run(case_id.removeprefix("gw-run-"), folder)
        (folder / "events.jsonl").write_text(result.pop("events"), encoding="utf-8")
        result.pop("stderr", None)
    else:
        result = run_import_case(next(c for c in IMPORT_CASES if c.id == case_id), folder)
    summary = {"case": case_id, "date": date.today().isoformat(), **result}
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def analyse(out: Path) -> dict:
    report = {}
    for case_id in CASE_IDS:
        folder = out / case_id
        if not (folder / "summary.json").is_file():
            continue
        summary = json.loads((folder / "summary.json").read_text())
        if case_id.startswith("imp-") and not summary.get("refused"):
            summary["analysis"] = analyse_import(folder)
        summary.pop("project_dir", None)
        summary.pop("home_dir", None)
        summary.pop("project_files_after", None)
        report[case_id] = summary
    (out / "analysis.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def write_fixture(out: Path, fixtures: Path) -> list[Path]:
    """The Gateway request sent with the origin-alone base URL, sanitised as prompt_capture's fixtures are.

    It is the request a captured Gateway run would hold: in Anthropic's format,
    to /api/openrouter/messages. prompt_capture's kilo-gateway fixture was sent
    with /api/gateway in the base URL, which made Kilo use the OpenAI format.
    """
    folder = out / "gw-run-origin"
    summary = json.loads((folder / "summary.json").read_text())
    summary |= {"target": "kilo", "about": "Kilo Gateway route, --pure kept, the kilo provider's baseURL the proxy's origin alone",
                "model": pc.GATEWAY_MODEL, "prompt": pc.PONG, "project_files": {**BASE_FILES, **pc.KILO_FILES}}
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    with mock.patch.object(pc, "FIXTURE_FOR", {"gw-run-origin": "kilo-gateway-origin"}):
        return pc.write_fixtures(out, fixtures)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--case", action="append")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--analyse", action="store_true")
    parser.add_argument("--fixtures", type=Path, help="write the sanitised Gateway fixture here from the saved gw-run-origin case")
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(CASE_IDS))
        return 0
    if not args.out or args.out.resolve().is_relative_to(pc.REPO):
        parser.error("--out is needed, outside the repo")
    for case_id in args.case or []:
        if case_id not in CASE_IDS:
            parser.error(f"unknown case {case_id}")
        print(case_id, json.dumps(run(case_id, args.out))[:400])
    if args.analyse:
        print(json.dumps(analyse(args.out), indent=2))
    if args.fixtures:
        for path in write_fixture(args.out, args.fixtures):
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
