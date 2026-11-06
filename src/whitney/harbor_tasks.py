"""Local Harbor BigCodeBench-Hard task with the Kilo template and verifier."""

import re
from copy import deepcopy
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset

from whitney import targets
from whitney.harbor_cache import check_cache
from whitney.project_files import files_not_staged, files_renamed, load_project_files_for, load_template_files

DEFAULT_HARBOR_DATASET = Path(__file__).resolve().parents[2] / "dataset/harbor/bigcodebench-hard-complete"
HARBOR_OUTPUT_PATH = "/workspace/solution.py"
HARBOR_OUTPUT_ADAPTATION = (
    "Harness note: save the completed solution to `solution.py` in the current working directory. "
    "The harness will copy that file to `/workspace/solution.py` in the Harbor task sandbox before verification. "
    "Do not run shell commands or execute the generated code."
)
HARBOR_HARNESS_PERMISSIONS = {
    "bash": "deny",
    "read": "allow",
    "edit": "allow",
    "external_directory": "deny",
}
# The same in Claude Code's terms; it already keeps edits inside the project.
HARBOR_PERMISSIONS = {
    "kilo": HARBOR_HARNESS_PERMISSIONS,
    "claude-code": {"allow": ["Read", "Edit", "Write"], "deny": ["Bash"]},
}
# The variables inspect-harbor fills in a task's docker-compose.yaml itself
# (_expand_compose_vars), besides the task's own [environment].env. Any other
# ${VAR} it fills from the host's environment, and Docker Compose fills every
# $VAR or ${VAR} left in the config the same way.
HARBOR_COMPOSE_VARIABLES = frozenset({
    "CONTEXT_DIR", "MAIN_IMAGE_NAME", "CPUS", "MEMORY", "TEST_DIR",
    "HOST_VERIFIER_LOGS_PATH", "HOST_AGENT_LOGS_PATH", "HOST_ARTIFACTS_PATH",
    "ENV_VERIFIER_LOGS_PATH", "ENV_AGENT_LOGS_PATH", "ENV_ARTIFACTS_PATH",
})  # fmt: skip
VARIABLE = re.compile(r"(?<!\$)\$(?:\{([A-Za-z_]\w*)|([A-Za-z_]\w*))")  # ${NAME...} or $NAME; $$ is a literal $
HARBOR_ENV_TEMPLATE = re.compile(r"\$\{([^}:]+)(?::-.*)?\}")  # inspect-harbor's resolve_env_vars, on a whole value
SAFE_SECURITY_OPTS = {"no-new-privileges", "no-new-privileges:true", "no-new-privileges=true"}
HOST_REACHING_VOLUME_KEYS = ("driver", "driver_opts", "external")  # a named volume with these can be a host folder


@task
def bigcodebench_hard_complete(
    variant: str = "template",
    *,
    target: str,
    model: str,
    dataset_path: str | None = None,
    project_path: str | None = None,
    api_key_env: str | None = None,
    base_url: str | None = None,
) -> Task:
    """Run a local Harbor package copy, syncing Kilo's solution into its sandbox.

    Hub resolution is not used. Kilo runs on the host against a temporary project
    with shell execution denied; only ``solution.py`` is copied into Harbor's
    Docker sandbox. The Harbor verifier runs there with runtime networking off.
    The cache is checked against its manifest's digests first (see
    whitney.harbor_cache), and a task whose sandbox asks for privileged
    mode, added capabilities, devices or a bind mount is refused.
    target picks the harness and template, project_path a project to read
    instead with that target's loader, and api_key_env and base_url the
    provider key, as in whitney.tasks.agents_md.
    """
    if variant not in {"baseline", "template"}:
        raise ValueError("variant must be 'baseline' or 'template'")
    targets.check_target(target, base_url=base_url)
    local_dataset = Path(dataset_path).expanduser() if dataset_path else DEFAULT_HARBOR_DATASET
    if not local_dataset.is_dir():
        raise FileNotFoundError(f"Local Harbor dataset directory not found: {local_dataset}")
    cache_integrity = check_cache(local_dataset)

    try:
        from inspect_harbor._harbor.converters import harbor_task_to_sample
        from inspect_harbor._harbor.scorer import harbor_scorer
        from inspect_harbor._harbor.task import _disambiguate_sample_ids, load_harbor_tasks
    except ImportError as error:
        raise RuntimeError('Install the optional extra with `pip install -e ".[harbor]"`.') from error

    harbor_tasks = load_harbor_tasks(path=local_dataset)
    if not harbor_tasks:
        raise ValueError(f"No Harbor tasks found in {local_dataset}")

    project_files = (
        load_project_files_for(target, project_path) if project_path else load_template_files(targets.template_dir(target))
    )
    if variant == "baseline":
        project_files.pop("AGENTS.md", None)

    samples = []
    sample_ids = _disambiguate_sample_ids(harbor_tasks)
    for harbor_task, sample_id in zip(harbor_tasks, sample_ids, strict=True):
        _refuse_host_variables(harbor_task.name, _host_variables(harbor_task))
        sample = harbor_task_to_sample(harbor_task, sample_id=sample_id)
        if HARBOR_OUTPUT_PATH not in sample.input:
            raise ValueError(
                f"Harbor task {harbor_task.name!r} does not use the expected output path "
                f"{HARBOR_OUTPUT_PATH}; cannot sync its solution safely."
            )
        prompt = sample.input.replace(HARBOR_OUTPUT_PATH, "`solution.py` in the current working directory")
        prompt += "\n\n" + HARBOR_OUTPUT_ADAPTATION

        task_sandbox = deepcopy(sample.sandbox)
        _refuse_host_variables(harbor_task.name, _variables_in(task_sandbox.config.model_dump()))
        task_dir = harbor_task.paths.task_dir.resolve()
        host_access = _host_volumes(task_sandbox.config.volumes or {})
        for service in task_sandbox.config.services.values():
            host_access += _host_access(service, task_dir)
        if host_access:
            raise ValueError(
                f"Harbor task {harbor_task.name!r} asks for {', '.join(host_access)}, "
                "which would let its container reach the host; refusing to run it."
            )
        for service in task_sandbox.config.services.values():
            service.network_mode = "none"
            service.networks = None

        samples.append(
            sample.model_copy(
                update={
                    "input": prompt,
                    "sandbox": task_sandbox,
                    "metadata": {
                        **(sample.metadata or {}),
                        "network_mode_override": "none",
                    },
                }
            )
        )

    return Task(
        name=f"bigcodebench_hard_complete_{variant}",
        dataset=MemoryDataset(samples, name=f"bigcodebench-harbor-{variant}"),
        solver=targets.build_agent(
            target,
            model,
            HARBOR_PERMISSIONS,
            local_project_files=project_files,
            local_output_targets={"solution.py": HARBOR_OUTPUT_PATH},
            api_key_env=api_key_env,
            base_url=base_url,
        ),
        scorer=harbor_scorer(),
        metadata={
            "variant": variant,
            "target": target,
            "model": model,
            "model_access": targets.model_access(target, api_key_env, base_url),
            "execution": "local",
            "asset_source": "local project" if project_path else "template",
            "evaluation_assets": project_files,
            "renamed": files_renamed(target, project_path, project_files) if project_path else {},
            "not_staged": files_not_staged(target, project_path) if project_path else [],
            "harness_permissions": HARBOR_PERMISSIONS[target],
            "evaluation_preamble": HARBOR_OUTPUT_ADAPTATION,
            "dataset_source": str(local_dataset),
            "harbor_task_count": len(samples),
            "solution_output_path": HARBOR_OUTPUT_PATH,
            "runtime_network": "none",
            "cache_integrity": cache_integrity,
        },
    )


