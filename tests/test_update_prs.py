import http.server
import json
import threading
from pathlib import Path
from runpy import run_path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/update_prs.py"
_update = run_path(str(SCRIPT))
update_pull_requests = _update["update_pull_requests"]
github_api = _update["github_api"]
ApiError = _update["ApiError"]
main = _update["main"]

REPO = "owner/repo"


def pr(number, sha, auto_merge=True, draft=False, head_repo=REPO, maintainer_can_modify=False):
    return {
        "number": number,
        "draft": draft,
        "auto_merge": {"merge_method": "merge"} if auto_merge else None,
        "head": {"sha": sha, "repo": {"full_name": head_repo} if head_repo else None},
        "maintainer_can_modify": maintainer_can_modify,
    }


class FakeGitHub:
    """Answers the three requests the script makes, and records each branch update it is asked for."""

    def __init__(self, prs, behind=None, update_errors=None, compare_errors=None, list_error=None, base="main"):
        self.prs = prs
        self.behind = behind or {}
        self.update_errors = update_errors or {}
        self.compare_errors = compare_errors or {}
        self.list_error = list_error
        self.base = base
        self.updated = []

    def __call__(self, method, path, body=None):
        if method == "GET" and path == f"/repos/{REPO}/pulls?state=open&base={self.base}&per_page=100":
            if self.list_error:
                raise self.list_error
            return self.prs
        if method == "GET" and path.startswith(f"/repos/{REPO}/compare/{self.base}..."):
            sha = path.rsplit("...", 1)[1].split("?")[0]
            if sha in self.compare_errors:
                raise self.compare_errors[sha]
            return {"behind_by": self.behind.get(sha, 0)}
        if method == "PUT" and path.endswith("/update-branch"):
            number = int(path.split("/")[-2])
            if number in self.update_errors:
                raise self.update_errors[number]
            self.updated.append((number, body))
            return {"message": "Updating pull request branch."}
        raise AssertionError(f"unexpected request {method} {path}")


def run(github, base="main"):
    lines = []
    code = update_pull_requests(github, REPO, base, lines.append)
    return lines, code


def test_a_pull_request_set_to_auto_merge_and_behind_main_is_updated_from_the_head_it_was_seen_at():
    github = FakeGitHub([pr(7, "aaa")], behind={"aaa": 2})

    lines, code = run(github)

    assert github.updated == [(7, {"expected_head_sha": "aaa"})]
    assert lines == ["- #7: update requested (2 commit(s) behind main)"]
    assert code == 0


def test_only_the_pull_requests_with_auto_merge_on_that_arent_drafts_are_updated():
    github = FakeGitHub(
        [pr(1, "aaa", auto_merge=False), pr(2, "bbb", draft=True), pr(3, "ccc")], behind={"aaa": 3, "bbb": 3, "ccc": 3}
    )

    lines, code = run(github)

    assert github.updated == [(3, {"expected_head_sha": "ccc"})]
    assert lines == ["- #3: update requested (3 commit(s) behind main)"]
    assert code == 0


def test_with_none_to_update_it_says_so():
    github = FakeGitHub([pr(1, "aaa", auto_merge=False), pr(2, "bbb", draft=True)], behind={"aaa": 3, "bbb": 3})

    lines, code = run(github)

    assert github.updated == []
    assert lines == ["No open pull request into main has auto-merge on and isn't a draft."]
    assert code == 0


def test_another_base_branch_is_read_and_compared_against():
    github = FakeGitHub([pr(9, "aaa")], behind={"aaa": 1}, base="release")

    lines, code = run(github, base="release")

    assert github.updated == [(9, {"expected_head_sha": "aaa"})]
    assert lines == ["- #9: update requested (1 commit(s) behind release)"]


def test_a_pull_request_already_up_to_date_is_not_updated():
    github = FakeGitHub([pr(4, "ccc")], behind={"ccc": 0})

    lines, code = run(github)

    assert github.updated == []
    assert lines == ["- #4: already up to date with main"]
    assert code == 0


def test_a_fork_that_doesnt_allow_maintainer_edits_is_skipped_and_one_that_does_is_updated():
    github = FakeGitHub(
        [pr(10, "aaa", head_repo="someone/fork"), pr(11, "bbb", head_repo="someone/fork", maintainer_can_modify=True)],
        behind={"aaa": 1, "bbb": 1},
    )

    lines, code = run(github)

    assert github.updated == [(11, {"expected_head_sha": "bbb"})]
    assert lines == [
        "- #10: skipped: its branch is in a fork that doesn't allow edits by maintainers",
        "- #11: update requested (1 commit(s) behind main)",
    ]
    assert code == 0


