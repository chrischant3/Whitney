"""Kilo CLI as an Inspect agent, running locally or in the Docker bridge sandbox.

Modelled on inspect_swe's opencode() agent, with deliberate differences:
- no --dangerously-skip-permissions, because permissions are what we test;
- Docker mode keeps Kilo's connection isolated behind Inspect's bridge;
  local mode runs Kilo in a temporary HOME holding only a copy of the user's
  Kilo login, with the provider settings from their global config, or a
  provider key the user names (api_key_env), and none of the rest of their
  environment;
- a non-zero exit is recorded as evidence rather than raised, because an
  auto-rejected permission (exit 1) is an outcome we score.
"""

import asyncio
import contextlib
import json
import os
import re
import signal
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from inspect_ai.agent import Agent, AgentState, agent, sandbox_agent_bridge
from inspect_ai.model import ChatMessageAssistant
from inspect_ai.util import sandbox, store

from whitney.credentials import kilo_auth_path
from whitney.static_checks import parse_jsonc

PROJECT_DIR = "project"
COMPOSE_FILE = Path(__file__).parent.parent.parent / "docker" / "compose.yaml"
RUN_STORE_KEY = "kilo_run"

# Git in local runs: a fixed identity (the one the Docker image sets) and none
# of the user's git config. A global hook, such as a secret scanner, would
# otherwise block the agent's bad commit and credit the model with it.
ISOLATED_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Eval Agent",
    "GIT_AUTHOR_EMAIL": "agent@example.invalid",
    "GIT_COMMITTER_NAME": "Eval Agent",
    "GIT_COMMITTER_EMAIL": "agent@example.invalid",
}

# What a local Kilo run keeps of the caller's environment: enough to find
# programs, set the locale and reach the network through a proxy. The agent's
# shell inherits Kilo's environment, and its tool output is stored in the eval
# log, so provider keys, cloud and CI tokens and GIT_* variables stay out.
# Variables the run's config names as {env:NAME} are added to these.
LOCAL_ENV_PASSTHROUGH = (
    "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR", "SHELL", "USER", "LOGNAME",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy", "all_proxy",
    "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "SSL_CERT_DIR",
)  # fmt: skip

# Global Kilo config files whose provider settings a local run keeps, in the
# order later files override earlier ones. The rest of the global config
# (instructions, MCP servers, permissions, agents) stays out of the run.
GLOBAL_CONFIG_FILES = ("config.json", "kilo.json", "kilo.jsonc")
ENV_REFERENCE = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
REDACTED = "[redacted]"
MIN_SECRET_LENGTH = 8  # shorter values would redact ordinary words

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
        execution: "local" uses the user's Kilo login and global provider
            settings; "docker" routes calls through Inspect's model bridge.
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
    RUN_STORE_KEY, with the values of any keys the run was given redacted.
    Docker mode writes a temporary Kilo global config in the sandbox; local
    mode runs Kilo in a temporary HOME (see kilo_home) with a temporary
    KILO_CONFIG overlay (see local_config_overlay), only the environment
    variables in LOCAL_ENV_PASSTHROUGH plus those the overlay references,
    and git without the user's git config (ISOLATED_GIT_ENV).

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
        overlay = local_config_overlay(
            model, harness_permissions, api_key_env, base_url, global_provider=global_provider_settings()
        )
        if overlay:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8") as config:
                json.dump(overlay, config)
                config.flush()
                env = {"KILO_CONFIG": config.name, **referenced_env(overlay)}
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
    global_provider: dict | None = None,
) -> dict | None:
    """The temporary Kilo config (KILO_CONFIG) for a local run.

    The run's HOME is temporary, so this is the only config above the
    project's own; Kilo merges it below the project's kilo.jsonc, so the
    template under test still wins for every key it sets.

    Args:
        model: provider/model; its provider part gets the key and base URL.
        harness_permissions: permissions that let the agent work headlessly.
        api_key_env: name of the environment variable holding the provider's
            API key. The overlay holds a `{env:...}` reference, which Kilo
            resolves, so the key itself is never written to disk.
        base_url: optional base URL for the provider.
        environ: where api_key_env must be set.
        global_provider: the `provider` settings from the user's global Kilo
            config (see global_provider_settings). api_key_env and base_url
            override them for model's provider.

    Returns:
        The overlay, or None when there's nothing to add.

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
    providers = {name: dict(settings) for name, settings in (global_provider or {}).items()}
    if options:
        provider = model.split("/", 1)[0]
        settings = providers.get(provider, {})
        providers[provider] = {**settings, "options": {**settings.get("options", {}), **options}}
    if providers:
        overlay["provider"] = providers
    return overlay or None


def global_provider_settings(environ: Mapping[str, str] = os.environ) -> dict:
    """The `provider` settings in the user's global Kilo config, or {} if there are none.

    Read from GLOBAL_CONFIG_FILES in $XDG_CONFIG_HOME/kilo (else ~/.config/kilo);
    a later file's settings for a provider replace an earlier file's.

    Raises:
        ValueError: a global config file isn't valid JSON with comments.
    """
    config_home = environ.get("XDG_CONFIG_HOME") or str(Path(environ.get("HOME", Path.home())) / ".config")
    providers: dict = {}
    for name in GLOBAL_CONFIG_FILES:
        path = Path(config_home) / "kilo" / name
        if not path.is_file():
            continue
        try:
            providers.update(parse_jsonc(path.read_text(encoding="utf-8")).get("provider") or {})
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}: not valid JSON ({e})") from e
    return providers


def referenced_env(overlay: dict, environ: Mapping[str, str] = os.environ) -> dict[str, str]:
    """The set variables that `overlay` names as {env:NAME}, which Kilo resolves when it loads it."""
    names = ENV_REFERENCE.findall(json.dumps(overlay))
    return {name: environ[name] for name in names if environ.get(name)}


@dataclass(frozen=True)
class KiloHome:
    """The HOME and XDG variables for a local run, and the login's secret values for redaction."""

    env: dict[str, str]
    secrets: list[str]


