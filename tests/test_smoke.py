import whitney


def test_package_imports():
    assert whitney.__doc__


def test_the_lock_pins_every_declared_dependency_with_hashes():
    # requirements.lock is what a reproducible install uses (README); a dependency added to
    # pyproject.toml without re-running `uv pip compile` would be missing from it.
    import re
    import tomllib
    from pathlib import Path

    root = Path(__file__).parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    declared = [*project["dependencies"], *(d for extra in project["optional-dependencies"].values() for d in extra)]
    lock = (root / "requirements.lock").read_text()
    pinned = {name.lower(): version for name, version in re.findall(r"^([A-Za-z0-9_.-]+)==(\S+) \\$", lock, re.M)}
    for requirement in declared:
        name = re.split(r"[\[<>=!~ ;]", requirement, maxsplit=1)[0].lower()
        assert name in pinned, f"{name} is declared in pyproject.toml but not pinned in requirements.lock"
        exact = re.search(r"==([^,;\s]+)", requirement)
        if exact:
            assert pinned[name] == exact.group(1), f"{name}: pyproject.toml pins {exact.group(1)}, the lock {pinned[name]}"
    assert all("--hash=sha256:" in block for block in re.split(r"\n(?=[A-Za-z0-9_.-]+==)", lock)[1:])
