# Signed approvals

[CONSTRAINTS.md](../CONSTRAINTS.md) records human decisions as commit-message trailers, such as `Exception-approved: E3`. Anyone who can commit can write a trailer, agents included. So on a repository where the person approving is also the pull request's author, an approval counts only when the commit carrying it is signed by an approver. The `floor-guard` check (`.github/workflows/floor-guard.yml`) enforces that on every pull request into `main`.

This guide sets that up so that an agent can't produce an approval, even one running as your own operating-system user. It takes about 45 minutes and has four parts:

1. A signing key that only works when you're there
2. `floor-guard` as a required check
3. A throwaway pull request that proves the check blocks what it should
4. A GitHub token for agents without admin rights

With more than one maintainer, a required review from someone other than the author also makes an approval a human's (CONSTRAINTS.md). Signatures are for when that isn't available.

## Why each step is yours to do

An agent on your machine runs as your user. It can read the files you can read, use your `ssh-agent`, use your unlocked login Keychain, and use any app approval your password manager remembers. **Anything that signs or authenticates without a fresh action from you, it can do too.** That rules out:

- a key with no passphrase
- a key loaded into `ssh-agent`, or whose passphrase is saved in the macOS Keychain
- a password-manager SSH agent that remembers approvals per app. 1Password can remember an approval for an app until it locks, so approving from VS Code's terminal would also let an agent running in VS Code sign.
- an admin-capable GitHub token saved anywhere your user can read it

So you create keys and tokens, change GitHub settings, sign commits and write trailers yourself, in a terminal or browser that no agent drives. An agent can read state and run the checks below.

The command blocks below contain no `#` comments. Interactive zsh tries to run a comment line as a command unless the `interactivecomments` option is set. They use `$REPO` for your fork:

```bash
REPO=your-org/your-fork
```

## 1. A signing key that only works with you

### Pick a key

**A FIDO2 hardware key** (for example a YubiKey 5 with a PIN set) is the strongest choice: each signature needs a touch.

