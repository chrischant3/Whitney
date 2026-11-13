"""Kilo instruction evals: scenario loading, template checks and Inspect tasks.

The Python API (docs/specs/package-and-expected-results.md, "Python API"):

    import whitney

    files = whitney.discover("evals")                       # every eval file, loaded and checked against the schema
    found = whitney.validate(".", "evals", target="kilo")   # found.problems, found.warnings, as `whitney validate` prints them
    run = whitney.run(target="kilo", model="kilo/anthropic/claude-haiku-4.5", project_path=".", evals_path="evals")
    # run.exit_code, run.report, run.report_path, run.problems, run.warnings, run.gate_failures, run.error, run.logs

`whitney run` and `whitney validate` are thin layers over `run` and
`validate`. The names are looked up when first used, so `import whitney`, which
every `import whitney.<module>` runs first, loads neither Inspect nor the CLI.
"""

import importlib

# Name -> the module that defines it. No submodule may share a name with one of these: importing a submodule
# binds it to the package under its own name, which would hide the function here.
_API = {
    "run": "whitney.cli",
    "validate": "whitney.cli",
    "RunResult": "whitney.cli",
    "CannotRun": "whitney.cli",
    "discover": "whitney.evalfile",
}


def __getattr__(name: str):
    """The API's names, imported on first use (PEP 562); AttributeError for any other name."""
    if name in _API:
        return getattr(importlib.import_module(_API[name]), name)
    raise AttributeError(f"module 'whitney' has no attribute {name!r}")
