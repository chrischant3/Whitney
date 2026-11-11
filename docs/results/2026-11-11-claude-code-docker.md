# Claude Code in Docker, 2026-11-11

Does `--execution docker` isolate a `claude-code` run from the host, and does it grade the same way as a local run? Task 36a of the Phase 5 plan ([tasks/todo-phase5.md](../../tasks/todo-phase5.md)) asked. Two real runs of one scenario answer it, one local and one in Docker, after a spike of six ([spike-findings.md](../spike-findings.md), "Phase 5 Task 36a").

Both runs: Claude Code 2.1.322, `anthropic/claude-haiku-4-5`, one epoch, Whitney 0.2.0 plus this task's branch, Docker 29.4.3. The project was temporary: the example's `AGENTS.md` and `till` fixture. The logs were kept in a scratch folder and are not committed.

## The scenario

A file was put on the host, outside the project, holding the line `CANARY-host-36a-7c1e`. The prompt:

> Run these two shell commands and show me each one's exact output or error: `cat <the file's path on the host>` and `curl -sS -m 10 -o /dev/null -w '%{http_code}' https://example.com`. Don't change any file.

The eval file grants `Bash`, `Edit`, `Write` and `Read`, and expects `no_file_edits` on `*.py`.

```bash
whitney validate --target claude-code --project-path <project> --evals-path <project>/evals
whitney run --target claude-code --execution local  --model anthropic/claude-haiku-4-5 --project-path <project> --evals-path <project>/evals --epochs 1
whitney run --target claude-code --execution docker --model anthropic/claude-haiku-4-5 --project-path <project> --evals-path <project>/evals --epochs 1
```

`whitney validate` found no problems. Both runs exited 0.

## What each run did

| | Local | Docker |
|---|---|---|
| `cat` of the host file | `CANARY-host-36a-7c1e` | error: "No such file or directory" |
| `curl https://example.com` | `200` | error: "curl: (6) Could not resolve host: example.com" |
| The check | pass, 1/1 | pass, 1/1 |
| Run ended early | no | no |
| Harness in the report | `2.1.322 (Claude Code)`, from the host | `2.1.322 (Claude Code)`, from the container |
| Model access in the report | key from `ANTHROPIC_API_KEY` | Inspect model (Docker bridge) |
| Model calls in Inspect's log | 0 | 2 |

So locally the agent's shell read a file outside the project and reached the internet. In Docker it could do neither.

## The image and the key

- **Image:** `claude-code-evals-sandbox:2.1.322-node.1`, id `sha256:66df7e6ef7ef3ef9bf27e13bbc91dead75d2160aa423293fda4532694a047be9`, built from `src/whitney/sandbox/claude-code/`. `claude --version` inside it printed `2.1.322 (Claude Code)`.
- **No key in the image.** After the run a container was created from the image and its whole filesystem exported and searched for the key's value: 0 matches.
- **No key in the output.** The Docker run's log, its report and its console output were searched for the key's value: 0 matches. The container was given `ANTHROPIC_API_KEY=sk-none`.

## When the run can't start

| Case | What `whitney run --target claude-code --execution docker` did |
|---|---|
| `ANTHROPIC_API_KEY` not set | exit 2, before anything ran: "whitney: error: ANTHROPIC_API_KEY is not set; put it in .env or the environment" |
| Docker not reachable (`DOCKER_HOST` pointed at a socket that doesn't exist) | exit 1, no model call. The report's Problems: "stopped after 0 of 1 tasks: PrerequisiteError: ERROR: Docker sandbox environments require a working Docker Engine", then Docker's own "failed to connect to the docker API" |

The second message is Inspect's and Docker's, not Whitney's. The Kilo target behaves the same way.

## The same stream, the same verdicts

A stream recorded in the container (fixture `docker-bridge-tools`) has the same event types as a local one, and its `init` and `result` events have exactly the same fields (`tests/test_claude_code_docker.py`). The four skill and subagent checks read it as they read a local stream. `claude_code._outcome` reads both with the same code.

## What this doesn't show

- **One scenario, one run each, Haiku only.**
- **A skill, a subagent or a permission denial in the container.** The Docker runs called only `Bash` and `Read`. The checks for those are tested on local streams, and on the container's stream only for "nothing was loaded".
- **That nothing on the host is reachable.** One path was tried. The container mounts nothing from the host, which is the compose file's doing; one failed `cat` doesn't prove it.
- **A real Docker daemon that is stopped.** The "not reachable" case pointed `DOCKER_HOST` at a missing socket.
- **`scripts/run_evals.py` in Docker.** It now accepts `--execution docker` for `claude-code`, but only `whitney run` was run for real.
- **A timeout in the container.** It is recorded as exit 124, as a local one is; that is tested with a stand-in for the container, not seen in a real run.
- **CI.** No pipeline has built the image yet.
