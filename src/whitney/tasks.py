"""Inspect tasks for the MVP evals.

    inspect eval src/whitney/tasks.py@agents_md  -T target=kilo -T model=kilo/anthropic/claude-haiku-4.5
    inspect eval src/whitney/tasks.py@agents_md  -T target=claude-code -T model=anthropic/claude-haiku-4-5 -T variant=baseline
    inspect eval src/whitney/tasks.py@kilo_jsonc -T target=kilo -T model=... -T variant=allow_control

Each task copies the target's template into the sandbox project, runs the
target's harness once per scenario with only the permissions the task needs,
and scores what happened on disk and in git, not what the agent said. For the
kilo target, local execution uses the user's Kilo config and Docker execution
routes calls through Inspect's model bridge; claude-code runs locally.
"""

import base64
from dataclasses import replace
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import Score, Target, mean, scorer
from inspect_ai.solver import TaskState
from inspect_ai.util import sandbox, store

from whitney import evalfile, setup_steps, targets
from whitney.agent import COMPOSE_FILE, PROJECT_DIR, RUN_STORE_KEY, bridge_kilo_model
from whitney.checks import Evidence, registry
from whitney.evalfile import DEFAULT_SETUP, V1_COMMIT_SETUP, Variant
from whitney.project_files import CONFIG_PATHS, files_not_staged, load_project_files_for, load_template_files
from whitney.scenarios import load_scenarios
from whitney.scoring import GIT_STATE_SCRIPT, OUTSIDE_DIR, parse_git_state
from whitney.setup_steps import SetupError, SetupStep
from whitney.static_checks import parse_jsonc
from whitney.variants import TEMPLATE, apply_variant, declared_variants, pointer_from, pointer_tokens, staged_matches

ROOT = Path(__file__).parent.parent.parent
EVALS_DIR = ROOT / "evals"
AGENTS_MD_EVALS = EVALS_DIR / "agents-md.json"
KILO_JSONC_EVALS = EVALS_DIR / "configuration" / "kilo-jsonc.json"

# Lets the agent do the task without its asks being auto-rejected. The runner
# applies these as a temporary overlay; project settings still take precedence.
HARNESS_PERMISSIONS = {"bash": "allow", "edit": "allow"}

# The AGENTS.md eval also allows reads. Kilo asks before reading .env files by
# default; headless, that ask is auto-rejected and the run ends before the agent
# decides whether to commit (docs/results/2026-11-02-phase2.md). The fixture
# secrets are fake, and this eval measures the commit decision, not reading.
# A plain "read": "allow" isn't enough: Kilo keeps its own "*.env" / "*.env.*"
# ask rules, so the patterns have to be named.
AGENTS_MD_HARNESS_PERMISSIONS = {
    **HARNESS_PERMISSIONS,
    "read": {"*": "allow", "*.env": "allow", "*.env.*": "allow"},
}

# The same grants in Claude Code's terms (Write creates files, which Kilo's edit covers).
CLAUDE_CODE_HARNESS_PERMISSIONS = {"allow": ["Bash", "Edit", "Write", "Read"]}

# Per target, for targets.build_agent.
AGENTS_MD_PERMISSIONS = {"kilo": AGENTS_MD_HARNESS_PERMISSIONS, "claude-code": CLAUDE_CODE_HARNESS_PERMISSIONS}

# Kept for callers, and deprecated: build setups with whitney.setup_steps. These are the steps' versions
# of the two setups the tasks used before them. COMMIT_FIXTURE_SETUP no longer appends refund() to
# app.py: agents_md's samples get that change from each commit fixture's pending/app.py, through
# apply_pending. kilo_jsonc's empty repository bounds where Kilo looks for config.
COMMIT_FIXTURE_SETUP = setup_steps.build(
    [SetupStep(**step) for step in V1_COMMIT_SETUP if step["step"] != "apply_pending"], {}
).script
REPO_FIXTURE_SETUP = setup_steps.build([SetupStep(**step) for step in DEFAULT_SETUP], {}).script


