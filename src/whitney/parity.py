"""Check that two Kilo runtimes (e.g. the CLI and the VS Code extension's bundled runtime) resolve the template the same way.

    python -m whitney.parity --cli "$(which kilo)" --other /path/to/extension/kilo [--template template/kilo]

The evals run against the CLI. This is the automated half of the evidence
that their results carry over to the VS Code extension: same runtime version,
and the same permission and instructions resolved from the same template.
The other half is the manual check in docs/vscode-manual-check.md.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from whitney import targets

COMPARED_KEYS = ("permission", "instructions")
# Seconds before a runtime that hangs is stopped, so the check fails instead of waiting forever.
DEBUG_CONFIG_TIMEOUT = 120
VERSION_TIMEOUT = 60


def resolve_config(binary: str, template_dir: Path, scratch: Path) -> dict:
    """Run `<binary> debug config` in a copy of the template, with an empty HOME.

    The empty HOME keeps the caller's own global Kilo config out of the result,
    so only the template (plus Kilo's defaults) is resolved.
    """
    project = scratch / "project"
    home = scratch / "home"
    shutil.copytree(template_dir, project)
    home.mkdir()
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local/share"),
        "PATH": os.pathsep.join(
            [str(Path(binary).absolute().parent), "/usr/local/bin", "/usr/bin", "/bin", "/opt/homebrew/bin"]
        ),
    }
    result = subprocess.run(
        [binary, "debug", "config", "--pure"], cwd=project, env=env, capture_output=True, text=True, timeout=DEBUG_CONFIG_TIMEOUT
    )
    if result.returncode != 0:
        raise RuntimeError(f"{binary} debug config failed: {result.stderr.strip()[-500:]}")
    return json.loads(result.stdout[result.stdout.index("{") :])


def runtime_version(binary: str) -> str:
    """`<binary> --version`, stripped."""
    return subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=VERSION_TIMEOUT).stdout.strip()


def compare_resolved(cli: dict, other: dict, keys: tuple[str, ...] = COMPARED_KEYS) -> list[str]:
    """Differences between two resolved configs in the keys that decide permissions and instructions."""
    return [
        f"{key}: CLI={json.dumps(cli.get(key))} other={json.dumps(other.get(key))}"
        for key in keys
        if cli.get(key) != other.get(key)
    ]


def main(argv: list[str] | None = None) -> int:
    """Compare the two runtimes; print the differences; return 1 if there are any."""
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--cli", required=True, help="path to the Kilo CLI binary")
    p.add_argument("--other", required=True, help="path to the other runtime, e.g. the extension's bundled kilo")
    p.add_argument("--template", default=str(targets.template_dir("kilo")), help="the template to resolve (default: the shipped Kilo template)")
    args = p.parse_args(argv)

    problems = []
    versions = {name: runtime_version(path) for name, path in (("CLI", args.cli), ("other", args.other))}
    print(f"versions: CLI {versions['CLI']}, other {versions['other']}")
    if versions["CLI"] != versions["other"]:
        problems.append(f"runtime versions differ: {versions}")

    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        cli_cfg = resolve_config(args.cli, Path(args.template), Path(a))
        other_cfg = resolve_config(args.other, Path(args.template), Path(b))
    problems += compare_resolved(cli_cfg, other_cfg)

    for problem in problems:
        print(problem)
    print("parity OK" if not problems else f"{len(problems)} difference(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
