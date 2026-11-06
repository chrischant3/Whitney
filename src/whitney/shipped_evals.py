"""Where Whitney's own eval files are, named once for the tasks that run them and the validation that checks them.

It imports nothing from Whitney or Inspect, so whitney.validate can use it
without loading inspect_ai.
"""

from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
AGENTS_MD_EVALS = "agents-md.json"  # relative to EVALS_DIR
KILO_JSONC_EVALS = "configuration/kilo-jsonc.json"  # relative to EVALS_DIR
KILO_JSONC_FILE = EVALS_DIR / KILO_JSONC_EVALS
