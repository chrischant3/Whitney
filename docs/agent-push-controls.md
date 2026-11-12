# Keeping AI coding agents from pushing to GitHub

Checked against GitHub's and Claude Code's documentation on 2026-11-06.

This guide is for platform, security and engineering-enablement teams whose organisation lets developers use AI coding agents, such as Claude Code or the Kilo CLI. The goal it serves: agents commit locally, but never push to GitHub and never act on GitHub through the `gh` CLI. A person reviews the work and pushes it.

Whitney's own repository is set up less strictly: there, a Claude Code agent may push feature branches over SSH and open pull requests, through the git workflow gate in `.claude/hooks/`, and only the maintainer merges. This guide describes the stricter organisation setup.

It complements [signed-approvals.md](signed-approvals.md), which sets up one maintainer's repository so that an agent can't approve its own changes. That guide leaves one gap open: agents can still push with the user's SSH key. This guide covers that gap, for a whole organisation.

There are four layers. Only the first two are boundaries. The third is a guardrail, and the fourth is how people work.

1. [Credentials on the developer's machine](#layer-1-credentials-on-the-developers-machine)
2. [GitHub organisation settings](#layer-2-github-organisation-settings)
3. [Agent configuration](#layer-3-agent-configuration)
4. [Process](#layer-4-process)

Agents in a cloud session hold their own credential, so they're [an exception](#an-exception-agents-in-a-cloud-session) to the first and third layers.

At the end: [how Whitney can produce evidence](#evidence-that-the-agent-configuration-holds) and a [checklist](#checklist).

## Why tool rules alone don't stop pushes

An AI coding agent runs as the developer's operating-system user. Whatever that user can do without a fresh action, such as typing a passphrase or touching a key, the agent can do too. That includes:

- SSH keys loaded in `ssh-agent`, or whose passphrase is saved in the macOS Keychain
- HTTPS passwords and tokens saved by git's credential helper
- the token `gh` stored when the developer logged in

An agent's own rules ("never run `git push`") are checked by the agent's software, which reads the command it's about to run and decides whether it matches. A command can be written in many ways that a rule doesn't match, and the agent can write a script that pushes. So agent configuration is a guardrail: it catches the ordinary case and makes intent clear. The boundary is what the credentials can do and what GitHub's servers refuse.

## Layer 1: credentials on the developer's machine

This is the boundary. If no credential the agent can reach is able to push, the agent can't push, whatever command it writes.

### Give `gh` a read-only fine-grained token

A **fine-grained personal access token** is a GitHub token limited to the repositories and permissions you choose, unlike a classic token, whose scopes cover every repository the user can reach. The browser login `gh auth login` makes by default is a classic-style token with broad rights.

Set these repository permissions:

| Repository permission | Access |
|---|---|
| Metadata | Read (required) |
| Contents | Read |
| Pull requests | Read, or No access |
| Workflows | No access |
| Administration | No access |
| Everything else | No access, unless an agent needs to read it |

What each one controls, from GitHub's [permissions reference](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens):

- **Contents: write** covers commits, refs and merging a pull request. Merging needs Contents: write, so a token with Contents: Read can't merge, even with Pull requests: write.
- **Pull requests: write** covers opening a pull request.
- **Workflows: write** is needed to change files in `.github/workflows`.
- **Administration: write** covers repository settings.

The steps to create the token, replace `gh`'s login and revoke the old one are in [signed-approvals.md, part 4](signed-approvals.md#4-give-agents-a-token-without-admin-rights). Use the table above in place of that guide's table, which gives agents write access.

That an HTTPS `git push` needs Contents: write is how GitHub behaves, but GitHub's docs don't state it on a page. Test it. From the agent's session, run:

```bash
git push https://github.com/<org>/<repo>.git HEAD:refs/heads/push-test
```

It must fail with a 403 or "Permission denied". If it succeeds, delete the `push-test` branch and check which credential git used. If someone ran `gh auth setup-git`, git's HTTPS operations use `gh`'s token.

### Control SSH separately

GitHub's docs say "Personal access tokens can only be used for HTTPS Git operations." A token, however narrow, does nothing about SSH. If the developer's SSH key can push and the agent can use it, the agent can push over SSH.

So the key a person pushes with must need a fresh action each time it's used. Two ways:

- **A passphrase that isn't stored.** Don't load the key into `ssh-agent`, and don't save its passphrase in the macOS Keychain. Remove `AddKeysToAgent yes` and `UseKeychain yes` for this key from `~/.ssh/config`. Run `ssh-add -D` to clear keys already loaded.
- **A hardware security key** that needs a touch at each use. Create the key with `ssh-keygen -t ed25519-sk`. On macOS, the bundled OpenSSH can't talk to a security key; see [signed-approvals.md, part 1](signed-approvals.md#pick-a-key) for the Homebrew workaround.

GitHub can't require the touch on its side. Touch is a property of the key, so an organisation can't check from GitHub that its members use one. On GitHub Free or Team, an organisation also can't restrict which personal SSH keys its members use (see [SSH certificate authorities](#ssh-certificate-authorities-enterprise-cloud-only) for the Enterprise Cloud option).

Check it. From the agent's session, run:

```bash
ssh-add -l
ssh -T git@github.com < /dev/null
```

`ssh-add -l` must not list the push key. `ssh -T` must fail or ask for a passphrase, not greet the user by name. If the agent's environment sets `SSH_ASKPASS`, a passphrase dialog may appear on the developer's screen instead; cancel it.

### Remove saved HTTPS credentials

git's credential helper gives a saved password or token to any process that asks, with no prompt. Remove saved GitHub entries. The macOS steps are in [signed-approvals.md, part 4](signed-approvals.md#revoke-the-old-token-and-remove-saved-passwords).

### The strongest option: run agents without the person's credentials

Run the agent as a separate operating-system user, or in a container or virtual machine, that holds no human credentials at all: no SSH key, no Keychain, no `gh` login, no browser session. It can still commit to a repository on disk. The person pushes from their own account. This removes the problem the rest of this layer works around, and it's the setup to aim for where agents run unattended.

## Layer 2: GitHub organisation settings

These are enforced on GitHub's side, so they hold whatever runs on the developer's machine. Each needs an organisation owner.

### Personal access token policy

Go to the organisation's **Settings** > **Personal access tokens** > **Settings**. There are two tabs, **Fine-grained tokens** and **Tokens (classic)**. ([GitHub's guide](https://docs.github.com/en/organizations/managing-programmatic-access-to-your-organization/setting-a-personal-access-token-policy-for-your-organization).)

1. On **Tokens (classic)**, under "Restrict personal access tokens (classic) from accessing your organizations", select **Restrict access via personal access tokens**.
2. On **Fine-grained tokens**, under "Require approval of fine-grained personal access tokens", keep **Require administrator approval** selected. It's the default.
3. Under **Set maximum lifetimes for personal access tokens**, set a limit. The default for fine-grained tokens is 366 days.

Blocking classic tokens matters because a classic token with `repo` scope can push to every repository its owner can. Requiring approval lets an owner see each fine-grained token's permissions before it works on the organisation's repositories, and refuse one with Contents: write meant for an agent. A token over the maximum lifetime is blocked from the organisation, not revoked.

### Rulesets on the default branch

A **ruleset** is a set of rules GitHub applies to matching branches or tags, such as "changes must come through a pull request". An organisation ruleset applies across many repositories at once. The **bypass list** names the people, teams and apps the ruleset doesn't apply to.

Go to the organisation's **Settings** > **Code, planning, and automation** > **Repository** > **Rulesets** > **New ruleset**. Target **All repositories**, or pick repositories by name or by property. Target the default branch, and turn on:

- **Restrict deletions**
- **Block force pushes**
- **Require a pull request before merging**, with:
  - **Required approvals** set to 1 or more
  - **Dismiss stale pull request approvals when new commits are pushed**
  - **Require approval of the most recent reviewable push**
  - **Require review from Code Owners**, if your repositories have a `CODEOWNERS` file
- **Require status checks to pass before merging**, with each check's source pinned to the GitHub Actions app

**Require approval of the most recent reviewable push** means the last push must be approved by someone other than the person who pushed it. If an agent pushes with a developer's credentials, that developer's own approval doesn't count for that push. Pinning a check's source stops any other token from posting a passing result under the same name; [signed-approvals.md, part 2](signed-approvals.md#2-make-floor-guard-a-required-check) explains this for one check.

Two more rules to consider:

- **Restrict updates** lets only the bypass list update matching branches. Use it for branches nobody should push to directly, such as release branches.
- **Require signed commits** refuses unsigned commits on matching branches. Agents' commits are unsigned if the signing key needs a person (see signed-approvals.md), so with this rule on, the person pushing must sign each commit first, for example by rebasing with `-S`. Turn it on only if your process does that.

Keep the bypass list empty. If someone must bypass, set their entry to **For pull requests only**, so that they still go through a pull request.

Organisation rulesets need GitHub Team or Enterprise; Team has had them since 2025-06-16. On GitHub Free, check [GitHub's plans page](https://github.com/pricing) for whether rulesets work on private repositories; we haven't confirmed it. Without them, set rulesets repository by repository.

### SSH certificate authorities (Enterprise Cloud only)

An **SSH certificate authority** is a key the organisation uses to sign its members' SSH keys, so GitHub can accept only keys the organisation has signed. It's the only way to control which SSH keys members use, and it needs GitHub Enterprise Cloud.

Go to the organisation's **Settings** > **Authentication security** > **SSH Certificate Authorities**. **Require SSH Certificates** blocks members' unsigned SSH keys and HTTPS access to the organisation's repositories. GitHub Apps, deploy keys, GitHub Actions and Codespaces are exempt.

On GitHub Free or Team, an organisation can't restrict which personal SSH keys members use. Layer 1 is the only control over SSH pushes on those plans.

### Deploy keys

A **deploy key** is an SSH key attached to one repository rather than to a person. Deploy keys are read-only unless someone ticks write access when adding one. To stop anyone adding them, go to the organisation's **Settings** > **Member privileges** > **Deploy keys** and select **Disabled**. Disabling removes access from deploy keys that already exist, so check what uses them first.

If an agent needs its own identity to read repositories, a GitHub App with Contents: read is the alternative to a read-only deploy key.

### Audit

GitHub records `git.push` events in the audit log only on Enterprise Cloud. They're available through the REST API, audit log streaming or export, not the web view, and GitHub keeps them for 7 days. To find what a particular token did, search by `hashed_token`. On GitHub Free or Team there's no audit record of pushes.

## Layer 3: agent configuration

This is a guardrail, not a boundary. Each control below catches the ordinary case. Each says what it can't stop.

### Claude Code: managed settings

**Managed settings** are Claude Code settings an administrator deploys to a machine. Nothing in a user's settings, a project's settings or the `--settings` flag overrides a key set there. ([Claude Code's guide](https://code.claude.com/docs/en/managed-settings.md).)

Deploy them in one of these ways:

| Where | Path or mechanism |
|---|---|
| macOS file | `/Library/Application Support/ClaudeCode/managed-settings.json` |
| Linux and WSL file | `/etc/claude-code/managed-settings.json` |
| Windows file | `C:\Program Files\ClaudeCode\managed-settings.json` |
| Drop-in folder | `managed-settings.d/` beside the file |
| macOS configuration profile | domain `com.anthropic.claudecode` |
| Windows policy | HKLM registry policy |
| Server-managed | Claude's admin console |

A developer with local admin rights can edit the file. A managed policy is only as strong as the machine's admin controls.

A starting `managed-settings.json`:

```json
{
  "permissions": {
    "deny": [
      "Bash(git push *)",
      "Bash(gh pr create *)",
      "Bash(gh pr merge *)",
      "Bash(gh api *)",
      "Bash(gh auth *)"
    ],
    "disableBypassPermissionsMode": "disable",
    "disableAutoMode": "disable"
  },
  "allowManagedPermissionRulesOnly": true,
  "allowManagedHooksOnly": true,
  "sandbox": {
    "enabled": true,
    "allowUnsandboxedCommands": false,
    "failIfUnavailable": true,
    "network": {
      "allowedDomains": [],
      "allowManagedDomainsOnly": true
    }
  }
}
```

What each part does ([permissions](https://code.claude.com/docs/en/permissions.md), [settings reference](https://code.claude.com/docs/en/settings-reference.md)):

- **`deny`** refuses Bash commands that match. `Bash(git push:*)` means the same as `Bash(git push *)`.
- **`disableBypassPermissionsMode`** rejects `--dangerously-skip-permissions`.
- **`disableAutoMode`** removes auto mode, in which Claude Code decides on permissions itself.
- **`allowManagedPermissionRulesOnly`** ignores allow, ask and deny rules from user, project and local settings, so nobody can add an allow rule that outranks yours.
- **`allowManagedHooksOnly`** runs only the hooks managed settings define.
- **`sandbox`** is described below.

**What deny rules can't stop.** Claude Code's docs say a Bash rule "isn't a security boundary around the program". It matches the command's text. None of these is matched by `Bash(git push *)`:

```bash
git -C . push origin main
git -c push.default=current push origin main
git 'push' origin main
sh -c 'git push origin main'
/usr/bin/git push origin main
```

The `gh` rules follow the same syntax as the `git` rule, but Claude Code's docs have no example of them. Test each one before relying on it: start a session under the managed settings and ask the agent to run the command.

### Claude Code: hooks

A **hook** is a program Claude Code runs at a set point, such as before each tool call. A `PreToolUse` hook can inspect a Bash command and block it, which lets you match more forms of `git push` than a deny rule can. To block, the hook exits with code 2 (its stderr becomes the reason) or prints:

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "Agents don't push. A person reviews and pushes."}}
```

([Claude Code's hooks guide](https://code.claude.com/docs/en/hooks.md).)

**What hooks can't stop.** A hook can't override a deny rule; it can add refusals, not remove them. Claude Code's docs don't call hooks a security boundary. A hook still reads the command as text, so a script the agent writes and then runs can push without the hook seeing `git push`. A hook that fails to start, crashes or times out usually lets the action go ahead. Set `"onFailure": "block"` on the hook so that it fails closed; this needs Claude Code v2.1.295 or later.

### Claude Code: the sandbox

The **sandbox** is an operating-system-enforced boundary around the commands Claude Code's Bash tool runs. It's the strongest agent-side control, because the operating system enforces it rather than Claude Code reading the command. ([Claude Code's sandboxing guide](https://code.claude.com/docs/en/sandboxing.md).)

- It's off by default. `"sandbox": {"enabled": true}` turns it on.
- Network traffic from sandboxed commands goes through a proxy that allows only the domains in `sandbox.network.allowedDomains`, which is empty by default. `allowManagedDomainsOnly` in managed settings stops users adding domains.
- Set `sandbox.allowUnsandboxedCommands` to `false`, so a blocked command can't be retried outside the sandbox.
- Set `sandbox.failIfUnavailable` to `true`, so Claude Code refuses to start rather than running unsandboxed.
- Don't list `git` or `gh` in `sandbox.excludedCommands`. Commands listed there run outside the sandbox.

Leaving `github.com` out of `allowedDomains` blocks pushes and reads alike: the agent can't fetch or clone either. That's usually acceptable when a person pushes and pulls.

**What the sandbox can't stop, and where it's weaker.**

- It covers shell commands only, not Claude Code's built-in file or web tools.
- On macOS, git over SSH fails inside the sandbox even to allowed hosts, and `gh` may fail TLS under macOS's sandbox (Seatbelt).
- On native Windows, commands run unsandboxed.
- On Linux, it needs `bubblewrap` and `socat` installed.

### Kilo: permissions in `kilo.jsonc`

Kilo reads permissions from the project's `kilo.jsonc`:

```jsonc
{
  "permission": {
    "bash": {
      "*": "allow",
      "git push *": "deny",
      "gh *": "deny"
    }
  }
}
```

- **The last matching rule wins.** Put the denies after `"*"`. Otherwise `"*": "allow"` matches last and the command runs.
- **A pattern object without `"*"`** leaves any command it doesn't name to Kilo's defaults.

These were observed with Kilo 7.8.18 on 2026-11-06. Check Kilo's documentation for the version you run.

**What Kilo's rules can't stop.** They match the command's text, like Claude Code's deny rules, so the same rewritten forms of `git push` (`git -C . push`, `sh -c '...'`, a full path) are likely to get past them. We haven't tested each form against Kilo. `kilo.jsonc` lives in the project, so a developer, or an agent that can edit files, can change it. Check Kilo's documentation for any setting an organisation can lock.

## Layer 4: process

How people work is what makes a review mean something.

1. Let agents commit only on branches, never on the default branch.
2. Review the diff in a terminal no agent drives: `git log -p origin/main..<sha>`.
3. Push the commit you reviewed by its SHA: `git push origin <sha>:refs/heads/<branch>`.
4. Open the pull request yourself.

Pushing by SHA matters because a branch name points at whatever was committed last. If you push `<branch>`, an agent that committed again after your review gets that commit pushed too. Pushing `<sha>` sends the commit you read and its parents, nothing newer.

With Layer 1 in place, step 3 asks for your passphrase or a touch. That prompt is the point: if a push ever happens without one, a credential is reachable that shouldn't be.

## An exception: agents in a cloud session

Everything above assumes the agent runs on a developer's machine, with that developer's credentials. A **cloud session** is different: the agent runs in a container the agent platform provides, such as a Claude Code session on the web, and the platform gives it a GitHub credential for the repositories attached to the session. That credential is how the agent clones, and it can push.

Layers 1 and 3 don't reach it. There's no developer key to lock away, and the platform, not your managed settings, decides what the container's agent may run. So a cloud agent can push, and you should plan on it doing so.

The boundary that's left is Layer 2. With a ruleset on the default branch, a cloud agent can push only to its own branches, and nothing reaches the default branch without a pull request that a person approves and merges. Layer 4 still applies to that person: review the pull request's diff, not the agent's summary of it.

If your organisation allows cloud sessions, accept their pushes to their own branches as an exception, write the exception down, and check that:

- the default branch's ruleset blocks direct pushes, force pushes and deletions, and requires a pull request with an approval
- no bypass list includes the platform's GitHub App or the account its credential acts as
- a person, not an agent, merges every pull request

### How this repository applies it

Whitney's own repository allows the exception, decided on 2026-11-08. Cloud sessions may push the branches they work on. `main` requires a pull request, signed commits and no direct pushes, and the maintainer reviews and merges every pull request. Changes that need an approver's signature, as [signed-approvals.md](signed-approvals.md) describes, are still made on the maintainer's own machine.

## Evidence that the agent configuration holds

Whitney runs the real agent CLI against a template and records what the agent did. It can show whether a permission rule held when an agent tried a blocked command.

A Kilo `bash` pattern rule that denies `git push *` leaves a refusal in the agent's transcript, which Whitney can record (a capture recorded on 2026-11-06 with Kilo 7.8.18). A scalar `"bash": "deny"` behaves differently: it removes the Bash tool, so there is no attempt and no refusal to see, and a run can't tell "held" from "never tried".

The `permission_held` check tests pattern rules such as the one above: on Kilo, a rule in `kilo.jsonc` ([results](results/2026-11-10-permissions.md)); on Claude Code, a `deny` or `ask` rule such as `Bash(git push:*)` in the project's `.claude/settings.json` ([results](results/2026-11-12-claude-code-permissions.md)). Both runs show the rule held in the template and was bypassed in a control. `git -C . push` got past both rules when tried (Kilo: `tasks/plan-phase5.md`, Later; Claude Code: [spike findings](spike-findings.md#permissions)), so a pattern rule is not a control on its own.

## Checklist

"Not checked" in the last column means we didn't confirm which GitHub plans offer the setting. Check GitHub's documentation for your plan.

| Control | What it stops | What it doesn't | Who sets it | Plan needed |
|---|---|---|---|---|
| Read-only fine-grained token for `gh` | `gh` and HTTPS pushes, pull requests and merges | SSH pushes | Developer (owner approves) | Any |
| Push key needs a passphrase or touch each use | Agent pushes over SSH with the person's key | Anything the person approves while the agent is working | Developer | Any |
| Saved HTTPS credentials removed | Agent pushes through git's credential helper | SSH pushes, `gh`'s token | Developer | Any |
| Agents as a separate OS user, container or VM | Use of any human credential | Nothing on this list, if it holds no credentials | Platform team | Any |
| Classic tokens restricted | Broad classic tokens reaching org repositories | Fine-grained tokens, SSH | Org owner | Not checked |
| Fine-grained tokens need approval, with a maximum lifetime | Unreviewed or long-lived write tokens | SSH, tokens already approved | Org owner | Not checked |
| Ruleset on the default branch | Direct pushes, force pushes, deletions, unreviewed merges | Pushes to other branches, opening pull requests | Org owner | Team or Enterprise (Free: unconfirmed for private repositories) |
| Require approval of the most recent reviewable push | The pusher approving their own push | A second person approving without reading | Org owner | Team or Enterprise |
| SSH certificate authority, certificates required | Unsigned personal SSH keys and HTTPS | GitHub Apps, deploy keys, Actions, Codespaces | Org owner | Enterprise Cloud |
| Deploy keys disabled | Write-enabled repository keys | Personal SSH keys | Org owner | Not checked |
| `git.push` audit events | Nothing; shows who pushed, for 7 days | Pushes on Free or Team | Org owner | Enterprise Cloud |
| Claude Code managed deny rules | Plain `git push` and named `gh` commands | Rewritten commands, scripts, an admin editing the file | Platform team | Any |
| Claude Code `PreToolUse` hook | More forms of `git push` than a deny rule | Scripts the agent writes; failures, unless `onFailure: "block"` | Platform team | Any |
| Claude Code sandbox, no GitHub domains | Network access to GitHub from shell commands | Built-in tools; native Windows | Platform team | Any |
| Kilo `bash` pattern denies | Plain `git push` and `gh` commands | Rewritten commands, edits to `kilo.jsonc` | Project or platform team | Any |
| Cloud sessions allowed to push their own branches, with the default branch protected | Cloud agents changing the default branch without a reviewed pull request | Cloud agents pushing other branches, which is the exception | Org owner | Team or Enterprise (Free: unconfirmed for private repositories) |
| Review and push by SHA | Pushing commits nobody reviewed | A reviewer missing something in the diff | Every person who pushes | Any |
