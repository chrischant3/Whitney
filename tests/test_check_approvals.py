import io
import json
import subprocess
import sys
from pathlib import Path
from runpy import run_path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_approvals.py"
_approvals = run_path(str(SCRIPT))
commits_needing_approval = _approvals["commits_needing_approval"]
unapproved = _approvals["unapproved"]
github_lookup = _approvals["github_lookup"]
main = _approvals["main"]


def git(repo, *args):
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def change(repo, path, message):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(f"{message}\n")
    git(repo, "add", path)
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    change(tmp_path, "app.py", "base")
    git(tmp_path, "checkout", "-q", "-b", "change")
    return tmp_path


def test_commits_that_approve_an_exception_or_change_the_guard_need_a_signature(repo):
    change(repo, "app.py", "ordinary work")
    approval = change(repo, "CONSTRAINTS.md", "Add exception E9\n\nExplained.\n\nException-approved: E9")
    workflow = change(repo, ".github/workflows/floor-guard.yml", "Run the guard")
    guard = change(repo, "scripts/floor_guard.py", "Change the guard")
    checker = change(repo, "scripts/check_approvals.py", "Change the approval check")

    assert commits_needing_approval(repo, "main") == [
        (approval, "it carries an Exception-approved trailer"),
        (workflow, "it changes .github/workflows/floor-guard.yml"),
        (guard, "it changes scripts/floor_guard.py"),
        (checker, "it changes scripts/check_approvals.py"),
    ]


def test_a_trailer_mentioned_mid_line_or_other_guard_like_paths_need_nothing(repo):
    change(repo, "notes.md", "Explain that Exception-approved: lines are human-only")
    change(repo, "scripts/floor_guard_notes.md", "Notes beside the guard")
    change(repo, "docs/.github/workflows/example.yml", "A documented example")
    assert commits_needing_approval(repo, "main") == []


def test_a_commit_changing_several_guarded_files_names_them_all(repo):
    (repo / "scripts").mkdir()
    (repo / "scripts/floor_guard.py").write_text("x\n")
    (repo / "scripts/check_approvals.py").write_text("y\n")
    git(repo, "add", "scripts")
    git(repo, "commit", "-qm", "Change both")
    assert commits_needing_approval(repo, "main") == [
        (git(repo, "rev-parse", "HEAD"), "it changes scripts/check_approvals.py, scripts/floor_guard.py")
    ]


def test_commits_already_on_the_base_are_not_checked(repo):
    git(repo, "checkout", "-q", "main")
    change(repo, "scripts/floor_guard.py", "Changed on main, already reviewed")
    git(repo, "checkout", "-q", "change")
    git(repo, "merge", "-q", "--no-edit", "main")
    assert commits_needing_approval(repo, "main") == []


def commit(verified=True, reason="valid", login="owner"):
    return {"commit": {"verification": {"verified": verified, "reason": reason}}, "committer": {"login": login} if login else None}


def test_a_verified_commit_by_an_approver_is_approved():
    lookups = {"abc123": commit()}
    assert unapproved([("abc123", "it carries an Exception-approved trailer")], lookups.__getitem__, {"owner"}) == []


def test_an_unsigned_commit_is_not_approved():
    lookups = {"0123456789abcdef": commit(verified=False, reason="unsigned")}
    assert unapproved([("0123456789abcdef", "it changes scripts/floor_guard.py")], lookups.__getitem__, {"owner"}) == [
        "0123456789ab needs an approver's signature because it changes scripts/floor_guard.py, "
        "but GitHub doesn't show it as verified (unsigned)"
    ]


