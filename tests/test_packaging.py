"""The package as someone else installs it: what the wheel holds, and what works once it is installed.

Building the wheel and reading its contents runs in the default suite. The
tests marked `packaging` install that wheel in a temporary venv and run its
commands; they are slower, so only `check.py full` runs them
(`pytest -m packaging tests/test_packaging.py` by hand).
"""

import importlib.metadata
import importlib.util
import shutil
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path

import pytest

from whitney import agent, cli

ROOT = Path(__file__).parents[1]
BUILD_INPUTS = ("pyproject.toml", "LICENSE", "README.md", "src")
BUILD_TIMEOUT = 300  # seconds; a cold cache fetches setuptools first


def build_wheel(tmp: Path) -> Path:
    """Build the wheel from a copy of the package's sources, so the repository gains no build folders.

    With pip where the environment has it (the spec's `pip wheel --no-deps`),
    else with uv, which a uv-made venv has in pip's place.
    """
    source = tmp / "source"
    source.mkdir()
    for name in BUILD_INPUTS:
        copy = shutil.copytree if (ROOT / name).is_dir() else shutil.copy
        copy(ROOT / name, source / name)
    out = tmp / "dist"
    if importlib.util.find_spec("pip"):
        command = [sys.executable, "-m", "pip", "wheel", "--quiet", "--no-deps", "-w", str(out), str(source)]
    elif uv := _uv():
        command = [uv, "build", "--quiet", "--wheel", "--out-dir", str(out), str(source)]
    else:
        pytest.fail(
            "building the wheel needs pip in this environment or uv: install pip in the venv, "
            "or put uv on PATH (it was also looked for beside python and in ~/.local/bin)"
        )
    done = subprocess.run(command, capture_output=True, encoding="utf-8", timeout=BUILD_TIMEOUT)
    assert done.returncode == 0, done.stderr[-2000:]
    [wheel] = out.glob("whitney-*.whl")
    return wheel


def _uv() -> str | None:
    """uv, from PATH, from beside this python, or from where its installer puts it."""
    beside = [Path(sys.executable).parent / "uv", Path.home() / ".local" / "bin" / "uv"]
    return shutil.which("uv") or next((str(path) for path in beside if path.is_file()), None)


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    return build_wheel(tmp_path_factory.mktemp("wheel"))


def test_the_wheel_holds_the_sandbox_files(wheel):
    names = zipfile.ZipFile(wheel).namelist()

    assert "whitney/sandbox/compose.yaml" in names
    assert "whitney/sandbox/Dockerfile" in names


def test_the_wheel_holds_the_package_and_nothing_else_from_the_repository(wheel):
    # no template/, evals/, dataset/ or examples/: a consumer brings their own project and evals
    names = zipfile.ZipFile(wheel).namelist()
    version = importlib.metadata.version("whitney")

    assert {name.split("/")[0] for name in names} == {"whitney", f"whitney-{version}.dist-info"}
    assert not [name for name in names if any(part in name.split("/") for part in ("template", "evals", "dataset", "examples"))]


def test_the_compose_file_is_package_data_beside_its_dockerfile():
    # compose.yaml says `build: .`, so the Dockerfile must travel with it
    assert agent.COMPOSE_FILE.is_file()
    assert agent.COMPOSE_FILE.parent == Path(agent.__file__).parent / "sandbox"
    assert (agent.COMPOSE_FILE.parent / "Dockerfile").is_file()


def test_version_prints_the_packages_version(capsys):
    assert cli.main(["--version"]) == 0

    assert capsys.readouterr().out == f"whitney {importlib.metadata.version('whitney')}\n"


def test_a_checkout_that_isnt_installed_still_runs_and_says_so(monkeypatch, capsys):
    def not_found(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr("whitney.tasks.package_version", not_found)

    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out == "whitney not installed\n"
    assert cli.main(["checks"]) == 0


@pytest.mark.parametrize("name", ["scenario_eval", "agents_md", "kilo_jsonc"])
def test_the_entry_point_registers_each_task_under_the_packages_name(name):
    # run apart, so the lookup sees only what loading the entry point registered
    code = (
        "from inspect_ai._util.entrypoints import ensure_entry_points\n"
        "from inspect_ai._util.registry import registry_lookup\n"
        "ensure_entry_points('whitney')\n"
        f"assert registry_lookup('task', 'whitney/{name}') is not None\n"
    )

    done = subprocess.run([sys.executable, "-c", code], capture_output=True, encoding="utf-8", timeout=120)

    assert done.returncode == 0, done.stderr[-2000:]


@pytest.fixture(scope="module")
def installed(wheel, tmp_path_factory) -> Path:
    """A venv holding the wheel, installed without its dependencies, which it borrows from this environment.

    A path file lends it this environment's packages, so nothing is
    downloaded. A path file's own lines aren't read for further path files,
    so this checkout's editable install doesn't come with them: `whitney` is
    the wheel's.
    """
    venv = tmp_path_factory.mktemp("installed") / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, capture_output=True, timeout=BUILD_TIMEOUT)
    python = venv / "bin" / "python"
    subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", "--no-deps", "--no-index", str(wheel)],
        check=True, capture_output=True, timeout=BUILD_TIMEOUT,
    )
    site = subprocess.run(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        check=True, capture_output=True, encoding="utf-8",
    ).stdout.strip()
    (Path(site) / "borrowed.pth").write_text(sysconfig.get_paths()["purelib"] + "\n", encoding="utf-8")
    return venv


def run_installed(venv: Path, *command: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(venv / "bin" / command[0]), *command[1:]], capture_output=True, encoding="utf-8", cwd=cwd, timeout=BUILD_TIMEOUT,
    )


@pytest.mark.packaging
def test_installed_whitney_is_the_wheels_and_finds_its_compose_file(installed, tmp_path):
    code = "from whitney import agent; print(agent.__file__); print(agent.COMPOSE_FILE); print(agent.COMPOSE_FILE.is_file())"

    done = run_installed(installed, "python", "-c", code, cwd=tmp_path)

    module, compose, found = done.stdout.split()
    assert Path(module).is_relative_to(installed), done.stderr[-2000:]
    assert Path(compose).is_relative_to(installed) and compose.endswith("whitney/sandbox/compose.yaml")
    assert found == "True"


@pytest.mark.packaging
def test_installed_whitney_prints_its_version(installed, tmp_path):
    done = run_installed(installed, "whitney", "--version", cwd=tmp_path)

    assert (done.returncode, done.stdout) == (0, f"whitney {importlib.metadata.version('whitney')}\n"), done.stderr[-2000:]


@pytest.mark.packaging
def test_installed_whitney_validates_the_consumer_example(installed, tmp_path):
    example = ROOT / "examples" / "consumer"

    done = run_installed(
        installed, "whitney", "validate", "--target", "kilo", "--project-path", str(example), "--evals-path", str(example / "evals"),
        cwd=tmp_path,
    )

    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    assert "0 problem(s)" in done.stdout


@pytest.mark.packaging
def test_installed_inspect_resolves_whitneys_tasks_by_name(installed, tmp_path):
    code = (
        "from inspect_ai._util.entrypoints import ensure_entry_points\n"
        "from inspect_ai._util.registry import registry_lookup\n"
        "ensure_entry_points('whitney')\n"
        "print(registry_lookup('task', 'whitney/agents_md').__module__)\n"
    )

    done = run_installed(installed, "python", "-c", code, cwd=tmp_path)

    assert (done.returncode, done.stdout) == (0, "whitney.tasks\n"), done.stderr[-2000:]
