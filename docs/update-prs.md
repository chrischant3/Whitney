# Keeping pull requests up to date

The `Update pull requests` workflow (`.github/workflows/update-prs.yml`) runs after each push to `main`. For every open pull request into `main` that has auto-merge on, isn't a draft, and is behind `main`, it does what the **Update branch** button does: GitHub merges `main` into the pull request's branch, and the pull request's checks run again. It never rebases, so signed commits stay signed.

Pull requests without auto-merge, drafts, and pull requests from a fork that doesn't allow edits by maintainers are left alone.

## Set it up

Each step needs the repository owner; an agent's token can't change settings or create secrets.

1. **Allow auto-merge.** On the repository, open Settings → General → Pull Requests and tick **Allow auto-merge**.
2. **Create the `pr-update` environment, for `main` only.** Settings → Environments → **New environment**, name it `pr-update`. Under **Deployment branches and tags**, choose **Selected branches and tags** and add `main`. This is what stops a workflow pushed on any other branch from reading the token.
3. **Create a fine-grained token** at <https://github.com/settings/personal-access-tokens/new>:
   - Repository access: **Only select repositories** → this repository.
   - Permissions → Repository: **Contents**, **Pull requests** and **Workflows**, each **Read and write**. Workflows is needed because merging `main` into a branch brings in any workflow files `main` changed.
   - Pick an expiry you'll notice, and put a reminder in your calendar.
4. **Save it in the environment.** In the `pr-update` environment from step 2, under **Environment secrets**, **Add environment secret**. Name: `PR_UPDATE_TOKEN`. Value: the token. Don't save it as a repository secret: any branch's workflow can read those.
5. **Optional, recommended: require pull requests to be up to date.** In the rule for `main` (Settings → Branches or Rules), tick **Require branches to be up to date before merging**. Without it, auto-merge merges a pull request as soon as its own checks pass, even if `main` has moved on, and two pull requests that each pass alone can break `main` together.

The token has to be a person's: GitHub doesn't start the checks for a push made with a workflow's own token (it holds them for approval), so an update made with it would leave the pull request waiting. Updates made with your token show as made by you.

**What the token can do.** It acts as you, within the permissions in step 3. If you can push to `main` past its rules (as an admin, or as a ruleset bypass actor), so can anything holding this token. Keep it in the `pr-update` environment only. If you have a machine account with write access to this repository and nothing else, its token is the safer choice.

## Use it

On a pull request you're happy to merge, press **Enable auto-merge**. From then on, each time `main` moves, the workflow updates that pull request, its checks run, and once they pass GitHub merges it.

To check the setup, open Actions → **Update pull requests** → **Run workflow** on `main`. The run's summary has a line per pull request, such as `#76: update requested (2 commit(s) behind main)`, or `No open pull request into main has auto-merge on and isn't a draft.`

## When it goes wrong

| The run says | What it means | What to do |
|---|---|---|
| `PR_UPDATE_TOKEN isn't set in the pr-update environment`, and the run passes | The secret is missing, or saved somewhere else | Do steps 2 and 4 |
| `Couldn't list the pull requests into main: ... (HTTP 401)`, and the run fails | The token has expired or been revoked | Make a new token as in step 3 and replace the secret |
| `#N: couldn't be updated: ... (HTTP 403)` or `(HTTP 404)`, and the run fails | The token lacks a permission, or isn't for this repository | Check step 3's permissions, then replace the secret |
| `#N: couldn't be updated: ... (HTTP 422)`, and the run passes | GitHub couldn't merge `main` into that branch, usually a conflict, or someone pushed to it since the run started | Merge `main` into the branch yourself and resolve the conflict; the next push to `main` picks it up again |
| `#N: skipped: its branch is in a fork that doesn't allow edits by maintainers` | GitHub won't let anyone but the fork's owner change that branch | Ask its author to tick **Allow edits by maintainers**, or to update it themselves |

Only the first 100 open pull requests are looked at.
