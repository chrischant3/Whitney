"""Claude Code CLI as an Inspect agent, for the claude-code target (local execution).

The run sees only the project: an empty temporary HOME and
`--setting-sources project` keep the user's own Claude settings, CLAUDE.md,
memory, plugins and MCP servers out of it, and the environment carries
nothing but PATH, the API key and a fixed git identity with none of the
user's git config. Model calls go to the Anthropic API with that key.

Like the Kilo agent, it runs without --dangerously-skip-permissions: the
eval grants only the permissions the task needs, through --settings.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path

from inspect_ai.agent import Agent, AgentState, agent
from inspect_ai.model import ChatMessageAssistant
from inspect_ai.util import sandbox, store

from whitney.agent import (
    PROJECT_DIR,
    RUN_STORE_KEY,
    copy_outputs,
    redact_run,
    run_process,
    staged_project,
)
from whitney.git_env import isolated_git_env

MODEL_PREFIX = "anthropic/"


@agent
def claude_code(
    model: str,
    harness_permissions: dict[str, list[str]] | None = None,
    local_project_files: dict[str, str] | None = None,
    local_output_targets: dict[str, str] | None = None,
    timeout: int = 300,
    api_key_env: str = "ANTHROPIC_API_KEY",
) -> Agent:
    """Run `claude -p` once on the sample's prompt, in the project folder.

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
        api_key_env: environment variable holding the Anthropic API key; it
            reaches Claude Code as ANTHROPIC_API_KEY.

    Side effects: stores exit_code, the stream-json events, stderr,
    ended_early, permission_denials and api_key_source under RUN_STORE_KEY,
    with the API key's value redacted.

    Raises:
        ValueError: model isn't anthropic/<model> (at construction), or
            api_key_env isn't set (when the run starts).
    """
    if not model.startswith(MODEL_PREFIX):
        raise ValueError(f"the claude-code target runs Anthropic models: use --model anthropic/<model>, got {model!r}")
    command = _claude_command(model.removeprefix(MODEL_PREFIX), harness_permissions or {})

    async def execute(state: AgentState) -> AgentState:
        key = os.environ.get(api_key_env)
        if not key:
            raise ValueError(f"{api_key_env} is not set; put it in .env or the environment")
        sbox = sandbox()
        prompt = [*command, state.messages[-1].text]
        if local_project_files is None:
            project_dir = Path((await sbox.exec(["pwd"], cwd=PROJECT_DIR)).stdout.strip())
            run = await _run(prompt, project_dir, key, timeout)
        else:
            with staged_project(local_project_files) as project_dir:
                run = await _run(prompt, project_dir, key, timeout)
                if local_output_targets:
                    run["copied_outputs"], run["missing_outputs"] = await copy_outputs(
                        sbox, project_dir, local_output_targets
                    )
        store().set(RUN_STORE_KEY, run)
        if run.get("final_text"):
            state.messages.append(ChatMessageAssistant(content=run["final_text"], model=model))
        return state

    return execute


def _claude_command(model_id: str, harness_permissions: dict[str, list[str]]) -> list[str]:
    return [
        "claude", "-p",
        "--setting-sources", "project",
        "--strict-mcp-config",  # no --mcp-config, so no MCP servers at all
        "--settings", json.dumps({"permissions": harness_permissions}),
        "--model", model_id,
        "--output-format", "stream-json", "--verbose",  # stream-json needs --verbose with -p
    ]  # fmt: skip


async def _run(command: list[str], project_dir: Path, key: str, timeout: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="claude-eval-home-") as home:
        env = {"PATH": os.environ.get("PATH", ""), "HOME": home, "PWD": str(project_dir), "ANTHROPIC_API_KEY": key}
        # stdin closed: with it open, claude -p waits for piped input before starting.
        run = await run_process(command, project_dir, {**env, **isolated_git_env(project_dir)}, timeout, "claude -p", asyncio.subprocess.DEVNULL)
    run = redact_run(run, [key])  # its Bash tool can print the key, and the transcript goes into the eval log
    return {**run, **_outcome(run["events"])}


def _outcome(events: str) -> dict:
    """What the transcript's init and final result events say about the run.

    ended_early is True unless a result event reports success with no
    permission denials: a denial means the harness, not the agent, decided.
    """
    init, result = {}, None
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            init = event
        elif event.get("type") == "result":
            result = event
    denials = (result or {}).get("permission_denials") or []
    succeeded = bool(result) and result.get("subtype") == "success" and not result.get("is_error")
    return {
        "ended_early": not succeeded or bool(denials),
        "permission_denials": denials,
        "api_key_source": init.get("apiKeySource"),
        "final_text": (result or {}).get("result"),
    }
