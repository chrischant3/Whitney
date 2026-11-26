# whitney watch: alerts in a live Claude Code session

`whitney watch` watches your own Claude Code session while you work. When the agent breaks one of your rules, it tells you in the session, quoting the rule and the file it came from. It only alerts: it never blocks a tool call and never tells the model anything.

It's a first version, built from the spike in [tasks/plan-later.md](../tasks/plan-later.md), Task 66. Today it watches one kind of rule, "never commit `.env` files or secrets", on Claude Code. See "What it can't do yet" below before you rely on it.

## Try it

You need Whitney installed (README, "Setup"), git, and Claude Code. Use a project that is a git repository.

```bash
cd your-project
whitney watch install          # writes .claude/settings.local.json hooks and .whitney/watch.json
whitney watch                  # leave this running in its own terminal
```

Then start Claude Code in the project, in another terminal. When the session starts, the watcher's hook replies:

```
whitney watch is watching 1 rule: "Never commit .env files or secrets" (AGENTS.md)
```

If it replies `whitney watch isn't running for this project` instead, start the watcher and then start a new session. In the recorded `-p` runs, Claude Code kept this start message as the SessionStart hook's response in its stream, but didn't print it as a notice the way it printed the alert. How the interactive terminal shows it hasn't been checked yet: it's the first thing to look at when you try it.

To see an alert, put a made-up `.env` in the project (say `API_KEY=not-a-real-key-123`) and ask Claude to "add everything and commit it". After the commit, Claude Code shows:

```
PostToolUse:Bash says: whitney watch: a rule wasn't followed
PostToolUse:Bash says: Rule: "Never commit .env files or secrets" (AGENTS.md)
PostToolUse:Bash says: What happened: 1 new commit(s); committed env file(s): .env; secret found in: 47355f88cdc3:.env (.env API_KEY)
PostToolUse:Bash says: Commit(s): 47355f88cdc3
PostToolUse:Bash says: In: Bash: git add -A && git commit -q -m 'Save my work'
```

The watcher's terminal prints the same alert with the time. Afterwards, undo the commit (`git reset HEAD~1`) and delete the made-up `.env`.

To remove the hooks: `whitney watch uninstall`. It leaves `.whitney/watch.json` and every hook that isn't its own.

## The rules file

`.whitney/watch.json` names each rule, the file it lives in, and the check that judges it:

```json
{
  "version": 1,
  "rules": [
    {"source": "AGENTS.md", "rule": "Never commit .env files or secrets", "check": "no_secret_committed"}
  ]
}
```

`rule` is the text the alert quotes. When a session starts, the watcher looks for it in `source`, ignoring spacing. If someone has edited it out, the start message gives a warning. The rule is still judged, because the check doesn't depend on the words.

`check` can only be `no_secret_committed` today. It is the same check `whitney run` uses: a new commit fails it if it adds a `.env`-style file (`.env.example`, `.env.sample` and `.env.template` are allowed), or a file holding one of the values in the project's own `.env` files. Values shorter than 8 characters are left out, since they would match everywhere. `"params": {"env_files": false}` turns off the file-name part and searches for the values only.

Commit `.whitney/watch.json` so your team shares the rules. The hooks file, `.claude/settings.local.json`, holds paths on your machine, so keep it out of git. Claude Code's own docs call it the file for "you, in this one project only".

## What an alert means

- **"a rule wasn't followed"**: the check found positive evidence, such as a `.env` file in a commit the agent made during that tool call.
- **"couldn't tell whether a rule was followed"**: the check couldn't judge, and the alert says why. For example, the tool call ran a command that can erase commits (`git reflog expire`), or a committed file was over 5 MiB and wasn't searched.

An alert names the `.env` file and variable whose value was found, never the value. If the tool call's command contains a value, it is shown as `[secret]`.

Commits you make yourself, between the agent's tool calls, are never judged. Commits made before the watcher started aren't either.

## How it works

`whitney watch install` adds command hooks to `.claude/settings.local.json`, for SessionStart, PreToolUse, PostToolUse, PostToolUseFailure (which Claude Code runs instead of PostToolUse when a call fails, such as a Bash command that exits non-zero) and Stop (the end of a turn, after which no call the agent started is still running). Each runs `whitney/watch_client.py` with `python -I -S`. The client imports only the standard library, sends the event to the watcher over a Unix socket in a folder only you can open, and prints the watcher's reply. The reply is only ever a `systemMessage`. Claude Code shows a synchronous hook's `systemMessage` to you and doesn't send it to the model ([hooks reference](https://code.claude.com/docs/en/hooks), read 2026-11-15). The hooks aren't `async`: an async hook's `systemMessage` would go to the model instead.

The watcher keeps the checks loaded, so a hook costs a tool call about 30 to 40 ms. Python on its own takes about 10 ms, and most of the rest is the client starting, not the check. On a tool call where nothing was committed, the watcher only compares the repository's refs and reflog with what it saw last time, which took about 4 ms on Whitney's own 666-commit history.

If the watcher isn't running or doesn't answer within 5 seconds, the hook prints nothing and the tool call goes ahead. The session's start message is the one place that says so.

## The evidence

[docs/results/2026-11-15-watch-live/](results/2026-11-15-watch-live/) comes from `spikes/watch_live.py`. It ran real Claude Code (2.1.325) with these hooks and the watcher, against a scripted stand-in for the model on 127.0.0.1, so no API key and no cost. Each scenario folder holds Claude Code's stream (`stream.jsonl`, with `--include-hook-events`), the watcher's output, a summary of every request the stand-in model received, and `summary.json`.

- `commits-env`: the agent runs `git add -A && git commit`. The watcher alerts, quoting the rule, and Claude Code turns the alert into a `"level": "notice"` system message. No request to the model contains the alert.
- `commits-app-only`: the agent commits `app.py` alone. No alert.
- `commits-env-then-fails`: the same commit as `commits-env`, then `exit 3`. Claude Code runs PostToolUseFailure instead of PostToolUse for a call that fails, and the watcher alerts from it.
- `latency.json`: the timings above.
- Neither scenario's saved files contain the fixture's canary values.

A real model was not used. What a real model does after seeing the alert can't be known from these runs: the model never sees it.

## What it can't do yet

- **Claude Code only.** Kilo needs a plugin as its client (Kilo's plugins are JavaScript in Kilo's own process). Whether to build `whitney watch` further, and its first task on each target, is still to be decided ([#101](https://github.com/chrischant3/Whitney/issues/101)).
- **One kind of rule.** Prose rules ("write tests first") need a judge, and no live judge exists yet.
- **Unix only**, because of the socket.
- **Shown in the VS Code extension: not checked.** Claude Code's docs don't say how the extension shows a `systemMessage`. `docs/vscode-manual-check.md` is where a person would record it.
- **Commits during a tool call are blamed on the agent.** A commit you make in your own terminal while a tool call is running is judged as the agent's.
- **A commit nothing points to isn't seen:** `git commit-tree` without `update-ref`.
- **A denied tool call raises no alert.** Claude Code doesn't run PostToolUse for a call it refused, and a refused call commits nothing.
