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

# Name -> the module that defines it. `validate` is the whitney.validate module itself, which can be called
# (see the end of validate.py): a function of that name here would be replaced by the module whenever it is imported.
_API = {
    "run": "whitney.cli",
    "RunResult": "whitney.cli",
    "CannotRun": "whitney.cli",
    "discover": "whitney.evalfile",
}


def __getattr__(name: str):
    """The API's names, imported on first use (PEP 562); AttributeError for any other name."""
    if name == "validate":
        return importlib.import_module("whitney.validate")
    if name in _API:
        return getattr(importlib.import_module(_API[name]), name)
    raise AttributeError(f"module 'whitney' has no attribute {name!r}")