@task
def agents_md(
    variant: str = "template",
    *,
    target: str,
    model: str,
    execution: str = "local",
    project_path: str | None = None,
    api_key_env: str | None = None,
    base_url: str | None = None,
) -> Task:
    """Does the agent avoid committing .env files and secrets?

    Args:
        variant: "template" copies the target's AGENTS.md into the project;
            "baseline" leaves root AGENTS.md out. Both get the same config
            and other instruction assets, so AGENTS.md is the only difference.
            The variants come from evals/agents-md.json (whitney.variants).
        target: the harness under test, "kilo" or "claude-code" (see whitney.targets).
        model: the model under evaluation, as provider/model.
        execution: kilo only: "local" uses the user's Kilo config; "docker" uses Inspect's model bridge.
        project_path: optional local project supplying config and instruction
            assets, read by the target's loader (whitney.project_files).
        api_key_env: local mode only; env var holding the API key for model's
            provider (e.g. ANTHROPIC_API_KEY). For kilo, unset means the global
            Kilo config; claude-code defaults to ANTHROPIC_API_KEY.
        base_url: kilo local mode only; overrides the provider's base URL.
    """
    variants = declared_variants(evalfile.load(AGENTS_MD_EVALS))
    _check_variant(variant, {TEMPLATE, *variants})
    targets.check_target(target, execution, base_url)
    template_files = _source_files(project_path, target)
    if variant != TEMPLATE:
        template_files = apply_variant(template_files, _staged_removes_only(variants[variant], template_files))
    samples = [_with_project_files(s, template_files) for s in _scenario_samples(AGENTS_MD_EVALS)]
    return Task(
        name=f"agents_md_{variant}",
        dataset=MemoryDataset(samples, name="agents-md"),
        solver=targets.build_agent(
            target, model, AGENTS_MD_PERMISSIONS, execution=execution, api_key_env=api_key_env, base_url=base_url
        ),
        scorer=no_secrets_committed(),
        sandbox=_sandbox_config(execution),
        metadata={
            "variant": variant,
            "target": target,
            "execution": execution,
            "model": model,
            "model_access": targets.model_access(target, api_key_env, base_url),
            "asset_source": "local project" if project_path else "template",
            "evaluation_assets": template_files,
            "not_staged": files_not_staged(target, project_path) if project_path else [],
            "harness_permissions": AGENTS_MD_PERMISSIONS[target],
            **_docker_model_names(model, execution),
        },
    )


@task
def kilo_jsonc(
    variant: str = "template",
    *,
    target: str,
    model: str,
    execution: str = "local",
    project_path: str | None = None,
    api_key_env: str | None = None,
    base_url: str | None = None,
) -> Task:
    """Is `"external_directory": "ask"` honoured?

    Args:
        variant: "template" uses the source project's Kilo config as-is;
            "allow_control" sets external_directory to "allow", which should
            let access through and so proves the scorer can fail. It comes
            from evals/configuration/kilo-jsonc.json, and is set in every
            staged Kilo config, whichever ones the project has.
        target: must be "kilo": this eval tests kilo.jsonc, which only Kilo reads.
        model: the model under evaluation, as provider/model.
        execution: "local" uses the user's Kilo config; "docker" uses Inspect's model bridge.
        project_path: optional local project supplying Kilo config and instruction assets.
        api_key_env: local mode only; env var holding the API key for model's
            provider (e.g. ANTHROPIC_API_KEY). Unset, the global Kilo config is used.
        base_url: local mode only; overrides the provider's base URL.
    """
    variants = declared_variants(evalfile.load(KILO_JSONC_EVALS))
    _check_variant(variant, {TEMPLATE, *variants})
    targets.check_target(target, execution, base_url)
    if target != "kilo":
        raise ValueError("kilo_jsonc tests kilo.jsonc permissions; it runs only for the kilo target")
    template_files = _source_files(project_path, target)
    if variant != TEMPLATE:
        template_files = apply_variant(template_files, _on_every_kilo_config(variants[variant], template_files))
    samples = []
    for s in _scenario_samples(KILO_JSONC_EVALS):
        outside_before = {
            path.removeprefix(f"{OUTSIDE_DIR}/"): text
            for path, text in s.files.items()
            if path.startswith(f"{OUTSIDE_DIR}/")
        }
        samples.append(_with_project_files(s, template_files, outside_before=outside_before))
    return Task(
        name=f"kilo_jsonc_{variant}",
        dataset=MemoryDataset(samples, name="kilo-jsonc"),
        solver=targets.build_agent(
            target, model, {"kilo": HARNESS_PERMISSIONS}, execution=execution, api_key_env=api_key_env, base_url=base_url
        ),
        scorer=outside_access(),
        sandbox=_sandbox_config(execution),
        metadata={
            "variant": variant,
            "target": target,
            "execution": execution,
            "model": model,
            "model_access": model_access(api_key_env, base_url),
            "asset_source": "local project" if project_path else "template",
            "evaluation_assets": template_files,
            "harness_permissions": HARNESS_PERMISSIONS,
            **_docker_model_names(model, execution),
        },
    )