def test_a_commit_signed_by_someone_who_isnt_an_approver_is_not_approved():
    lookups = {"0123456789abcdef": commit(login="agent-bot"), "fedcba9876543210": commit(login=None)}
    needed = [("0123456789abcdef", "it carries an Exception-approved trailer"), ("fedcba9876543210", "it changes x")]
    assert unapproved(needed, lookups.__getitem__, {"owner", "reviewer"}) == [
        "0123456789ab needs an approver's signature because it carries an Exception-approved trailer, "
        "but it was committed by agent-bot, who isn't an approver",
        "fedcba987654 needs an approver's signature because it changes x, "
        "but it was committed by an unknown account, who isn't an approver",
    ]


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_lookup_reads_the_commit_from_githubs_api(monkeypatch):
    seen = {}

    def urlopen(request, timeout):
        seen.update(url=request.full_url, headers=dict(request.header_items()), timeout=timeout)
        return Response(json.dumps(commit()).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    assert github_lookup("owner/repo", "a-token")("abc123") == commit()
    assert seen["url"] == "https://api.github.com/repos/owner/repo/commits/abc123"
    assert seen["headers"]["Authorization"] == "Bearer a-token"
    assert seen["headers"]["Accept"] == "application/vnd.github+json"
    assert seen["timeout"] == 30


def test_the_lookup_works_without_a_token_on_a_public_repository(monkeypatch):
    seen = {}

    def urlopen(request, timeout):
        seen.update(headers=dict(request.header_items()))
        return Response(b"{}")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    github_lookup("owner/repo", None)("abc123")
    assert "Authorization" not in seen["headers"]


@pytest.fixture
def fake_github(monkeypatch):
    """GitHub's view of each commit, by sha; main() looks commits up here instead of over the network."""
    commits = {}
    calls = []

    def lookup_for(github_repo, token):
        calls.append((github_repo, token))
        return commits.__getitem__

    monkeypatch.setitem(main.__globals__, "github_lookup", lookup_for)
    monkeypatch.setenv("GITHUB_TOKEN", "a-token")
    return commits, calls


def run_main(repo, *extra):
    return main(["--repo", str(repo), "--base", "main", "--github-repo", "owner/repo", "--approvers", "owner, reviewer", *extra])


def test_main_passes_when_nothing_needs_a_signature(repo, fake_github, capsys):
    change(repo, "app.py", "ordinary work")
    assert run_main(repo) == 0
    assert capsys.readouterr().out == "check-approvals: no commit needs an approver's signature\n"
    assert fake_github[1] == []


def test_main_passes_when_every_commit_that_needs_one_is_signed(repo, fake_github, capsys):
    commits, calls = fake_github
    commits[change(repo, "CONSTRAINTS.md", "Approve\n\nException-approved: E9")] = commit(login="reviewer")
    commits[change(repo, "scripts/floor_guard.py", "Guard")] = commit()
    assert run_main(repo) == 0
    assert capsys.readouterr().out == "check-approvals: 2 commit(s) signed by an approver\n"
    assert calls == [("owner/repo", "a-token")]


def test_main_fails_and_names_each_commit_that_isnt_signed(repo, fake_github, capsys):
    commits, _ = fake_github
    sha = change(repo, "CONSTRAINTS.md", "Approve\n\nException-approved: E9")
    commits[sha] = commit(verified=False, reason="unsigned")
    assert run_main(repo) == 1
    assert capsys.readouterr().err == (
        "check-approvals: 1 commit(s) need an approver's signature:\n"
        f"  {sha[:12]} needs an approver's signature because it carries an Exception-approved trailer, "
        "but GitHub doesn't show it as verified (unsigned)\n"
        "\nSign the commit with a key on your GitHub account (`git commit --amend -S --no-edit`) and push it again.\n"
    )


def test_main_reads_the_repository_and_approvers_from_github_actions(repo, fake_github, monkeypatch):
    commits, calls = fake_github
    commits[change(repo, "scripts/floor_guard.py", "Guard")] = commit(login="the-owner")
    monkeypatch.setenv("GITHUB_REPOSITORY", "the-owner/whitney")
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "the-owner")
    monkeypatch.delenv("APPROVERS", raising=False)
    assert main(["--repo", str(repo), "--base", "main"]) == 0
    assert calls == [("the-owner/whitney", "a-token")]


def test_an_approvers_variable_overrides_the_repository_owner(repo, fake_github, monkeypatch):
    commits, _ = fake_github
    commits[change(repo, "scripts/floor_guard.py", "Guard")] = commit(login="the-owner")
    monkeypatch.setenv("GITHUB_REPOSITORY", "an-org/whitney")
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "the-owner")
    monkeypatch.setenv("APPROVERS", "someone-else")
    assert main(["--repo", str(repo), "--base", "main"]) == 1


@pytest.mark.parametrize("missing", ["--github-repo", "--approvers"])
def test_main_cannot_run_without_a_repository_or_approvers(repo, fake_github, monkeypatch, capsys, missing):
    for name in ("GITHUB_REPOSITORY", "GITHUB_REPOSITORY_OWNER", "APPROVERS"):
        monkeypatch.delenv(name, raising=False)
    args = {"--github-repo": "owner/repo", "--approvers": "owner"}
    args[missing] = ""
    assert main(["--repo", str(repo), "--base", "main", *[part for pair in args.items() for part in pair]]) == 2
    assert capsys.readouterr().err == "check-approvals: needs --github-repo and --approvers (or GITHUB_REPOSITORY and APPROVERS)\n"


def test_the_script_reports_that_it_couldnt_run_when_git_fails(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--repo", str(tmp_path), "--base", "main", "--github-repo", "o/r", "--approvers", "o"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert result.stderr.startswith("check-approvals: couldn't run: ")
