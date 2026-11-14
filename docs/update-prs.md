# Keeping pull requests up to date

The `Update pull requests` workflow (`.github/workflows/update-prs.yml`) runs after each push to `main`. For every open pull request into `main` that has auto-merge on and is behind `main`, it does what the **Update branch** button does: GitHub merges `main` into the pull request's branch, and the pull request's checks run again. It never rebases, so signed commits stay signed.

Pull requests without auto-merge, and drafts, are left alone.

## Set it up

Each step needs the repository owner; an agent's token can't create secrets or change settings.

1. **Allow auto-merge.** On the repository, open Settings → General → Pull Requests and tick **Allow auto-merge**.
2. **Create a fine-grained token** at <https://github.com/settings/personal-access-tokens/new>:
   - Repository access: **Only select repositories** → this repository.
   - Permissions → Repository: **Contents**, **Pull requests** and **Workflows**, each **Read and write**. Workflows is needed because merging `main` into a branch brings in any workflow files `main` changed.
   - Pick an expiry you'll notice, and put a reminder in your calendar.
3. **Save it as a secret.** Settings → Secrets and variables → Actions → **New repository secret**. Name: `PR_UPDATE_TOKEN`. Value: the token.
4. **Optional, recommended: require pull requests to be up to date.** In the rule for `main` (Settings → Branches or Rules), tick **Require branches to be up to date before merging**. Without it, auto-merge merges a pull request as soon as its own checks pass, even if `main` has moved on, and two pull requests that each pass alone can break `main` together.

The token has to be a person's: GitHub starts no workflow runs for a push made with a workflow's own token, so an update made with it would leave the pull request's checks waiting. Updates made with your token show as made by you.

## Use it

On a pull request you're happy to merge, press **Enable auto-merge**. From then on, each time `main` moves, the workflow updates that pull request, its checks run, and once they pass GitHub merges it.

To check the setup, open Actions → **Update pull requests** → **Run workflow** on `main`. The run's summary has a line per pull request, such as `#76: updated (2 commit(s) behind main)`, or `No open pull request into main has auto-merge on.`

## When it goes wrong

| The run says | What it means | What to do |
|---|---|---|
| `needs --repo and a token in GH_TOKEN` | The `PR_UPDATE_TOKEN` secret is missing | Do step 3 |
| `#N: couldn't be updated: ... (HTTP 401)`, `(HTTP 403)` or `(HTTP 404)`, and the run fails | The token has expired or lacks a permission | Make a new token as in step 2 and replace the secret |
| `#N: couldn't be updated: ... (HTTP 422)`, and the run passes | GitHub couldn't merge `main` into that branch, usually a conflict, or someone pushed to it since the run started | Merge `main` into the branch yourself and resolve the conflict; the next push to `main` picks it up again |

Only the first 100 open pull requests are looked at.
