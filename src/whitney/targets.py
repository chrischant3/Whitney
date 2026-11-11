"""The harnesses the evals can target: each has its own template, agent and model route.

| Target        | Harness         | Template                 | Model route                         |
|---------------|-----------------|--------------------------|-------------------------------------|
| `kilo`        | Kilo CLI        | `template/kilo/`         | Kilo Gateway or a named provider key |
| `claude-code` | Claude Code CLI | `template/claude-code/`  | Anthropic API (`ANTHROPIC_API_KEY`) |

Either target can run in Docker, where Inspect's bridge answers for the model.
"""

from pathlib import Path

from inspect_ai.agent import Agent

from whitney.agent import kilo
from whitney.claude_code import claude_code
from whitney.target_names import TARGETS
TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "template"


def check_target(target: str, execution: str = "local", base_url: str | None = None) -> None:
    """Reject a target, or an option the target can't use.

    Raises:
        ValueError: unknown target, or claude-code with a base URL.
    """
    if target not in TARGETS:
        raise ValueError(f"target must be one of {', '.join(TARGETS)}; got {target!r}")
    if target == "claude-code" and base_url:
        raise ValueError("base_url applies to the kilo target only")


def template_dir(target: str) -> Path:
    """The shipped template folder for `target`."""
    check_target(target)
    return TEMPLATE_ROOT / target


def build_agent(
    target: str,
    model: str,
    harness_permissions: dict[str, dict],
    *,
    execution: str = "local",
    local_project_files: dict[str, str] | None = None,
    local_output_targets: dict[str, str] | None = None,
    api_key_env: str | None = None,
    base_url: str | None = None,
    kilo_agent: str | None = None,
    kilo_command: str | None = None,
) -> Agent:
    """The agent that runs `target`'s harness on `model`.

    Args:
        harness_permissions: the eval's headless permissions keyed by target,
            each in its own harness's format; only `target`'s is used.
        api_key_env: kilo: optional provider key variable (else the global
            Kilo config); claude-code: the Anthropic key variable, default
            ANTHROPIC_API_KEY.
        kilo_agent, kilo_command: kilo only: run with `kilo run --agent NAME`
            or `--command NAME` (the kilo agent's agent_name and command_name).
        Other arguments are passed through to the kilo or claude_code agent.

    Raises:
        ValueError: as check_target, or kilo_agent or kilo_command given for another target.
    """
    check_target(target, execution, base_url)
    if target != "kilo" and (kilo_agent or kilo_command):
        raise ValueError(f"only the kilo target runs a named agent or command; got them for {target}")
    if target == "claude-code":
        return claude_code(
            model=model,
            harness_permissions=harness_permissions[target],
            local_project_files=local_project_files,
            local_output_targets=local_output_targets,
            # Docker mode has no key variable of its own: Inspect answers for the model. `execution` is
            # given only for Docker, so a local run builds the agent as it always did.
            **({"api_key_env": api_key_env, "execution": execution} if execution == "docker"
               else {"api_key_env": api_key_env or "ANTHROPIC_API_KEY"}),
        )
    return kilo(
        model=model,
        execution=execution,
        harness_permissions=harness_permissions[target],
        local_project_files=local_project_files,
        local_output_targets=local_output_targets,
        api_key_env=api_key_env,
        base_url=base_url,
        # Given only when set, so a run without a name builds the agent as it always did.
        **{key: value for key, value in (("agent_name", kilo_agent), ("command_name", kilo_command)) if value},
    )


def model_access(target: str, api_key_env: str | None, base_url: str | None) -> dict:
    """How the harness reached the model, for the log: env var name and base URL, never the key.

    Raises:
        ValueError: unknown target.
    """
    check_target(target)
    if target == "claude-code":
        return {"api_key_env": api_key_env or "ANTHROPIC_API_KEY", "base_url": None}
    if api_key_env or base_url:
        return {"api_key_env": api_key_env, "base_url": base_url}
    return {"source": "global Kilo config"}
