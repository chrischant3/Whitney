"""The harnesses the evals can target: each has its own template, agent and model route.

| Target        | Harness         | Template                 | Model route                         |
|---------------|-----------------|--------------------------|-------------------------------------|
| `kilo`        | Kilo CLI        | `template/kilo/`         | Kilo Gateway or a named provider key |
| `claude-code` | Claude Code CLI | `template/claude-code/`  | Anthropic API (`ANTHROPIC_API_KEY`) |
"""

from pathlib import Path

from inspect_ai.agent import Agent

from whitney.agent import kilo
from whitney.claude_code import claude_code

TARGETS = ("kilo", "claude-code")
TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "template"


def check_target(target: str, execution: str = "local", base_url: str | None = None) -> None:
    """Reject a target, or an option the target can't use.

    Raises:
        ValueError: unknown target, or claude-code with Docker execution or a base URL.
    """
    if target not in TARGETS:
        raise ValueError(f"target must be one of {', '.join(TARGETS)}; got {target!r}")
    if target == "claude-code" and execution != "local":
        raise ValueError("the claude-code target runs locally only")
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
) -> Agent:
    """The agent that runs `target`'s harness on `model`.

    Args:
        harness_permissions: the eval's headless permissions keyed by target,
            each in its own harness's format; only `target`'s is used.
        api_key_env: kilo: optional provider key variable (else the global
            Kilo config); claude-code: the Anthropic key variable, default
            ANTHROPIC_API_KEY.
        Other arguments are passed through to the kilo or claude_code agent.
    """
    check_target(target, execution, base_url)
    if target == "claude-code":
        return claude_code(
            model=model,
            harness_permissions=harness_permissions[target],
            local_project_files=local_project_files,
            local_output_targets=local_output_targets,
            api_key_env=api_key_env or "ANTHROPIC_API_KEY",
        )
    return kilo(
        model=model,
        execution=execution,
        harness_permissions=harness_permissions[target],
        local_project_files=local_project_files,
        local_output_targets=local_output_targets,
        api_key_env=api_key_env,
        base_url=base_url,
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
