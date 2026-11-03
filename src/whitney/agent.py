"""Kilo CLI as an Inspect agent, running locally or in the Docker bridge sandbox.

Modelled on inspect_swe's opencode() agent, with deliberate differences:
- no --dangerously-skip-permissions, because permissions are what we test;
- Docker mode keeps Kilo's connection isolated behind Inspect's bridge;
  local mode uses the user's global connection settings, or a provider key
  the user names (api_key_env), layered over them;
- a non-zero exit is recorded as evidence rather than raised, because an
  auto-rejected permission (exit 1) is an outcome we score.
"""

import asyncio
import contextlib
import json
import os
import signal
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path

from inspect_ai.agent import Agent, AgentState, agent, sandbox_agent_bridge
from inspect_ai.model import ChatMessageAssistant
from inspect_ai.util import sandbox, store

PROJECT_DIR = "project"
COMPOSE_FILE = Path(__file__).parent.parent.parent / "docker" / "compose.yaml"
RUN_STORE_KEY = "kilo_run"

# Kilo providers whose request format Inspect's bridge serves, each with how a
# model id becomes the id in that provider's Kilo catalog: the Kilo Gateway
# writes Anthropic versions with dots (claude-haiku-4.5), Kilo's Anthropic
# catalog with dashes (claude-haiku-4-5). Kilo's own `kilo` provider can't be
# used: it appends a Gateway path the bridge doesn't serve (tasks/plan.md T16).
BRIDGE_PROVIDERS = {
    "anthropic": lambda model_id: model_id.replace(".", "-"),
    "openai": lambda model_id: model_id,
}


@agent
def kilo(
    model: str,
    execution: str = "local",
    harness_permissions: dict | None = None,
    local_project_files: dict[str, str] | None = None,
    local_output_targets: dict[str, str] | None = None,
    auto: bool = False,
    timeout: int = 300,
    api_key_env: str | None = None,
    base_url: str | None = None,
) -> Agent:
    """Run `kilo run` once on the sample's prompt, in PROJECT_DIR.

    Args:
        model: the model under evaluation, as provider/model. Locally it's
            passed to `kilo run -m`. In Docker mode it's the Inspect model that
            answers through the bridge, and Kilo is told the matching name in
            a format the bridge serves (see bridge_kilo_model).
        execution: "local" uses the user's Kilo config; "docker" routes calls
            through Inspect's model bridge.
        harness_permissions: temporary permission overlay, so the agent can
            work headlessly. Project-level permission settings take precedence.
        local_project_files: optional files to materialize in a temporary host
            project, independent of the Inspect sandbox (for code-generation evals).
        local_output_targets: files to copy from that temporary host project into
            the active Inspect sandbox after Kilo finishes.
        auto: pass --auto, which approves every permission not explicitly
            denied, so `ask` behaves like `allow`.
        timeout: seconds before the Kilo run is killed.
        api_key_env: local mode only. Name of the environment variable (e.g.
            ANTHROPIC_API_KEY, from the shell or .env) holding the API key for
            model's provider. Unset, the global Kilo config supplies it.
        base_url: local mode only. Overrides the provider's base URL.

    Side effects: stores the exit code, JSON event stream and stderr under
    RUN_STORE_KEY. Docker mode writes a temporary Kilo global config in the
    sandbox; local mode uses Kilo's normal user config plus a temporary
    KILO_CONFIG overlay (see local_config_overlay).

    Raises:
        ValueError: api_key_env or base_url given in Docker mode, where
            Inspect serves --model through the bridge instead; or, in Docker
            mode, a model bridge_kilo_model can't map.
    """
    if execution == "docker" and (api_key_env or base_url):
        raise ValueError(
            "api_key_env and base_url apply to local execution; in Docker mode Inspect serves --model "
            "through the bridge with its own credentials"
        )
    kilo_cli_model = bridge_kilo_model(model) if execution == "docker" else model

    async def execute(state: AgentState) -> AgentState:
        if execution == "docker":
            async with sandbox_agent_bridge(state, model="inspect") as bridge:
                sbox = sandbox()
                bridge_url = f"http://localhost:{bridge.port}/v1"
                provider = kilo_cli_model.split("/", 1)[0]
                global_config: dict = {
                    "provider": {provider: {"options": {"baseURL": bridge_url, "apiKey": "sk-none"}}}
                }
                if harness_permissions:
                    global_config["permission"] = harness_permissions
                await sbox.exec(["mkdir", "-p", "/root/.config/kilo"])
                await sbox.write_file("/root/.config/kilo/kilo.json", json.dumps(global_config))
                run = await _run_kilo(sbox, state, kilo_cli_model, auto, {"HOME": "/root"}, timeout)
                store().set(RUN_STORE_KEY, run)
            return bridge.state

        if execution != "local":
            raise ValueError("execution must be 'local' or 'docker'")

        sbox = sandbox()
        env = None
        overlay = local_config_overlay(model, harness_permissions, api_key_env, base_url)
        if overlay:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8") as config:
                json.dump(overlay, config)
                config.flush()
                env = {"KILO_CONFIG": config.name}
                run = await _run_local(
                    sbox, state, model, auto, env, timeout, local_project_files, local_output_targets
                )
        else:
            run = await _run_local(
                sbox, state, model, auto, env, timeout, local_project_files, local_output_targets
            )
        store().set(RUN_STORE_KEY, run)
        _append_final_message(state, run, model)
        return state

    return execute


