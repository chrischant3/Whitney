import http.server
import threading
from pathlib import Path
from runpy import run_path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/update_prs.py"
_update = run_path(str(SCRIPT))
update_pull_requests = _update["update_pull_requests"]
ApiError = _update["ApiError"]
main = _update["main"]

REPO = "owner/repo"


def pr(number, sha, auto_merge=True, draft=False):
    return {"number": number, "draft": draft, "auto_merge": {"merge_method": "merge"} if auto_merge else None, "head": {"sha": sha}}


class FakeGitHub:
    """Answers the three requests the script makes, and records each branch update it is asked for."""

    def __init__(self, prs, behind=None, update_errors=None):
        self.prs = prs
        self.behind = behind or {}
        self.update_errors = update_errors or {}
        self.updated = []

    def __call__(self, method, path, body=None):
        if method == "GET" and path == f"/repos/{REPO}/pulls?state=open&base=main&per_page=100":
            return self.prs
        if method == "GET" and path.startswith(f"/repos/{REPO}/compare/main..."):
            return {"behind_by": self.behind.get(path.rsplit("...", 1)[1], 0)}
        if method == "PUT" and path.endswith("/update-branch"):
            number = int(path.split("/")[-2])
            if number in self.update_errors:
                raise self.update_errors[number]
            self.updated.append((number, body))
            return {"message": "Updating pull request branch."}
        raise AssertionError(f"unexpected request {method} {path}")


def test_a_pull_request_set_to_auto_merge_and_behind_main_is_updated_from_the_head_it_was_seen_at():
    github = FakeGitHub([pr(7, "aaa")], behind={"aaa": 2})

    lines, code = update_pull_requests(github, REPO, "main")

    assert github.updated == [(7, {"expected_head_sha": "aaa"})]
    assert lines == ["#7: updated (2 commit(s) behind main)"]
    assert code == 0


def test_pull_requests_without_auto_merge_and_drafts_are_left_alone():
    github = FakeGitHub([pr(1, "aaa", auto_merge=False), pr(2, "bbb", draft=True)], behind={"aaa": 3, "bbb": 3})

    lines, code = update_pull_requests(github, REPO, "main")

    assert github.updated == []
    assert lines == ["No open pull request into main has auto-merge on."]
    assert code == 0


def test_a_pull_request_already_up_to_date_is_not_updated():
    github = FakeGitHub([pr(4, "ccc")], behind={"ccc": 0})

    lines, code = update_pull_requests(github, REPO, "main")

    assert github.updated == []
    assert lines == ["#4: already up to date with main"]
    assert code == 0


def test_a_conflict_is_reported_and_the_other_pull_requests_are_still_updated():
    conflict = ApiError(422, "merge conflict between base and head")
    github = FakeGitHub([pr(5, "ddd"), pr(6, "eee")], behind={"ddd": 1, "eee": 1}, update_errors={5: conflict})

    lines, code = update_pull_requests(github, REPO, "main")

    assert github.updated == [(6, {"expected_head_sha": "eee"})]
    assert lines == [
        "#5: couldn't be updated: merge conflict between base and head (HTTP 422)",
        "#6: updated (1 commit(s) behind main)",
    ]
    assert code == 0


@pytest.mark.parametrize("status", [401, 403, 404])
def test_a_token_that_cant_update_a_branch_fails_the_run(status):
    github = FakeGitHub([pr(8, "fff")], behind={"fff": 1}, update_errors={8: ApiError(status, "Resource not accessible")})

    lines, code = update_pull_requests(github, REPO, "main")

    assert lines == [f"#8: couldn't be updated: Resource not accessible (HTTP {status})"]
    assert code == 1


def test_without_a_token_it_says_which_secret_to_set_and_cant_run(monkeypatch, capsys):
    monkeypatch.delenv("GH_TOKEN", raising=False)

    code = main(["--repo", REPO])

    assert code == 2
    assert "PR_UPDATE_TOKEN" in capsys.readouterr().err


def test_githubs_error_message_and_status_reach_the_report():
    github_api = _update["github_api"]

    class Refuse(http.server.BaseHTTPRequestHandler):
        def do_PUT(self):
            # Read the body before answering, or closing the socket with it unread can reset the client's read.
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"message": "Resource not accessible by personal access token"}')

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Refuse)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        api = github_api("token", base_url=f"http://127.0.0.1:{server.server_port}")
        with pytest.raises(ApiError) as caught:
            api("PUT", "/repos/owner/repo/pulls/1/update-branch", {"expected_head_sha": "aaa"})
    finally:
        server.shutdown()

    assert (caught.value.status, caught.value.message) == (403, "Resource not accessible by personal access token")
