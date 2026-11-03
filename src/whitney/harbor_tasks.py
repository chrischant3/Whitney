"""Local Harbor BigCodeBench-Hard task with the Kilo template and verifier."""

from copy import deepcopy
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset

from whitney import targets
from whitney.harbor_cache import check_cache
from whitney.project_files import load_project_files, load_template_files

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
    target picks the harness and template, and api_key_env and base_url the
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

    project_files = load_project_files(project_path) if project_path else load_template_files(targets.template_dir(target))
    if variant == "baseline":
        project_files.pop("AGENTS.md", None)

    samples = []
    sample_ids = _disambiguate_sample_ids(harbor_tasks)
    for harbor_task, sample_id in zip(harbor_tasks, sample_ids, strict=True):
        sample = harbor_task_to_sample(harbor_task, sample_id=sample_id)
        if HARBOR_OUTPUT_PATH not in sample.input:
            raise ValueError(
                f"Harbor task {harbor_task.name!r} does not use the expected output path "
                f"{HARBOR_OUTPUT_PATH}; cannot sync its solution safely."
            )
        prompt = sample.input.replace(HARBOR_OUTPUT_PATH, "`solution.py` in the current working directory")
        prompt += "\n\n" + HARBOR_OUTPUT_ADAPTATION

        task_sandbox = deepcopy(sample.sandbox)
        for service in task_sandbox.config.services.values():
            host_access = _host_access(service)
            if host_access:
                raise ValueError(
                    f"Harbor task {harbor_task.name!r} asks for {', '.join(host_access)}, "
                    "which would let its container reach the host; refusing to run it."
                )
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
            "harness_permissions": HARBOR_PERMISSIONS[target],
            "evaluation_preamble": HARBOR_OUTPUT_ADAPTATION,
            "dataset_source": str(local_dataset),
            "harbor_task_count": len(samples),
            "solution_output_path": HARBOR_OUTPUT_PATH,
            "runtime_network": "none",
            "cache_integrity": cache_integrity,
        },
    )


def _host_access(service) -> list[str]:
    """The compose settings in a task's service that would let its container reach the host."""
    found = [name for name in ("privileged", "cap_add", "devices") if getattr(service, name, None)]
    for volume in getattr(service, "volumes", None) or []:
        if isinstance(volume, str):
            source = volume.split(":", 1)[0] if ":" in volume else ""  # no ':' is an anonymous volume
            is_bind = source.startswith(("/", ".", "~"))
        else:
            source, is_bind = volume.source or "", volume.type == "bind"
        if is_bind:
            found.append(f"bind mount {source}")
    return found