def bridge_kilo_model(model: str) -> str:
    """The provider/model Kilo is told to use in Docker mode, where the bridge answers with `model`.

    kilo/anthropic/claude-haiku-4.5 -> anthropic/claude-haiku-4-5, and
    kilo/openai/gpt-5.1 -> openai/gpt-5.1; Inspect's own anthropic/... and
    openai/... models map to themselves. Kilo picks its system prompt from
    this name, so it matches the answering model's family.

    Raises:
        ValueError: the model isn't Anthropic or OpenAI, whose formats are the ones the bridge serves.
    """
    parts = model.split("/")
    if parts[0] == "kilo":  # a Kilo Gateway model: kilo/<vendor>/<id>
        parts = parts[1:]
    if len(parts) != 2 or parts[0] not in BRIDGE_PROVIDERS or not parts[1]:
        raise ValueError(
            "Docker mode needs an Anthropic or OpenAI model, e.g. kilo/anthropic/claude-haiku-4.5 or "
            f"anthropic/claude-haiku-4-5; got {model!r}. Run other models locally."
        )
    vendor, model_id = parts
    return f"{vendor}/{BRIDGE_PROVIDERS[vendor](model_id)}"


def local_config_overlay(
    model: str,
    harness_permissions: dict | None,
    api_key_env: str | None = None,
    base_url: str | None = None,
    environ: Mapping[str, str] = os.environ,
) -> dict | None:
    """The temporary Kilo config (KILO_CONFIG) layered over the user's global config.

    Kilo merges it above the global config and below the project's
    kilo.jsonc, so the template under test still wins for every key it sets.

    Args:
        model: provider/model; its provider part gets the key and base URL.
        harness_permissions: permissions that let the agent work headlessly.
        api_key_env: name of the environment variable holding the provider's
            API key. The overlay holds a `{env:...}` reference, which Kilo
            resolves, so the key itself is never written to disk or logged.
        base_url: optional base URL for the provider.
        environ: where api_key_env must be set.

    Returns:
        The overlay, or None when there's nothing to add (the global config is used as-is).

    Raises:
        ValueError: api_key_env names a variable that isn't set.
    """
    overlay: dict = {}
    if harness_permissions:
        overlay["permission"] = harness_permissions
    options: dict[str, str] = {}
    if api_key_env:
        if not environ.get(api_key_env):
            raise ValueError(f"{api_key_env} is not set; put it in .env or the environment, or drop --api-key-env")
        options["apiKey"] = f"{{env:{api_key_env}}}"
    if base_url:
        options["baseURL"] = base_url
    if options:
        overlay["provider"] = {model.split("/", 1)[0]: {"options": options}}
    return overlay or None


async def _run_kilo(
    sbox, state: AgentState, model: str, auto: bool, env: dict | None, timeout: int
) -> dict:
    try:
        cwd = (await sbox.exec(["pwd"], cwd=PROJECT_DIR)).stdout.strip()
        env = {**(env or {}), "PWD": cwd}
        result = await sbox.exec(_kilo_command(state, model, auto), cwd=PROJECT_DIR, env=env, timeout=timeout)
        return {"exit_code": result.returncode, "events": result.stdout, "stderr": result.stderr}
    except TimeoutError:
        return {"exit_code": 124, "events": "", "stderr": f"kilo run timed out after {timeout}s"}


