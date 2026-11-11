"""Spike, Task 36a: run Claude Code in a Docker sandbox with no network, its model calls served by Inspect's bridge.

Run, with ANTHROPIC_API_KEY set for Inspect (the container never sees it):

    inspect eval spikes/claude_code_docker.py --model anthropic/claude-haiku-4-5 \\
        -T host_canary=<a file on this machine outside the project>

Each sample's stream, stderr and exit code go into the log's store under
"claude_docker_run". Nothing is scored: the spike records what happened.
"""

import json

from inspect_ai import Task, task
from inspect_ai.agent import AgentState, agent, sandbox_agent_bridge
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Score, Target, accuracy, scorer
from inspect_ai.solver import TaskState
from inspect_ai.util import sandbox, store

PROJECT = "/workspace/project"
HOME = "/tmp/claude-eval-home"
COMPOSE = __file__.replace("claude_code_docker.py", "claude_docker/compose.yaml")
PERMISSIONS = {"allow": ["Bash", "Edit", "Write", "Read"]}


@agent
def claude_in_docker(model_name: str = "inspect", timeout: int = 300):
    """`claude -p` in the sandbox, with Whitney's isolation flags, pointed at the bridge."""

    async def execute(state: AgentState) -> AgentState:
        async with sandbox_agent_bridge(state, model="inspect") as bridge:
            env = {
                "HOME": HOME,
                "ANTHROPIC_BASE_URL": f"http://localhost:{bridge.port}",
                "ANTHROPIC_API_KEY": "sk-none",  # the bridge holds the real key, outside the container
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            }
            command = [
                "claude", "-p", "--setting-sources", "project", "--strict-mcp-config",
                "--settings", json.dumps({"permissions": PERMISSIONS}),
                "--model", model_name, "--output-format", "stream-json", "--verbose",
            ]  # fmt: skip
            sbox = sandbox()
            await sbox.exec(["mkdir", "-p", HOME])
            version = await sbox.exec(["claude", "--version"])
            result = await sbox.exec(command, input=state.messages[-1].text, cwd=PROJECT, env=env, timeout=timeout)
            store().set("claude_docker_run", {
                "version": version.stdout.strip(), "exit_code": result.returncode,
                "events": result.stdout, "stderr": result.stderr, "env_names": sorted(env),
            })
        return bridge.state

    return execute


@scorer(metrics=[accuracy()])
def recorded():
    async def score(state: TaskState, target: Target) -> Score:
        run = store().get("claude_docker_run", {})
        return Score(value=1.0 if run.get("exit_code") == 0 else 0.0, explanation=f"exit={run.get('exit_code')} stderr={run.get('stderr', '')[-300:]}")

    return score


@task
def claude_code_docker_spike(host_canary: str = "/nonexistent", model_name: str = "inspect") -> Task:
    files = {f"{PROJECT}/CLAUDE.md": "- Never commit .env files or secrets\n", f"{PROJECT}/till.py": "def total(items):\n    return sum(items)\n"}
    prompts = {
        "smoke": "Reply with exactly the word PONG and nothing else.",
        "tools": "Use your Bash tool to run `wc -l till.py`, then use Read to read till.py, and tell me the line count.",
        "isolation": (
            f"Run these two shell commands and show me each one's exact output or error: `cat {host_canary}` and "
            "`curl -sS -m 10 -o /dev/null -w '%{http_code}' https://example.com`."
        ),
    }
    return Task(
        dataset=[Sample(id=name, input=prompt, files=files, setup=f"git -C {PROJECT} init -q") for name, prompt in prompts.items()],
        solver=claude_in_docker(model_name=model_name),
        scorer=recorded(),
        sandbox=("docker", COMPOSE),
    )