- **macOS's bundled OpenSSH can't talk to a security key.** It isn't linked against libfido2; we checked on macOS 27 with OpenSSH 10.5p1. Install Homebrew's `openssh` and then unlink it. Homebrew's `ssh` rejects the `UseKeychain` option many macOS SSH configs use, so if it replaced `/usr/bin/ssh` on your `PATH`, your git pushes would break.
- **GitHub checks the touch, not the PIN.** GitHub verifies signatures with the [`ssh_data`](https://github.com/github/ssh_data) library, which by default requires the user-presence flag but not user verification. Your own `ssh-keygen` asks for the PIN. Treat the touch as the real guard: only touch the key for a commit you started.
- **Not yet verified here:** Whitney's own setup used a passphrase key, not this one.

```bash
brew install openssh
brew unlink openssh
which ssh
/opt/homebrew/opt/openssh/bin/ssh-keygen -t ed25519-sk -O verify-required -C "git signing" -f ~/.ssh/git_signing_sk
git config --global gpg.ssh.program /opt/homebrew/opt/openssh/bin/ssh-keygen
```

`which ssh` must still print `/usr/bin/ssh`. With this key, use `~/.ssh/git_signing_sk.pub` wherever the steps below say `git_signing_ed25519.pub`.

**A passphrase key that is never loaded into an agent.** This is what Whitney uses.

- Git signs by running `ssh-keygen -Y sign`. That uses the key from `ssh-agent` if it's loaded there, and otherwise reads the private key and asks for the passphrase. It ignores `AddKeysToAgent` and `UseKeychain`, so your SSH config won't cache the key as long as you never `ssh-add` it, never list it as an `IdentityFile`, and never use it with `ssh -i`.
- Anything running as you can read the encrypted key file and try passphrases offline. So use a long passphrase, and `-a 100` to make each guess slower.
- **Don't reuse your SSH login key**, even though GitHub lets you upload it again as a signing key. If your SSH config has `AddKeysToAgent yes` or `UseKeychain yes`, that key sits in the agent, and an agent could sign with it.

```bash
ssh-keygen -t ed25519 -a 100 -C "git signing" -f ~/.ssh/git_signing_ed25519
ssh-add -l
ssh-keygen -lf ~/.ssh/git_signing_ed25519.pub
```

The second fingerprint must not appear in the `ssh-add -l` list.

**1Password's SSH agent** works only if you sign from a terminal app no agent runs in, and lock 1Password afterwards, for the reason above.

### Add it to GitHub and check your email

1. GitHub → **Settings** → **SSH and GPG keys** → **New SSH key**. Set **Key type** to **Signing Key** and paste the `.pub` file. Do this in the web UI: doing it with `gh` would need the `admin:ssh_signing_key` scope, which agents shouldn't have.
2. GitHub → **Settings** → **Emails**: your git `user.email` must be listed and **verified**. GitHub matches a signature to the account that owns the committer's email.

### Configure git

```bash
git config --global gpg.format ssh
git config --global user.signingkey "$HOME/.ssh/git_signing_ed25519.pub"
echo "$(git config user.email) namespaces=\"git\" $(cut -d' ' -f1,2 ~/.ssh/git_signing_ed25519.pub)" >> ~/.ssh/allowed_signers
git config --global gpg.ssh.allowedSignersFile "$HOME/.ssh/allowed_signers"
```

- **Global rather than per-repo.** Neither is a security boundary, since anything running as you can edit both; the passphrase or touch is the boundary. Global config also applies in the scratch repo used by the test below.
- **Leave `commit.gpgsign` unset.** Sign approvals explicitly with `-S`. If signing were on by default, every commit an agent made would fail or prompt for your passphrase.
- **`allowed_signers` only drives the local check.** It's what lets `git log --show-signature` report a good signature. Anything running as you can add a line to it, so a local "Good signature" is not evidence. GitHub's verification is what `floor-guard` checks.
- **Sign from a terminal app no agent runs in.** IDE commit buttons have no terminal to ask for the passphrase.

### Prove only you can sign

First, ask your agent to try to sign in a scratch repo:

```bash
git init -q /tmp/sign-test && cd /tmp/sign-test && git commit --allow-empty -S -m test < /dev/null
```

It must fail, with `incorrect passphrase supplied to decrypt private key` and `fatal: failed to write commit object`. That one failure shows the private key is encrypted and isn't in `ssh-agent`, because `ssh-keygen` would otherwise have used the agent. Check first that the agent's environment has no `SSH_ASKPASS` set. If it does, a passphrase dialog could pop up on your screen instead; cancel it.

Then make two signed commits yourself, on a throwaway branch in a separate worktree, and push:

```bash
git fetch origin
git worktree add -b signing-test ../signing-test origin/main
cd ../signing-test
git commit --allow-empty -S -m "Signing test (throwaway)"
git commit --allow-empty -S -m "Signing test 2 (throwaway)"
git push -u origin signing-test
```

Both commits must ask for your passphrase. If the second doesn't, something is caching it. Check that GitHub shows both commits as `verified: true` with reason `valid`, then delete the branch and the worktree:

```bash
gh api "repos/$REPO/commits/signing-test" --jq .commit.verification
gh api "repos/$REPO/commits/signing-test~1" --jq .commit.verification
```

## 2. Make `floor-guard` a required check

The check only blocks a merge once the branch ruleset requires it.

1. GitHub → **Settings** → **Rules** → **Rulesets** → the ruleset for your default branch → **Require status checks to pass** → **Add checks** → `floor-guard` → **Save changes**.
2. **Pin the source to GitHub Actions.** With "Any source", any token that can write commit statuses can post a passing status called `floor-guard`; a classic token with `repo` scope can. With an app selected, GitHub won't allow the merge if the status comes from any other person or integration.
3. **Approvers.** The workflow reads approvers from the repository variable `APPROVERS` (comma-separated logins) and falls back to the repository's owner. In an organisation the owner is the organisation, which never commits, so set `APPROVERS`: **Settings** → **Secrets and variables** → **Actions** → **Variables**. Every approver needs their own key set up as in part 1.

**The source picker is empty at first.** It only offers apps that have recently reported a check, and `floor-guard` doesn't run until a pull request is opened after the workflow is on `main`: `pull_request_target` runs the base branch's copy of the workflow, so the pull request that adds the workflow doesn't run it. Add the check with "Any source" now, and switch it to GitHub Actions after the first run in part 3.

Check the result:

```bash
gh api "repos/$REPO/rulesets" --jq '.[] | {id, name}'
gh api "repos/$REPO/rulesets/<id>" --jq '{enforcement, bypass_actors, checks: (.rules[] | select(.type=="required_status_checks") | .parameters)}'
```

When you're done, you should see `"enforcement": "active"`, an empty `bypass_actors`, and `{"context": "floor-guard", "integration_id": 15368}`; 15368 is GitHub Actions.

"Require branches to be up to date" is worth keeping on: each merge is then checked against the current `main`. The cost is that when `main` moves, a pull request needs updating (and `floor-guard` runs again) before it can merge.

## 3. Prove it with a throwaway pull request

Open a pull request with a one-line change, such as a comment at the end of `README.md`. Then push the commits below in order, waiting for `floor-guard` after each one. An agent can make the commits marked "agent"; the ones marked "you" need your hand.

| # | Commit | By | Expected `floor-guard` | Expected merge state |
|---|---|---|---|---|
| 1 | Ordinary unsigned change, no trailer | agent | pass, and listed as required | `CLEAN` |
| 2 | `git commit --allow-empty -m "Test an unsigned approval" --trailer "Exception-approved: E1"`, unsigned | you | fail: "carries an Exception-approved trailer, but GitHub doesn't show it as verified (unsigned)" | `BLOCKED` |
| 3 | `git commit --amend -S --no-edit`, then `git push --force-with-lease` | you | pass: "1 commit(s) signed by an approver" | `CLEAN` |
| 4 | Unsigned edit to a comment in `scripts/floor_guard.py` | agent | fail: "changes scripts/floor_guard.py, but GitHub doesn't show it as verified (unsigned)" | `BLOCKED` |
| 5 | Adds a `pull_request` workflow with a job also named `floor-guard` that sleeps 45 seconds, then passes | agent | the real check fails and the fake one passes; both count as required | `BLOCKED` |

After the first run, switch the check's source to GitHub Actions (part 2) and confirm the pull request is still `CLEAN`.

The workflow for row 5, saved as `.github/workflows/fake-floor-guard.yml`:

```yaml
name: Fake floor guard
on:
  pull_request:
    branches: [main]
permissions: {}
jobs:
  floor-guard:
    name: floor-guard
    runs-on: ubuntu-latest
    timeout-minutes: 3
    steps:
      - run: sleep 45 && echo "fake floor-guard passing"
```

Read the results like this:

```bash
gh pr checks <n> --required
gh pr view <n> --json mergeStateStatus --jq .mergeStateStatus
gh api "repos/$REPO/compare/main...<branch>" --jq .behind_by
gh api graphql -f query='query{repository(owner:"OWNER",name:"REPO"){pullRequest(number:N){commits(last:1){nodes{commit{statusCheckRollup{contexts(first:10){nodes{... on CheckRun{name conclusion isRequired(pullRequestNumber:N) checkSuite{app{databaseId} workflowRun{event}}}}}}}}}}}}'
```

- **`mergeStateStatus`** is the signal. `mergeable: MERGEABLE` only means there are no conflicts.
- **`behind_by` must be 0**, so that a `BLOCKED` comes from the check and not from the branch being out of date.
- **`gh pr checks` exits 1 whenever a check has failed.** That's not a permissions error.

Row 5 is the attack an agent with push access could try. It matters because both jobs come from the same app, GitHub Actions, so pinning the source doesn't tell them apart. In Whitney's run, GitHub counted both as required and kept the pull request blocked, even though the fake job finished last. GitHub's docs don't describe this case, so run row 5 again after a significant change on GitHub's side.

Close the pull request without merging, and delete its branch. Whitney's own run, on 3 November 2026, is [PR #6](https://github.com/chrischant3/Whitney/pull/6); its closing comment lists the commits and results.

## 4. Give agents a token without admin rights

Anyone with admin rights on the repository can edit the ruleset, or change `APPROVERS`, and the check is then gone. A classic or OAuth token with `repo` scope has admin rights on every repository you own, and the login `gh auth login` makes in the browser is one of those.

Because agents run as you, adding a weaker second token doesn't help while the admin one is still where they can reach it. Take the admin credential off the machine, and do admin work in your own browser.

### Create a fine-grained token

GitHub → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**. Set an expiry, such as 90 days, and choose the repositories. Your SSH login key can already push to every repository you own, so limiting the list mainly limits `gh`.

| Repository permission | Access |
|---|---|
| Metadata | Read (required) |
| Contents | Read and write |
| Pull requests | Read and write |
| Issues | Read and write |
| Actions | Read |
| Commit statuses | Read |
| Administration, Variables, Secrets, Workflows, Webhooks, Environments | No access |

- **Account permissions:** none, apart from Gists if your agents use them. In particular, give no access to SSH keys, signing keys or emails.
- **Workflows:** no access only stops workflow changes made with this token. Pushes over SSH still use your login key. Part 3, rows 4 and 5 show `floor-guard` blocking unsigned changes to guarded files, including a new workflow.

GitHub shows the token once. Don't save it to a file; paste it straight into the next step.

### Replace `gh`'s login

With the token on your clipboard (macOS):

```bash
gh auth logout --hostname github.com --user <your-login>
pbpaste | gh auth login --hostname github.com --git-protocol ssh --with-token
pbcopy < /dev/null
gh auth status
```

On Linux, run `gh auth login --hostname github.com --git-protocol ssh --with-token`, paste the token, press Enter and then Ctrl-D, and clear the terminal afterwards.

`gh auth status` should show no "Token scopes" line, since fine-grained tokens don't have scopes.

### Revoke the old token and remove saved passwords

1. **Revoke the old token.** GitHub → **Settings** → **Applications** → **Authorized OAuth Apps** → **GitHub CLI** → **Revoke**. Logging out only deleted the local copy, and GitHub still accepted the token until now. Revoking signs `gh` out on every machine that used its browser login. While you're there, look for any other app that keeps a `repo` token on this machine: it's the same kind of gap.
2. **Remove saved HTTPS passwords.** git's credential helper hands a saved password to anything that asks, with no prompt (`git credential fill`). On macOS:

   ```bash
   security find-internet-password -s github.com
   ```

   That prints the entry's details but not the password. If it finds an entry, run `printf 'protocol=https\nhost=github.com\n\n' | git credential-osxkeychain erase` and check again until it reports "could not be found".

### Check the token

An agent can run these. None of them prints the token:

```bash
gh auth token | grep -q '^github_pat_' && echo fine-grained
gh api -i "repos/$REPO/actions/permissions" | grep -i -E '^HTTP|^x-accepted-github-permissions'
```

| Request | Expected |
|---|---|
| `repos/$REPO`, `repos/$REPO/pulls`, `gh run view <id> --log` | 200 / works |
| `repos/$REPO/actions/permissions`, `repos/$REPO/keys` | 403, needs `administration=read` |
| `repos/$REPO/actions/variables` | 403, needs `actions_variables=read` |
| `user/keys`, `user/ssh_signing_keys`, `user/emails` | 403 |

A token refused even read access to Administration can't change the ruleset.

### Keep it that way

- **Do admin work in your own browser.** Don't sign in to GitHub in a browser profile an agent can drive.
- **Don't run `gh auth login` in the browser again.** It would bring back an admin-capable token. When the fine-grained token expires, `gh` fails with 401 errors; generate a new one with the same settings.

## What this doesn't cover

- **Agents can still push to every repository you own, using your SSH login key.** That includes branches with workflow changes, and they can open pull requests. What they can't do is get an unsigned approval or an unsigned change to the guard merged into `main`, or change the rules that stop them.
- **Running agents as a separate OS user, or in a container or VM, is the stronger fix.** It keeps them away from your SSH key, Keychain and browser altogether. It isn't covered here.
- **Some routes are untested here.** The FIDO2 key and 1Password routes haven't been tried on this repository, and the same-name check result (part 3, row 5) comes from a single run.

To stop agents pushing at all, across an organisation, see [agent-push-controls.md](agent-push-controls.md).
