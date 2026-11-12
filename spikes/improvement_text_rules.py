"""Task 40 evidence: apply the improvement report's text rules to real files in the repo, as a run of each target would stage them.

Usage: .venv/bin/python spikes/improvement_text_rules.py "$PWD"
"""
import sys
from pathlib import Path

from whitney.improve import RULES, text_findings
from whitney.project_files import load_project_files_for

ROOT = Path(sys.argv[1])
for target, project, evals in [
    ("kilo", ROOT / "examples/consumer", ROOT / "examples/consumer/evals"),
    ("kilo", ROOT / "template/kilo", ROOT / "evals"),
    ("claude-code", ROOT / "template/claude-code", ROOT / "evals"),
    ("claude-code", ROOT, ROOT / "evals"),
]:
    staged = load_project_files_for(target, project, evals_path=evals)
    for path in sorted(p for p in staged if p.endswith(".md")):
        if target == "claude-code" and project == ROOT and not (path in ("AGENTS.md",) or path.endswith("SKILL.md")):
            continue
        found = text_findings(path, staged)
        print(f"### `{project.relative_to(ROOT) if project != ROOT else '.'}` ({target}): `{path}`, {len(staged[path].splitlines())} lines\n")
        if not found:
            print("No finding.\n")
        for f in found:
            print(f"- **`{f.rule}`**: {RULES[f.rule][0]}.")
            for e in f.evidence:
                print(f"  - {e}")
        print()
