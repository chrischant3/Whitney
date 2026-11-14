"""Update each open pull request into main that has auto-merge on and is behind main, as "Update branch" does.

Run by .github/workflows/update-prs.yml after each push to main. GitHub merges
main into the pull request's branch (never a rebase, so signed commits stay as
they are). The token is a person's, not the workflow's own: GitHub starts no
workflow runs for a push made with the workflow's token, so an update made with
it would leave the pull request's checks waiting forever.

Exit codes: 0 each pull request was updated, already up to date, or couldn't be
merged (a conflict, which a person resolves); 1 the token was refused for a
pull request; 2 couldn't run (no token, or the pull requests couldn't be listed).
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Callable

API = "https://api.github.com"
# 401, 403 and 404 mean the token can't update the branch, which no later push fixes.
REFUSED = {401, 403, 404}


class ApiError(Exception):
    """A GitHub API request that returned an error status, with GitHub's message."""

    def __init__(self, status: int, message: str):
        super().__init__(f"{message} (HTTP {status})")
        self.status = status
        self.message = message


Api = Callable[..., object]


def update_pull_requests(api: Api, repo: str, base: str) -> tuple[list[str], int]:
    """Ask GitHub to update each auto-merge pull request into `base` that is behind it.

    `api(method, path, body=None)` makes one GitHub REST request and returns its JSON,
    raising ApiError on an error status. Returns a line per pull request and the exit code.
    Only the first 100 open pull requests are read.
    """
    prs = api("GET", f"/repos/{repo}/pulls?state=open&base={base}&per_page=100")
    chosen = [pr for pr in prs if pr["auto_merge"] and not pr["draft"]]
    if not chosen:
        return [f"No open pull request into {base} has auto-merge on."], 0
    lines, code = [], 0
    for pr in chosen:
        number, sha = pr["number"], pr["head"]["sha"]
        behind = api("GET", f"/repos/{repo}/compare/{base}...{sha}")["behind_by"]
        if not behind:
            lines.append(f"#{number}: already up to date with {base}")
            continue
        try:
            # expected_head_sha: if someone pushed since the list was read, GitHub refuses rather than merge over it.
            api("PUT", f"/repos/{repo}/pulls/{number}/update-branch", {"expected_head_sha": sha})
        except ApiError as e:
            lines.append(f"#{number}: couldn't be updated: {e}")
            if e.status in REFUSED:
                code = 1
            continue
        lines.append(f"#{number}: updated ({behind} commit(s) behind {base})")
    return lines, code


def github_api(token: str, base_url: str = API) -> Api:
    """An `api` callable for update_pull_requests that sends each request to GitHub's REST API (`base_url`) with `token`."""

    def api(method: str, path: str, body: dict | None = None) -> object:
        request = urllib.request.Request(
            f"{base_url}{path}",
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            try:
                message = json.load(e).get("message", e.reason)
            except ValueError:
                message = e.reason
            raise ApiError(e.code, message) from None

    return api


def main(argv: list[str] | None = None) -> int:
    """Print a line per pull request and return the exit code (see the module docstring)."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="owner/name on GitHub")
    p.add_argument("--base", default="main", help="branch the pull requests merge into")
    args = p.parse_args(argv)
    token = os.environ.get("GH_TOKEN")
    if not token or not args.repo:
        print(
            "update-prs: needs --repo and a token in GH_TOKEN; the workflow reads it from the PR_UPDATE_TOKEN secret",
            file=sys.stderr,
        )
        return 2
    lines, code = update_pull_requests(github_api(token), args.repo, args.base)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # a crash must read as "couldn't run" (2)
        print(f"update-prs: couldn't run: {e!r}", file=sys.stderr)
        sys.exit(2)
