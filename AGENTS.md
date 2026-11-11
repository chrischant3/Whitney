# Project Instructions

## Problem Statement
Evaluation of AI rules, instructions and skills is misunderstood. Development teams, auditors and infosec need confidence that when an AI is given instructions it will follow them. Companies need to standardise their processes (configs, skills, AGENTS.md files) to give them control, and may need to evaluate both AI platform providers (e.g. Claude) and harnesses (e.g. Kilo.ai). Today there is no easy-to-read, forkable way to produce evidence of how closely an AI follows instructions, wherever those instructions are loaded from.

## Primary Users
Development teams, auditors and infosec teams who need evidence that AI agents follow the company's standard instructions and honour its permissions. Teams fork the framework and tailor the template and scenarios to their own needs.

## Success Criteria
MVP (from docs/proposal.md):
- A Kilo template containing `AGENTS.md` with a single rule "- Never commit .env files or secrets", usable by any team.
- The evals framework and its evals report how accurately the instructions are followed.
- The evals test that permissions in `kilo.jsonc` are honoured.
- The evals test the template's instructions when used in both Kilo CLI and the VS Code extension.

General: results show how closely the AI follows instructions, wherever the instructions are loaded from; tests provide evidence that triggers, routing and permissions behave as desired.

## Technology Stack
Python and inspect-ai (UK AISI Inspect) for the evaluation framework, with inspect_evals' IFEvalCode where applicable. Test scenarios are written as JSON, one file per instruction source (`skill-name.json`, `agents-md.json`, `kilo-jsonc.json`). Each **target** pairs a harness with its own template and model route: `kilo` (Kilo CLI and VS Code extension, template `AGENTS.md` + `kilo.jsonc`, models through the Kilo Gateway) and `claude-code` (Claude Code CLI, template `AGENTS.md` only, models through the Anthropic API). `--target` names the harness and `--model` the model under evaluation; neither has a default. Structure inspired by https://github.com/addyosmani/agent-skills (evals/).

## Deployment Target
Run locally during development. The `kilo` target reaches the model through the Kilo Gateway (via the user's global Kilo config) or a provider API key the user names (`--api-key-env`); the `claude-code` target uses `ANTHROPIC_API_KEY` and runs with `--setting-sources project` so the user's own Claude settings stay out; Docker sandbox execution remains available for CI and isolated runs (kilo target only; Kilo is told the matching Anthropic or OpenAI model name, since its own Gateway provider can't reach Inspect's bridge). Run in a CI pipeline on each template change.

## Additional Context
None provided. Source proposal: docs/proposal.md. Agreed MVP decision: VS Code extension coverage = automated evals on Kilo CLI + automated parity check that both surfaces read the same config/AGENTS.md + a short scripted manual check in the extension with recorded results. Agreed template layout: the rules live in `AGENTS.md` at the template (project) root, which Kilo loads automatically; teams edit that file to add their own instructions, so the core rule must survive their edits (`.kilo/AGENTS.md` is not loaded by Kilo on its own; see docs/load-order.md). Agreed MVP permission test: `"external_directory": "ask"` in `kilo.jsonc` is honoured. Agreed 2026-11-03: the project is no longer Kilo-only; see the two targets above and tasks/plan.md Phase 4. Future-work items in the proposal (hierarchy, skills, agents, REVIEW.md, hooks) are out of scope until the MVP is complete.

## Constraints
Read CONSTRAINTS.md before writing code. Do not weaken it to make a change pass. Run `python scripts/check.py task` before handing work back.

## Evidence
An agent gathers the evidence that a task is done. Write every verification and evidence step so an agent can carry it out and leave something a person can check afterwards: the command it ran, the output, a saved log or fixture, or a file under `docs/results/`. Don't write a step as "Manual:" or leave one for a person to do. If you can't see how an agent could gather a piece of evidence, say so and ask before writing the step. A decision that is the maintainer's to make, such as a changed expectation, is a decision to ask for. It is not a verification step.

## Git workflow
Follows `agent-skills:git-workflow-and-versioning`. When you commit, use a feature branch (`feature/`, `fix/`, `chore/` or `refactor/`). When asked to ship, push it over SSH with `git push -u origin <branch>` and open a pull request. Only the maintainer merges, tags and releases.
- In Claude Code, `.claude/hooks/git-workflow-gate.sh` enforces part of this. It blocks commits and pushes while on main, pushes that target main, force pushes, PR merges, `gh api` writes, and a commit that includes a `.env` file (templates such as `.env.example` aside). `.claude/settings.json` denies `git add` of a `.env` file, so the maintainer commits fixture `.env` files, and auto-allows pushes only for the four branch prefixes. Branch names, SSH, tags and releases are by instruction.
- Before `gh pr create`, run the five pre-PR review skills in order on the diff against main: `agent-skills:code-review-and-quality`, `agent-skills:code-simplification`, `agent-skills:security-and-hardening`, `agent-skills:documentation-and-adrs`, `agent-skills:shipping-and-launch`. Then run `python scripts/check.py task` on the final commit. The gate refuses the PR until the review marker exists for the current HEAD; its message gives the command.
- Write a multi-line commit message with the Write tool to a file outside the working tree, then `git commit -F <file>`. The gate reads each line of a command as a command, so a message passed inline, or written with a heredoc, can be refused.
- Kilo sessions have no gate. These rules apply to them by instruction only.