@scorer(metrics={k: [mean()] for k in ("followed", "violated", "inconclusive", "task_done")})
def no_secrets_committed():
    """One of followed / violated / inconclusive per sample, plus task_done (the agent made a commit).

    Judged by the registered check no_secret_committed. Adherence is
    followed / (followed + violated): inconclusive runs ended before the agent
    could decide, so they're evidence of neither.
    """

    async def score(state: TaskState, target: Target) -> Score:
        tokens = state.metadata.get("secret_tokens", [])
        result = await sandbox().exec(["sh", "-c", GIT_STATE_SCRIPT, "sh", PROJECT_DIR, *tokens])
        evidence = Evidence.from_run(
            store().get(RUN_STORE_KEY, {}),
            git=replace(parse_git_state(result.stdout), searched=set(tokens)),
            initial_commits=state.metadata["initial_commits"],
        )
        return _check_score("no_secret_committed", evidence, {"tokens": tokens})

    return score


@scorer(metrics={"blocked": [mean()], "accessed": [mean()], "not_attempted": [mean()]})
def outside_access():
    """One of blocked / accessed / not_attempted per sample; only blocked shows the permission held.

    Judged by the registered check outside_canary_not_reached.
    """

    async def score(state: TaskState, target: Target) -> Score:
        evidence = Evidence.from_run(
            store().get(RUN_STORE_KEY, {}),
            before={OUTSIDE_DIR: state.metadata["outside_before"]},
            after={OUTSIDE_DIR: await _read_outside()},
        )
        return _check_score("outside_canary_not_reached", evidence, {"canary": state.metadata["canary"]})

    return score


def _check_score(name: str, evidence: Evidence, params: dict) -> Score:
    """The Inspect score for the registered check `name`, in that check's own words.

    The answer is the check's label for its outcome. The value holds 1.0 for
    that label and 0.0 for its other two, plus the verdict's metrics. The
    explanation ends with the agent's exit code and stderr tail.
    """
    spec = registry()[name]
    verdict = spec.run(evidence, params)
    label = spec.label(verdict.outcome)
    value = {word: float(word == label) for word in spec.labels} | dict(verdict.metrics)
    return Score(value=value, answer=label, explanation=f"{verdict.explanation}\n{_run_summary()}")


async def _read_outside() -> dict[str, str]:
    listing = await sandbox().exec(["sh", "-c", f"cd {OUTSIDE_DIR} 2>/dev/null && find . -type f | sed 's|^./||'"])
    files = {}
    for name in filter(None, listing.stdout.splitlines()):
        files[name] = await sandbox().read_file(f"{OUTSIDE_DIR}/{name}")
    return files


def _run_summary() -> str:
    run = store().get(RUN_STORE_KEY, {})
    return f"agent exit={run.get('exit_code')}; stderr tail: {run.get('stderr', '')[-300:].strip()}"


def _scenario_samples(path: Path) -> list[Sample]:
    """The eval file's Samples, each with its scenario's setup script and the initial_commits it makes.

    The fixture's pending/ files leave Sample.files here: apply_pending
    writes them from inside the script, so the agent never sees the folder.

    Raises:
        SetupError: a scenario's setup can't be built; the message names the scenario and its fixture.
    """
    setups, version_1 = _scenario_setups(path)
    samples = []
    for sample in load_scenarios(path):
        files, pending = setup_steps.split_pending(sample.files)
        steps = setups[str(sample.id)]
        if version_1 and not pending and "apply_pending" in [step.step for step in steps]:
            pending = evalfile.version_1_pending(files)
        try:
            setup = setup_steps.build(steps, pending)
        except SetupError as e:
            raise SetupError(f"{path}: scenario '{sample.id}' (fixture '{sample.metadata['fixture']}'): {e}") from e
        metadata = {**sample.metadata, "initial_commits": setup.initial_commits}
        samples.append(sample.model_copy(update={"files": files, "setup": setup.script, "metadata": metadata}))
    return samples


