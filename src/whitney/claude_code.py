"""Claude Code CLI as an Inspect agent, for the claude-code target, run locally or in the Docker sandbox.

The run sees only the project: an empty temporary HOME and
`--setting-sources project` keep the user's own Claude settings, CLAUDE.md,
memory, plugins and MCP servers out of it, and the environment carries
nothing but PATH, the API key and a fixed git identity with none of the
user's git config. Model calls go to the Anthropic API with that key.

That is a local run. In Docker mode Claude Code runs in a container with no
network and nothing of the host's mounted, and Inspect's bridge answers its
model calls, so the key stays on the host (execute_in_docker).

Like the Kilo agent, it runs without --dangerously-skip-permissions: the
eval grants only the permissions the task needs, through --settings.
"""

import asyncio
import importlib.resources
import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from inspect_ai.agent import Agent, AgentState, agent, sandbox_agent_bridge
from inspect_ai.log import transcript
from inspect_ai.model import ChatMessageAssistant
from inspect_ai.util import sandbox, store

from whitney import capture, capture_proxy
from whitney.agent import (
    PROJECT_DIR,
    RUN_STORE_KEY,
    check_no_inherited_instructions,
    copy_outputs,
    redact,
    redact_run,
    run_process,
    staged_project,
)
from whitney.git_env import isolated_git_env
from whitney.refusals import Rule, asked, expected

MODEL_PREFIX = "anthropic/"
# The Docker sandbox for this target: Claude Code pinned, no network (docs/spike-findings.md, "Phase 5 Task 36a").
COMPOSE_FILE = Path(str(importlib.resources.files("whitney") / "sandbox" / "claude-code" / "compose.yaml"))
DOCKER_HOME = "/tmp/claude-eval-home"  # empty, and made for each run: the container's own HOME holds nothing either
# The key Claude Code is given in the container. The real one stays with Inspect, on the host.
BRIDGE_KEY = "sk-none"
VERSION_TIMEOUT = 30  # seconds; as cli.VERSION_TIMEOUT: `--version` answers at once
MAX_VERSION_LENGTH = 200  # as cli.MAX_VERSION_LENGTH: a long first line is something else


