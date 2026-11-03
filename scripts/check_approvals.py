"""Check that the human approvals in a pull request are signed by an approver.

    python scripts/check_approvals.py --base origin/main --github-repo owner/name --approvers alice,bob
    python -I base/scripts/check_approvals.py --repo pr --base <sha>   # CI: main's copy, the PR's commits

A commit needs an approver's signature if its message has an
`Exception-approved:` trailer, which lets a change lower the floor in
CONSTRAINTS.md, or if it changes the files that enforce that rule (GUARDED).
Anyone who can commit can write a trailer, an agent included; a signature
made with a key only the approver holds is what makes the approval theirs.

"Signed" means GitHub shows the commit as verified and its committer is an
approver: GitHub verifies a signature only against the keys on the
committer's own account. In GitHub Actions the repository comes from
GITHUB_REPOSITORY, the approvers from APPROVERS (default: the repository's
owner) and the token from GITHUB_TOKEN.

Exit codes: 0 every such commit is signed, 1 one isn't, 2 the check couldn't
run (no merge base, git or GitHub unreadable, no repository or approvers).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

APPROVAL = re.compile(r"^Exception-approved:", re.M)
# The files that enforce the rule. A pull request that weakens them would be
# checked by its own weakened copy once merged, so changing them needs a signature too.
GUARDED = (".github/workflows/", "scripts/floor_guard.py", "scripts/check_approvals.py")
API = "https://api.github.com"


def commits_needing_approval(repo: Path, base: str) -> list[tuple[str, str]]:
    """(sha, why) for each commit since the merge base with `base` that needs an approver's signature, oldest first."""
    merge_base = _git(repo, "merge-base", base, "HEAD").strip()
    needed = []
    for sha in _git(repo, "rev-list", "--reverse", f"{merge_base}..HEAD").split():
        message = _git(repo, "log", "-1", "--format=%B", sha)
        # --cc: for a merge, only what the merge itself changed, not what it brought in
        paths = _git(repo, "diff-tree", "--cc", "--no-commit-id", "--name-only", "-r", sha).splitlines()
        guarded = sorted(path for path in paths if path.startswith(GUARDED))
        if APPROVAL.search(message):
            needed.append((sha, "it carries an Exception-approved trailer"))
        elif guarded:
            needed.append((sha, f"it changes {', '.join(guarded)}"))
    return needed


def unapproved(needed: list[tuple[str, str]], lookup: Callable[[str], dict], approvers: set[str]) -> list[str]:
    """A problem for each needed commit that GitHub doesn't show as verified and committed by an approver.

    Args:
        needed: (sha, why) pairs from commits_needing_approval.
        lookup: returns GitHub's REST API view of a commit, given its sha.
        approvers: GitHub logins whose signatures count.
    """
    problems = []
    for sha, why in needed:
        commit = lookup(sha)
        verification = commit["commit"]["verification"]
        login = (commit.get("committer") or {}).get("login")
        if not verification["verified"]:
            problems.append(
                f"{sha[:12]} needs an approver's signature because {why}, "
                f"but GitHub doesn't show it as verified ({verification['reason']})"
            )
        elif login not in approvers:
            problems.append(
                f"{sha[:12]} needs an approver's signature because {why}, "
                f"but it was committed by {login or 'an unknown account'}, who isn't an approver"
            )
    return problems


def github_lookup(github_repo: str, token: str | None) -> Callable[[str], dict]:
    """A lookup reading each commit from GitHub's REST API; the token is optional on a public repository."""

    def lookup(sha: str) -> dict:
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(f"{API}/repos/{github_repo}/commits/{sha}", headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    return lookup


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def main(argv: list[str] | None = None) -> int:
    """Print the result and return the exit code (0 signed, 1 not signed, 2 couldn't run)."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", type=Path, default=Path("."), help="repository whose commits to check")
    p.add_argument("--base", default="origin/main", help="branch the change will merge into")
    p.add_argument("--github-repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="owner/name on GitHub")
    p.add_argument(
        "--approvers",
        default=os.environ.get("APPROVERS") or os.environ.get("GITHUB_REPOSITORY_OWNER", ""),
        help="comma-separated GitHub logins whose signatures count",
    )
    args = p.parse_args(argv)
    approvers = {login.strip() for login in args.approvers.split(",") if login.strip()}
    if not args.github_repo or not approvers:
        print("check-approvals: needs --github-repo and --approvers (or GITHUB_REPOSITORY and APPROVERS)", file=sys.stderr)
        return 2
    needed = commits_needing_approval(args.repo, args.base)
    if not needed:
        print("check-approvals: no commit needs an approver's signature")
        return 0
    problems = unapproved(needed, github_lookup(args.github_repo, os.environ.get("GITHUB_TOKEN")), approvers)
    if not problems:
        print(f"check-approvals: {len(needed)} commit(s) signed by an approver")
        return 0
    print(f"check-approvals: {len(problems)} commit(s) need an approver's signature:", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    print("\nSign the commit with a key on your GitHub account (`git commit --amend -S --no-edit`) and push it again.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # a crash must read as "couldn't run" (2), never as unsigned (1) or signed (0)
        print(f"check-approvals: couldn't run: {e!r}", file=sys.stderr)
        sys.exit(2)