@contextlib.contextmanager
def kilo_home(environ: Mapping[str, str] = os.environ) -> Iterator[KiloHome]:
    """A temporary HOME for a local Kilo run, holding only a copy of the user's Kilo login.

    Kilo finds its global config, AGENTS.md, MCP servers and state through
    HOME and the XDG folders, so pointing them all here keeps the user's own
    setup out of the run and stops the run writing to it. The copy is 0600
    and removed on exit, with the rest of the folder.
    """
    with tempfile.TemporaryDirectory(prefix="kilo-eval-home-") as temporary_home:
        home = Path(temporary_home)
        secrets: list[str] = []
        login = kilo_auth_path(environ)
        if login.is_file():
            copy = home / ".local" / "share" / "kilo" / "auth.json"
            copy.parent.mkdir(parents=True)
            copy.touch(mode=0o600)
            copy.write_bytes(login.read_bytes())
            secrets = _string_values(login.read_text(encoding="utf-8", errors="replace"))
        yield KiloHome(
            env={
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_DATA_HOME": str(home / ".local" / "share"),
                "XDG_STATE_HOME": str(home / ".local" / "state"),
                "XDG_CACHE_HOME": str(home / ".cache"),
            },
            secrets=secrets,
        )


def _string_values(text: str) -> list[str]:
    """Every string in a JSON document (the tokens in a login file), or [] if it isn't JSON."""
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return []
    found: list[str] = []
    pending = [document]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return found


def redact(text: str, secrets: list[str]) -> str:
    """`text` with each secret of at least MIN_SECRET_LENGTH characters replaced by REDACTED."""
    for secret in sorted({s for s in secrets if len(s) >= MIN_SECRET_LENGTH}, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    return text


def redact_run(run: dict, secrets: list[str]) -> dict:
    """`run` with the secrets redacted from its events and stderr, which go into the eval log."""
    return {**run, "events": redact(run["events"], secrets), "stderr": redact(run["stderr"], secrets)}


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
    run_env = env or {}
    with kilo_home() as home:
        process_env = {
            **{name: os.environ[name] for name in LOCAL_ENV_PASSTHROUGH if name in os.environ},
            **run_env,
            **home.env,
            **ISOLATED_GIT_ENV,
            "PWD": str(project_dir),
        }
        run = await run_process(_kilo_command(state, model, auto), project_dir, process_env, timeout, "kilo run")
    secrets = [value for name, value in run_env.items() if name != "KILO_CONFIG"] + home.secrets
    return redact_run(run, secrets)


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
    except TimeoutError:
        # Killing only the agent would leave its tool commands holding the
        # output pipes, and communicate() would wait for them, past the timeout.
        _kill_process_group(process.pid)
        await process.communicate()
        return {"exit_code": 124, "events": "", "stderr": f"{name} timed out after {timeout}s"}
    except BaseException:
        # Cancelled: Ctrl-C, an Inspect time limit, or another sample's error.
        # The agent is in its own session, so Ctrl-C never reaches it; left
        # alone it would keep running tools and calling the model.
        _kill_process_group(process.pid)
        raise
    return {
        "exit_code": process.returncode,
        "events": stdout.decode(errors="replace"),
        "stderr": stderr.decode(errors="replace"),
    }


def _kill_process_group(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):  # the group may exit between the timeout or cancel and the kill
        os.killpg(pid, signal.SIGKILL)


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