@agent
def claude_code(
    model: str,
    harness_permissions: dict[str, list[str]] | None = None,
    local_project_files: dict[str, str] | None = None,
    local_output_targets: dict[str, str] | None = None,
    timeout: int = 300,
    api_key_env: str | None = "ANTHROPIC_API_KEY",
    execution: str = "local",
    capture_prompt: bool = False,
    thinking: bool = False,
    capture_texts: dict[str, str] | None = None,
) -> Agent:
    """Run `claude -p` once on the sample's prompt, in the project folder.

    A local run starts the host's Claude Code with the API key. A Docker run
    starts the image's Claude Code in the sandbox, which has no network: its
    model calls go to Inspect's bridge inside the container, and Inspect
    answers each with the eval's --model, so the key never enters the
    container. Both use the same command line.

    Args:
        model: the model under evaluation, as anthropic/<model>.
        harness_permissions: Claude Code's `permissions` settings block, e.g.
            {"allow": ["Bash", "Edit"], "deny": [...]}, so the agent can work
            headlessly without being granted more than the eval needs.
        local_project_files: optional files to stage in a temporary host
            project instead of the sandbox's project (for code-generation evals).
        local_output_targets: files to copy from that staged project into the
            active Inspect sandbox after the run.
        timeout: seconds before the run is killed.
        api_key_env: local mode: the environment variable holding the
            Anthropic API key; it reaches Claude Code as ANTHROPIC_API_KEY.
            Must be None in Docker mode.
        execution: "local" or "docker".
        capture_prompt: record what was sent to the model (whitney.capture):
            locally through the logging proxy, as ANTHROPIC_BASE_URL; in
            Docker from Inspect's own log of the bridged calls.
        thinking: tell Claude Code to show its thinking (CLAUDE_CODE_THINKING
            in --settings), so the text is in the stream; its level stays
            Claude Code's own. The task asks Inspect for a budget in Docker.
        capture_texts: what to look for in the requests (capture.texts_to_find), redacted.

    Side effects: stores exit_code, the stream-json events, stderr,
    ended_early, permission_denials and api_key_source under RUN_STORE_KEY,
    with the API key's value redacted (in Docker mode the container never
    has it, and the host's is redacted all the same). A denied skill that a
    `Skill(<name>)` rule in harness_permissions' `deny` list switched off
    doesn't make ended_early true (see _outcome). A Docker run also stores
    harness_version, what the container's `claude --version` printed.
    With capture_prompt or thinking, it also stores `sent` (capture only)
    and `thinking` (capture.run_entries); with neither, a run records only
    the fields above and starts no proxy.

    Raises:
        ValueError: model isn't anthropic/<model>, execution is neither, or
            Docker mode is given api_key_env or local_project_files (all at
            construction); or, in local mode, api_key_env isn't set (when
            the run starts).
    """
    if not model.startswith(MODEL_PREFIX):
        raise ValueError(f"the claude-code target runs Anthropic models: use --model anthropic/<model>, got {model!r}")
    if execution not in ("local", "docker"):
        raise ValueError("execution must be 'local' or 'docker'")
    if execution == "docker" and api_key_env:
        raise ValueError(
            "api_key_env applies to local execution; in Docker mode Inspect serves --model through the bridge with its own credentials"
        )
    if execution == "docker" and local_project_files is not None:
        raise ValueError("files staged on the host can't be used in Docker mode, where the project is the container's")
    command = _claude_command(model.removeprefix(MODEL_PREFIX), harness_permissions or {}, thinking)
    asked_for = asked("claude-code", harness_permissions or {})
    told = capture.told_harness(f"--settings {json.dumps(capture.CLAUDE_CODE_THINKING)}", execution == "docker")
    watch = capture.watch_for(capture_prompt, thinking, capture_texts, told, model)

    async def execute_in_docker(state: AgentState) -> AgentState:
        # "inspect" is the bridge's own name for the eval's --model. Claude Code is told the model's real
        # name, as in a local run: the bridge answers any name with the eval's model, and a name Claude
        # Code doesn't know makes it misprice the run (spike, 2026-11-11).
        async with sandbox_agent_bridge(state, model="inspect") as bridge:
            sbox = sandbox()
            env = {
                "HOME": DOCKER_HOME,
                "ANTHROPIC_BASE_URL": f"http://localhost:{bridge.port}",
                "ANTHROPIC_API_KEY": BRIDGE_KEY,
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",  # there is no network for it to reach
            }
            await sbox.exec(["mkdir", "-p", DOCKER_HOME])
            version = await _container_version(sbox)
            # The container was never given the host's key, so this should find nothing. The record goes
            # into the eval log, so it is redacted as a local run's is.
            secrets = list(filter(None, [os.environ.get("ANTHROPIC_API_KEY")]))
            start = len(transcript().events) if watch else 0
            try:
                result = await sbox.exec([*command, "--", state.messages[-1].text], cwd=PROJECT_DIR, env=env, timeout=timeout)
                run = {"exit_code": result.returncode, "events": result.stdout, "stderr": result.stderr}
            except TimeoutError:  # Inspect's Docker exec raises; a local run records 124 (agent.run_process)
                run = {"exit_code": 124, "events": "", "stderr": f"claude -p timed out after {timeout}s"}
            if watch:
                sent = capture.from_inspect_log(
                    list(transcript().events)[start:], transcript().attachments, watch.texts, lambda text: redact(text, secrets)
                )
                run |= capture.run_entries(watch, sent, run["events"], docker=True)
            run = redact_run(run, secrets)
            store().set(RUN_STORE_KEY, {**run, **_outcome(run["events"], asked_for), **({"harness_version": version} if version else {})})
        return bridge.state

    async def execute(state: AgentState) -> AgentState:
        key = os.environ.get(api_key_env)
        if not key:
            raise ValueError(f"{api_key_env} is not set; put it in .env or the environment")
        sbox = sandbox()
        prompt = [*command, "--", state.messages[-1].text]  # "--": a prompt such as "--version" stays the prompt
        if local_project_files is None:
            project_dir = Path((await sbox.exec(["pwd"], cwd=PROJECT_DIR)).stdout.strip())
            run = await _run(prompt, project_dir, key, timeout, asked_for, watch)
        else:
            with staged_project(local_project_files) as project_dir:
                run = await _run(prompt, project_dir, key, timeout, asked_for, watch)
                if local_output_targets:
                    run["copied_outputs"], run["missing_outputs"] = await copy_outputs(
                        sbox, project_dir, local_output_targets
                    )
        store().set(RUN_STORE_KEY, run)
        if run.get("final_text"):
            state.messages.append(ChatMessageAssistant(content=run["final_text"], model=model))
        return state

    return execute_in_docker if execution == "docker" else execute


