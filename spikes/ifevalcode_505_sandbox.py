"""Score IFEvalCode row 505 in the real IFEvalCode Docker sandbox with a fixed answer.

No model is called, so this is not the eval rerun. It runs the upstream
scorer (inspect_evals 0.27.0) on two samples with the same answer: "before",
the row's check code as Whitney passed it until 2026-11-12, and "after", as
`scorer_check_code` passes it now. It shows what lands in /app/solution.py.

Run (results in docs/results/2026-11-12-ifevalcode-escapes.md):
    python spikes/ifevalcode_505_sandbox.py dataset/ifevalcode_cleaned.jsonl <log-dir> [compose.yaml]

The optional compose file replaces upstream's, for a machine where its image
won't build as it stands (behind a TLS-intercepting proxy, say); it must keep
`network_mode: none`.
"""

import json
import sys

from inspect_ai import Task
from inspect_ai import eval as inspect_eval
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import ModelOutput
from inspect_ai.solver import solver
from inspect_evals.ifevalcode.ifevalcode import COMPOSE_FILE
from inspect_evals.ifevalcode.scorer import ifevalcode_scorer

from whitney.interference import scorer_check_code

# Minimum escape cost: each run of escapable characters costs 1 if it is one character, else 2.
ANSWER = '''```python
import re


def min_escape_cost(input_str: str) -> int:
    run_lengths = [len(run) for run in re.findall(r"[<>&\\"']+", input_str)]
    cost_of = lambda length: 1 if length == 1 else 2
    return sum([cost_of(length) for length in run_lengths])
```'''


@solver
def fixed_answer():
    async def solve(state, generate):
        state.output = ModelOutput.from_content("none/none", ANSWER)
        return state

    return solve


def main(dataset: str, log_dir: str, compose_file: str) -> None:
    [row] = [record for record in map(json.loads, open(dataset, encoding="utf-8")) if record["id"] == 505]
    checks = {"before": row["check_correctness"], "after": scorer_check_code("python", row["check_correctness"])}
    samples = [
        Sample(
            id=f"505-{label}",
            input="row 505",
            metadata={"check_correctness": check, "check_instruction": row["check_instruction"], "programming_language": "python"},
        )
        for label, check in checks.items()
    ]
    task = Task(dataset=MemoryDataset(samples), solver=fixed_answer(), scorer=ifevalcode_scorer(), sandbox=("docker", compose_file))
    [log] = inspect_eval(task, model="mockllm/model", log_dir=log_dir, display="none")
    for sample in log.samples:
        score = next(iter(sample.scores.values()))
        print(f"== {sample.id}: {score.value}\n{score.explanation}\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else str(COMPOSE_FILE))