def _host_access(service, task_dir: Path) -> list[str]:
    """The compose settings in a task's service that would let its container reach the host.

    `task_dir` is the task's own folder, the only place a build context may be.
    """
    found = [name for name in ("privileged", "cap_add", "devices") if getattr(service, name)]
    for volume in service.volumes or []:
        if isinstance(volume, str):
            source, colon, _ = volume.partition(":")
            is_bind = bool(colon) and source.startswith(("/", ".", "~"))  # no ':' is an anonymous volume
        else:
            source, is_bind = volume.source, volume.type == "bind"
        if is_bind:
            found.append(f"bind mount {source}")
    env_files = service.env_file or []
    found += [f"env_file {path}" for path in ([env_files] if isinstance(env_files, str) else env_files)]
    found += [f"security_opt {option}" for option in service.security_opt or [] if option not in SAFE_SECURITY_OPTS]
    if service.cgroup == "host":
        found.append("cgroup host")
    if service.build is not None:
        context = service.build if isinstance(service.build, str) else service.build.context
        found += _outside_build_context(context, task_dir)
    return found


def _outside_build_context(context: str, task_dir: Path) -> list[str]:
    """A build context that would send the image build files from outside the task folder."""
    if not Path(context).is_absolute():
        # Inspect resolves it against the working folder, where the eval runs (its .env included).
        return [f"build context {context}, which resolves against the folder the eval runs in"]
    if not Path(context).resolve().is_relative_to(task_dir):
        return [f"build context {context}, outside the task folder"]
    return []


def _host_volumes(volumes: dict) -> list[str]:
    """Top-level named volumes whose options could make them a host folder or another project's volume."""
    found = []
    for name, options in volumes.items():
        found += [f"volume {name} with {key}" for key in HOST_REACHING_VOLUME_KEYS if (options or {}).get(key)]
    return found


def _host_variables(harbor_task) -> set[str]:
    """Host environment variables a task would read, before inspect-harbor fills them in.

    Every variable in task.toml's [environment].env, which reaches the
    container's config; every [verifier].env value that is a whole ${VAR}
    template, which the verifier fills from the host when it scores (others it
    passes on as text); and every variable in the task's docker-compose.yaml
    except those inspect-harbor fills itself. [solution].env is read only by
    inspect-harbor's oracle, which Whitney doesn't run.
    """
    config = harbor_task.config
    names = _variables_in(list(config.environment.env.values()))
    names |= {match.group(1) for value in config.verifier.env.values() if (match := HARBOR_ENV_TEMPLATE.fullmatch(value))}
    compose = harbor_task.paths.environment_dir / "docker-compose.yaml"
    if compose.is_file():
        own = HARBOR_COMPOSE_VARIABLES | config.environment.env.keys()
        for braced, bare in VARIABLE.findall(compose.read_text(encoding="utf-8")):
            if bare or braced not in own:  # inspect-harbor fills only ${NAME}; Compose fills $NAME
                names.add(braced or bare)
    return names


def _variables_in(node: object) -> set[str]:
    """Names of the $VAR and ${VAR} references in every string of a parsed config, at any depth."""
    if isinstance(node, str):
        return {braced or bare for braced, bare in VARIABLE.findall(node)}
    if isinstance(node, dict):
        node = list(node.values())
    if isinstance(node, list):
        return {name for item in node for name in _variables_in(item)}
    return set()


def _refuse_host_variables(task_name: str, names: set[str]) -> None:
    if names:
        raise ValueError(
            f"Harbor task {task_name!r} reads {', '.join(sorted(names))} from the host's environment, which would "
            "copy host values such as API keys into its container; refusing to run it."
        )