async def _run_local(
    sbox, state: AgentState, model: str, auto: bool, env: dict | None, timeout: int,
    local_project_files: dict[str, str] | None, local_output_targets: dict[str, str] | None = None,
) -> dict:
    if local_project_files is None:
        project_dir = Path((await sbox.exec(["pwd"], cwd=PROJECT_DIR)).stdout.strip())
        return await _run_local_process(project_dir, state, model, auto, env, timeout)

    with staged_project(local_project_files) as project_dir:
        run = await _run_local_process(project_dir, state, model, auto, env, timeout)
        if local_output_targets:
            run["copied_outputs"], run["missing_outputs"] = await copy_outputs(sbox, project_dir, local_output_targets)
        return run


async def _run_local_process(
    project_dir: Path, state: AgentState, model: str, auto: bool, env: dict | None, timeout: int
) -> dict:
    process_env = {**os.environ, **(env or {}), "PWD": str(project_dir)}
    return await run_process(_kilo_command(state, model, auto), project_dir, process_env, timeout, "kilo run")


@contextlib.contextmanager
def staged_project(files: dict[str, str]) -> Iterator[Path]:
    """A temporary host folder, PROJECT_DIR, holding `files`; removed on exit.

    Raises:
        ValueError: a path is absolute or climbs out with "..".
    """
    with tempfile.TemporaryDirectory(prefix="kilo-eval-") as temporary_dir:
        project_dir = Path(temporary_dir) / PROJECT_DIR
        project_dir.mkdir(parents=True)
        for relative, content in files.items():
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"local project file path must be relative: {relative!r}")
            destination = project_dir / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        yield project_dir


async def copy_outputs(sbox, project_dir: Path, targets: dict[str, str]) -> tuple[list[str], list[str]]:
    """Copy each project-relative file in `targets` to its absolute sandbox path.

    Returns:
        (sandbox paths written, source files that weren't there). Every
        mapping is checked, even after a missing one.

    Raises:
        ValueError: a source isn't relative to the project or a target isn't absolute.
    """
    copied, missing = [], []
    for source_relative, sandbox_path in targets.items():
        source, target = Path(source_relative), Path(sandbox_path)
        if source.is_absolute() or ".." in source.parts or not target.is_absolute():
            raise ValueError("local output mapping must use a relative source and absolute sandbox target")
        source_path = project_dir / source
        if not source_path.is_file():
            missing.append(source_relative)
            continue
        await sbox.write_file(sandbox_path, source_path.read_text(encoding="utf-8"))
        copied.append(sandbox_path)
    return copied, missing


async def run_process(
    command: list[str], cwd: Path, env: dict[str, str], timeout: int, name: str, stdin: int | None = None
) -> dict:
    """Run `command` on the host and return {exit_code, events (stdout), stderr}; 124 on timeout.

    Args:
        name: how the timeout message names the command.
        stdin: passed to the subprocess; None inherits the caller's.
    """
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=env,
        stdin=stdin,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,  # its own process group, so a timeout can stop the tools it started too
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        return {
            "exit_code": process.returncode,
            "events": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
        }
    except TimeoutError:
        # Killing only the agent would leave its tool commands holding the
        # output pipes, and communicate() would wait for them, past the timeout.
        with contextlib.suppress(ProcessLookupError):  # the group may exit between the timeout and the kill
            os.killpg(process.pid, signal.SIGKILL)
        await process.communicate()
        return {"exit_code": 124, "events": "", "stderr": f"{name} timed out after {timeout}s"}


def _kilo_command(state: AgentState, model: str, auto: bool) -> list[str]:
    command = ["kilo", "run", "--pure", "--format", "json", "-m", model]
    if auto:
        command.append("--auto")
    command.append(state.messages[-1].text)
    return command


def _append_final_message(state: AgentState, run: dict, model: str) -> None:
    text = None
    for line in run.get("events", "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "text":
            text = (event.get("part") or {}).get("text")
    if text:
        state.messages.append(ChatMessageAssistant(content=text, model=model))