async def _container_version(sbox) -> str | None:
    """The first line the container's `claude --version` prints, cut as cli.harness_version cuts the host's; None if it fails."""
    try:
        done = await sbox.exec(["claude", "--version"], timeout=VERSION_TIMEOUT)
    except TimeoutError:
        return None
    lines = done.stdout.strip().splitlines() if done.returncode == 0 else []
    return lines[0].strip()[:MAX_VERSION_LENGTH] or None if lines else None


def _claude_command(model_id: str, harness_permissions: dict[str, list[str]], thinking: bool = False) -> list[str]:
    settings = {"permissions": harness_permissions, **(capture.CLAUDE_CODE_THINKING if thinking else {})}
    return [
        "claude", "-p",
        "--setting-sources", "project",
        "--strict-mcp-config",  # no --mcp-config, so no MCP servers at all
        "--settings", json.dumps(settings),
        "--model", model_id,
        "--output-format", "stream-json", "--verbose",  # stream-json needs --verbose with -p
    ]  # fmt: skip


async def _run(
    command: list[str], project_dir: Path, key: str, timeout: int, asked_for: Sequence[Rule] = (),
    watch: capture.Watch | None = None,
) -> dict:
    check_no_inherited_instructions(project_dir, claude_code=True)
    proxy = capture_proxy.proxied(watch, capture_proxy.CLAUDE_CODE_UPSTREAM, lambda text: redact(text, [key]))
    with tempfile.TemporaryDirectory(prefix="claude-eval-home-") as home, proxy as (origin, captured):
        env = {"PATH": os.environ["PATH"], "HOME": home, "PWD": str(project_dir), "ANTHROPIC_API_KEY": key}
        if origin:
            env["ANTHROPIC_BASE_URL"] = origin
        # stdin closed: with it open, claude -p waits for piped input before starting.
        run = await run_process(command, project_dir, {**env, **isolated_git_env(project_dir)}, timeout, "claude -p", asyncio.subprocess.DEVNULL)
    run |= capture.run_entries(watch, captured.record() if captured else None, run["events"], docker=False)
    run = redact_run(run, [key])  # its Bash tool can print the key, and the transcript goes into the eval log
    return {**run, **_outcome(run["events"], asked_for)}


def _outcome(events: str, asked_for: Sequence[Rule]) -> dict:
    """What the transcript's init and result events say about the run.

    A run with a background subagent has more than one result event: the main
    turn ending, then the answer once the subagent finished
    (docs/spike-findings.md, Claude Code 2.1.322). So permission_denials
    gathers every result event's list, once per tool_use_id, and final_text
    is the last one's text. ended_early is True unless every result event
    reports success and none lists a denial: a denial means the harness, not
    the agent, decided.

    One kind of denial doesn't count: a skill that `asked_for`, the eval
    file's own deny rules, switched off (whitney.refusals). The file asked
    for that refusal, and the agent carries on without the skill, so the
    result is still the agent's. It stays in permission_denials.
    """
    init, results = {}, []
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            init = event
        elif event.get("type") == "result":
            results.append(event)
    denials = _all_denials(results)
    succeeded = bool(results) and all(r.get("subtype") == "success" and not r.get("is_error") for r in results)
    return {
        "ended_early": not succeeded or any(not expected(asked_for, denial) for denial in denials),
        "permission_denials": denials,
        "api_key_source": init.get("apiKeySource"),
        "final_text": results[-1].get("result") if results else None,
    }


def _all_denials(results: list[dict]) -> list[dict]:
    """Every result event's permission denials, in order, each tool_use_id once; one without an id is always kept."""
    denials, seen = [], set()
    for result in results:
        for denial in result.get("permission_denials") or []:
            key = denial.get("tool_use_id")
            if key is None or key not in seen:
                seen.add(key)
                denials.append(denial)
    return denials
