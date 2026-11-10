# Security policy

## Reporting a vulnerability

Report it privately. Don't open a public issue or pull request.

Use GitHub's private vulnerability reporting: on the repository's **Security** tab, choose **Report a vulnerability**. Only the maintainers see the report.

Include what you can of:

- what an attacker or a misbehaving agent could do
- the steps, eval file or fixture that show it
- the Whitney version (`whitney --version`), the target (`kilo` or `claude-code`) and the harness version
- whether a real credential was exposed. If one was, revoke it first, and don't put it in the report.

You'll get a reply within 7 days. Once a fix is released, the advisory is published and credits you, unless you'd rather not be named.

## Supported versions

Fixes go into the latest release and `main`. Whitney is pre-1.0, so older versions aren't patched. Forks pick up fixes by merging from upstream.

## What counts

Whitney runs AI agents on your machine against your configuration, so these are in scope:

- An API key or other secret reaching an eval log, a report, or a file in `results/` or `logs/`. Whitney redacts the run's secrets from the record.
- Whitney writing to the project given as `--project-path`. It should only ever copy from it.
- The user's own harness settings, instructions, MCP servers or credentials reaching a run they shouldn't. Each run is meant to get a temporary HOME.
- A benchmark task's sandbox reaching the host (`bigcodebench_hard_complete`).
- An eval file, fixture or dataset row that makes Whitney itself run something it shouldn't.
- A check or gate that passes a run it should fail. Teams use the results as evidence.

These are out of scope:

- **Gaps in a harness's own controls.** Report a weakness in Kilo's or Claude Code's permission system to that vendor, under its own policy. Whitney records the ones its evals find in [docs/findings/](docs/findings/), with whether the vendor has been told.
- **An agent doing something your instructions forbid.** That is what Whitney measures, and it belongs in the report.
- **The fake canary tokens in the eval fixtures.** They're not real secrets; see the Exceptions in [CONSTRAINTS.md](CONSTRAINTS.md).