def test_a_conflict_is_reported_and_the_other_pull_requests_are_still_updated():
    conflict = ApiError(422, "merge conflict between base and head")
    github = FakeGitHub([pr(5, "ddd"), pr(6, "eee")], behind={"ddd": 1, "eee": 1}, update_errors={5: conflict})

    lines, code = run(github)

    assert github.updated == [(6, {"expected_head_sha": "eee"})]
    assert lines == [
        "- #5: couldn't be updated: merge conflict between base and head (HTTP 422)",
        "- #6: update requested (1 commit(s) behind main)",
    ]
    assert code == 0


def test_a_failed_comparison_is_reported_and_the_other_pull_requests_are_still_updated():
    github = FakeGitHub(
        [pr(1, "gone"), pr(2, "bbb")], behind={"bbb": 1}, compare_errors={"gone": ApiError(502, "Bad Gateway")}
    )

    lines, code = run(github)

    assert github.updated == [(2, {"expected_head_sha": "bbb"})]
    assert lines == [
        "- #1: couldn't be updated: Bad Gateway (HTTP 502)",
        "- #2: update requested (1 commit(s) behind main)",
    ]
    assert code == 0


@pytest.mark.parametrize("status", [401, 403, 404])
def test_a_token_refused_for_one_pull_request_fails_the_run_after_the_rest_are_tried(status):
    refused = ApiError(status, "Resource not accessible")
    github = FakeGitHub([pr(8, "fff"), pr(9, "ggg")], behind={"fff": 1, "ggg": 1}, update_errors={8: refused})

    lines, code = run(github)

    assert github.updated == [(9, {"expected_head_sha": "ggg"})]
    assert lines[0] == f"- #8: couldn't be updated: Resource not accessible (HTTP {status})"
    assert code == 1


def test_a_token_refused_when_listing_fails_the_run_like_any_other_refusal():
    github = FakeGitHub([], list_error=ApiError(401, "Bad credentials"))

    lines, code = run(github)

    assert lines == ["Couldn't list the pull requests into main: Bad credentials (HTTP 401)"]
    assert code == 1


def test_any_other_failure_to_list_means_it_couldnt_run():
    github = FakeGitHub([], list_error=ApiError(500, "Server Error"))

    lines, code = run(github)

    assert lines == ["Couldn't list the pull requests into main: Server Error (HTTP 500)"]
    assert code == 2


def test_without_a_token_it_says_which_secret_to_set_and_cant_run(monkeypatch, capsys):
    monkeypatch.delenv("GH_TOKEN", raising=False)

    code = main(["--repo", REPO])

    assert code == 2
    assert "PR_UPDATE_TOKEN" in capsys.readouterr().out


class FakeGitHubServer(http.server.BaseHTTPRequestHandler):
    """GitHub's REST API, as far as the script uses it: it lists one pull request, which is behind, and refuses to update it."""

    requests = []

    def _answer(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        self.requests.append(("GET", self.path, self.headers["Authorization"], None))
        if "/pulls?" in self.path:
            self._answer(200, [pr(3, "aaa")])
        else:
            self._answer(200, {"behind_by": 4})

    def do_PUT(self):
        # Read the body before answering, or closing the socket with it unread can reset the client's read.
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(("PUT", self.path, self.headers["Authorization"], body))
        self._answer(403, {"message": "Resource not accessible by personal access token"})

    def log_message(self, *args):
        pass


@pytest.fixture
def github_server():
    FakeGitHubServer.requests = []
    server = http.server.HTTPServer(("127.0.0.1", 0), FakeGitHubServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_over_http_each_request_carries_the_token_and_githubs_refusal_reaches_the_report(github_server):
    lines = []

    code = update_pull_requests(github_api("secret-token", base_url=github_server), REPO, "main", lines.append)

    assert FakeGitHubServer.requests == [
        ("GET", f"/repos/{REPO}/pulls?state=open&base=main&per_page=100", "Bearer secret-token", None),
        ("GET", f"/repos/{REPO}/compare/main...aaa?per_page=1", "Bearer secret-token", None),
        ("PUT", f"/repos/{REPO}/pulls/3/update-branch", "Bearer secret-token", {"expected_head_sha": "aaa"}),
    ]
    assert lines == ["- #3: couldn't be updated: Resource not accessible by personal access token (HTTP 403)"]
    assert code == 1


def test_main_prints_each_line_and_returns_the_code(monkeypatch, capsys, github_server):
    monkeypatch.setenv("GH_TOKEN", "secret-token")

    code = main(["--repo", REPO, "--api-url", github_server])

    assert code == 1
    assert capsys.readouterr().out == "- #3: couldn't be updated: Resource not accessible by personal access token (HTTP 403)\n"
