"""Spike T2: which instruction locations does Kilo actually load?

Each sample puts a unique token in one candidate location. Because Kilo's
model calls go through the Inspect bridge, the log holds the exact prompt
Kilo sent, so the check is deterministic: is the token in what the model saw?

Run:
    inspect eval spikes/load_order.py --model anthropic/claude-haiku-4-5
"""

import json

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.log import transcript
from inspect_ai.scorer import Score, Target, accuracy, scorer
from inspect_ai.solver import TaskState

from kilo_bridge import COMPOSE_FILE, PROJECT_DIR, kilo

from whitney.agent import DOCKER_HOME

GLOBAL_DIR = f"{DOCKER_HOME}/.config/kilo"

LAYOUTS = {
    # sample id: (files to create, kilo.jsonc contents)
    "root_agents_md": ({f"{PROJECT_DIR}/AGENTS.md": "- TOKEN-ROOT\n"}, {}),
    "dot_kilo_agents_md": ({f"{PROJECT_DIR}/.kilo/AGENTS.md": "- TOKEN-DOT-KILO\n"}, {}),
    "instructions_key": (
        {f"{PROJECT_DIR}/team-rules.md": "- TOKEN-INSTRUCTIONS\n"},
        {"instructions": ["team-rules.md"]},
    ),
    "instructions_key_to_dot_kilo": (
        {f"{PROJECT_DIR}/.kilo/AGENTS.md": "- TOKEN-INSTR-DOT-KILO\n"},
        {"instructions": [".kilo/AGENTS.md"]},
    ),
    "dot_kilo_config_dir": (
        {
            f"{PROJECT_DIR}/.kilo/kilo.jsonc": json.dumps({"instructions": ["team-rules.md"]}),
            f"{PROJECT_DIR}/team-rules.md": "- TOKEN-DOT-KILO-CONFIG\n",
        },
        None,
    ),
    "global_agents_md": ({f"{GLOBAL_DIR}/AGENTS.md": "- TOKEN-GLOBAL\n"}, {}),
}


@scorer(metrics=[accuracy()])
def token_in_prompt():
    """1.0 if the sample's token appears in any model request Kilo sent."""

    async def score(state: TaskState, target: Target) -> Score:
        sent = "\n".join(
            json.dumps([m.model_dump() for m in e.input], default=str)
            for e in transcript().events
            if e.event == "model"
        )
        token = state.metadata["token"]
        found = token in sent
        return Score(
            value=1.0 if found else 0.0,
            explanation=f"{token} {'found' if found else 'NOT found'} in prompt sent by Kilo",
        )

    return score


def _sample(id: str, files: dict[str, str], config: dict | None) -> Sample:
    """config=None means no project kilo.jsonc is written."""
    token = next(word for text in files.values() for word in text.split() if word.startswith("TOKEN-"))
    all_files = {f"{PROJECT_DIR}/README.md": "# test project\n", **files}
    if config is not None:
        all_files[f"{PROJECT_DIR}/kilo.jsonc"] = json.dumps(config)
    return Sample(
        id=id,
        input="Reply with exactly the word OK.",
        metadata={"token": token},
        files=all_files,
        setup=f"git -C {PROJECT_DIR} init -q",
    )


@task
def load_order_spike(model: str) -> Task:
    """One sample per candidate instruction location; model is the provider/model Kilo runs."""
    return Task(
        dataset=[_sample(id, files, cfg) for id, (files, cfg) in LAYOUTS.items()],
        solver=kilo(model=model),
        scorer=token_in_prompt(),
        sandbox=("docker", str(COMPOSE_FILE)),
    )