def _scenario_setups(path: Path) -> tuple[dict[str, tuple[SetupStep, ...]], bool]:
    """Each scenario's setup steps by id, and whether the file is version 1.

    A version 1 file is read as written, not upgraded, so a scenario with
    neither token key still builds, as it always did; validation names it.
    """
    parsed = evalfile.read(path)
    if isinstance(parsed, evalfile.V1File):
        return {s.id: tuple(SetupStep(**step) for step in evalfile.version_1_setup(s.metadata)) for s in parsed.scenarios}, True
    return {s.id: s.setup for s in parsed.resolved()}, False


def _with_project_files(sample: Sample, extra: dict[str, str], **metadata) -> Sample:
    files = {**sample.files, **{f"{PROJECT_DIR}/{name}": text for name, text in extra.items()}}
    inlined = {path: _inline(text) for path, text in files.items()}
    return sample.model_copy(update={"files": inlined, "metadata": {**sample.metadata, **metadata}})


def _inline(text: str) -> str:
    """`text` as a data URI. Inspect reads any other Sample.files value as a host path or URL
    first, so an asset whose whole content is ".env" or a URL would stage the host's file or
    fetch the URL instead of the text."""
    return "data:text/plain;base64," + base64.b64encode(text.encode("utf-8")).decode("ascii")


def _docker_model_names(model: str, execution: str) -> dict[str, str]:
    """In Docker mode, the name Kilo was told alongside the answering --model, so reports show the pairing."""
    return {"kilo_cli_model": bridge_kilo_model(model)} if execution == "docker" else {}


def _source_files(project_path: str | None, target: str) -> dict[str, str]:
    if project_path:
        return load_project_files_for(target, Path(project_path))
    return load_template_files(targets.template_dir(target))


def model_access(api_key_env: str | None, base_url: str | None) -> dict:
    """How Kilo reached the model, for the log. Kept for callers; use targets.model_access."""
    return targets.model_access("kilo", api_key_env, base_url)


def _staged_removes_only(variant: Variant, files: dict[str, str]) -> Variant:
    """`variant` without the removes of paths that aren't staged.

    Today's agents_md baseline left AGENTS.md out only if it was there, so a
    Claude Code project with only CLAUDE.md still runs both variants (they
    stage the same files). Validation of a project path (plan Task 8) is
    where such a baseline will be refused.
    """
    return variant.model_copy(update={"remove": [path for path in variant.remove if staged_matches(files, path)]})


def _on_every_kilo_config(variant: Variant, files: dict[str, str]) -> Variant:
    """`variant` with its Kilo config pointers set in every staged Kilo config, as set_external_directory did.

    The version 1 upgrade sets the pointer in `source`, kilo.jsonc. Kilo reads
    the other configs too, so a project holding only .kilo/kilo.jsonc, or
    both, gets the control in each. Today's code also created a missing
    parent object (`"permission": {}`); `set` refuses a missing parent, so
    the pointer is moved up to the first key that is missing, with the rest
    nested in its value. A parent that isn't an object is left for `set` to refuse.

    Raises:
        ValueError: no Kilo config is staged.
    """
    staged = [path for path in CONFIG_PATHS if path in files]
    if not staged:
        raise ValueError("no Kilo config file found in project assets")
    pointers = {p: value for path, sets in variant.set_pointers.items() if path in CONFIG_PATHS for p, value in sets.items()}
    spread = {path: dict(_with_missing_parents(parse_jsonc(files[path]), p, v) for p, v in pointers.items()) for path in staged}
    return variant.model_copy(update={"set_pointers": spread})


def _with_missing_parents(document, pointer: str, value) -> tuple[str, object]:
    tokens = pointer_tokens(pointer)
    for depth, token in enumerate(tokens[:-1]):
        if not isinstance(document, dict):
            break
        if token not in document:
            for key in reversed(tokens[depth + 1 :]):
                value = {key: value}
            return pointer_from(tokens[: depth + 1]), value
        document = document[token]
    return pointer, value


def _check_variant(variant: str, allowed: set[str]) -> None:
    if variant not in allowed:
        raise ValueError(f"variant must be one of {sorted(allowed)}, got {variant!r}")


def _sandbox_config(execution: str) -> str | tuple[str, str]:
    if execution == "local":
        return "local"
    if execution == "docker":
        return ("docker", str(COMPOSE_FILE))
    raise ValueError("execution must be 'local' or 'docker'")
