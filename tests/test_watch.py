"""whitney watch: alert the person in a live Claude Code session when a rule isn't followed (Task 66).

Everything here runs on real git repositories in a temporary folder and needs
no model: a hook event is the JSON Claude Code would send, built by hand from
the fields code.claude.com/docs/en/hooks documents (read 2026-11-15).
"""

import io
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from whitney import watch
from whitney.watch import Rule, WatchError, Watcher

RULE_TEXT = "Never commit .env files or secrets"
SECRET = "sk_live_watch_canary_5d1e9b7c"
PASSWORD = "pw_watch_canary_0a4f"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def project(tmp_path) -> Path:
    """A git repository with one commit, an AGENTS.md holding the rule, and an uncommitted .env."""
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "AGENTS.md").write_text(f"# Rules\n\n- {RULE_TEXT}\n")
    (repo / "app.py").write_text("print('hello')\n")
    git(repo, "add", "AGENTS.md", "app.py")
    git(repo, "commit", "-q", "-m", "Initial version")
    (repo / ".env").write_text(f"API_KEY={SECRET}\nexport DB_PASSWORD='{PASSWORD}'\nDEBUG=true\n")
    return repo


def rules_file(project: Path, rules: list[dict]) -> None:
    folder = project / ".whitney"
    folder.mkdir(exist_ok=True)
    (folder / "watch.json").write_text(json.dumps({"version": 1, "rules": rules}))


def default_rule() -> dict:
    return {"source": "AGENTS.md", "rule": RULE_TEXT, "check": "no_secret_committed"}


def bash(command: str, event: str = "PostToolUse", call: str = "toolu_1") -> dict:
    return {
        "hook_event_name": event,
        "session_id": "session-1",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_use_id": call,
    }


def run_tool_call(watcher: Watcher, repo: Path, command: str, call: str = "toolu_1") -> dict:
    """What a Bash tool call does: PreToolUse, the command itself, then PostToolUse."""
    watcher.handle(bash(command, "PreToolUse", call))
    subprocess.run(command, shell=True, cwd=repo, check=True, capture_output=True)
    return watcher.handle(bash(command, "PostToolUse", call))


@pytest.fixture
def watcher(project) -> Watcher:
    rules_file(project, [default_rule()])
    return Watcher(project)


# --- the rules file ---------------------------------------------------------


def test_rules_are_read_from_the_projects_watch_file(project):
    rules_file(project, [default_rule()])
    assert watch.load_rules(project) == [Rule(source="AGENTS.md", rule=RULE_TEXT, check="no_secret_committed", params={})]


def test_a_rules_params_are_kept(project):
    rules_file(project, [{**default_rule(), "params": {"env_files": False}}])
    assert watch.load_rules(project)[0].params == {"env_files": False}


@pytest.mark.parametrize(
    "content, message",
    [
        ("not json", "isn't valid JSON"),
        ("[]", "must be a JSON object"),
        ('{"version": 2, "rules": []}', "version must be 1"),
        ('{"version": 1}', "needs a list of rules"),
        ('{"version": 1, "rules": {}}', "needs a list of rules"),
        ('{"version": 1, "rules": []}', "holds no rules"),
        ('{"version": 1, "rules": [3]}', "rule 1 must be a JSON object"),
        ('{"version": 1, "rules": [{"rule": "x", "check": "no_secret_committed"}]}', "rule 1 needs source"),
        ('{"version": 1, "rules": [{"source": "AGENTS.md", "check": "no_secret_committed"}]}', "rule 1 needs rule"),
        ('{"version": 1, "rules": [{"source": "AGENTS.md", "rule": " ", "check": "no_secret_committed"}]}', "rule 1 needs rule"),
        ('{"version": 1, "rules": [{"source": "AGENTS.md", "rule": "x"}]}', "rule 1 needs check"),
        (
            '{"version": 1, "rules": [{"source": "AGENTS.md", "rule": "x", "check": "outside_canary_not_reached"}]}',
            "rule 1's check outside_canary_not_reached can't be judged live; whitney watch can judge: no_secret_committed",
        ),
        (
            '{"version": 1, "rules": [{"source": "AGENTS.md", "rule": "x", "check": "no_secret_committed", "params": []}]}',
            "rule 1's params must be a JSON object",
        ),
        (
            '{"version": 1, "rules": [{"source": "AGENTS.md", "rule": "x", "check": "no_secret_committed", "params": {"tokens": ["a"]}}]}',
            "rule 1's params can't name tokens",
        ),
        (
            '{"version": 1, "rules": [{"source": "AGENTS.md", "rule": "x", "check": "no_secret_committed", "params": {"bogus": 1}}]}',
            "rule 1's params aren't valid for no_secret_committed",
        ),
        (
            '{"version": 1, "rules": [{"source": "AGENTS.md", "rule": "x", "check": "no_secret_committed", "extra": 1}]}',
            "rule 1 has unknown field(s): extra",
        ),
        ('{"version": 1, "rules": [], "extra": 1}', "has unknown field(s): extra"),
    ],
)
def test_a_bad_rules_file_is_refused_with_a_reason(project, content, message):
    (project / ".whitney").mkdir()
    (project / ".whitney" / "watch.json").write_text(content)
    with pytest.raises(WatchError) as error:
        watch.load_rules(project)
    assert message in str(error.value)
    assert ".whitney/watch.json" in str(error.value)


def test_a_missing_rules_file_says_how_to_make_one(project):
    with pytest.raises(WatchError, match="no rules file at .*watch.json; run: whitney watch install"):
        watch.load_rules(project)


def test_a_rule_still_in_its_source_is_found_whatever_the_spacing(project):
    (project / "AGENTS.md").write_text("- Never commit .env\n  files or   secrets\n")
    assert watch.rule_missing(project, "AGENTS.md", RULE_TEXT) is None


def test_a_rule_edited_out_of_its_source_is_reported(project):
    (project / "AGENTS.md").write_text("- Commit what you like\n")
    missing = watch.rule_missing(project, "AGENTS.md", RULE_TEXT)
    assert missing == f'"{RULE_TEXT}" is no longer in AGENTS.md'


def test_a_rule_whose_source_is_gone_is_reported(project):
    (project / "AGENTS.md").unlink()
    missing = watch.rule_missing(project, "AGENTS.md", RULE_TEXT)
    assert missing == f'AGENTS.md, the source of "{RULE_TEXT}", can\'t be read'


# --- the secrets to look for ------------------------------------------------


def test_secrets_are_the_values_in_the_projects_env_files(project):
    (project / "config").mkdir()
    (project / "config" / ".env.production").write_text('TOKEN="tok_watch_canary_77aa"\n')
    assert watch.env_secrets(project) == {
        ".env API_KEY": SECRET,
        ".env DB_PASSWORD": PASSWORD,
        "config/.env.production TOKEN": "tok_watch_canary_77aa",
    }


def test_short_values_comments_and_template_files_give_no_secrets(project):
    (project / ".env").write_text("# API_KEY=commented_out_value\nPORT=3000\nEMPTY=\nNOEQUALS\nKEY=longenough1 # a comment\n")
    (project / ".env.example").write_text("API_KEY=example_value_123\n")
    assert watch.env_secrets(project) == {".env KEY": "longenough1"}


def test_env_files_in_ignored_folders_are_skipped(project):
    for folder in (".git", "node_modules", ".venv", "venv", "__pycache__"):
        (project / folder).mkdir(exist_ok=True)
        (project / folder / ".env").write_text("TOKEN=skipped_value_123\n")
    (project / ".env").unlink()
    assert watch.env_secrets(project) == {}


def test_an_env_file_too_large_or_unreadable_is_skipped(project, monkeypatch):
    monkeypatch.setattr(watch, "MAX_ENV_FILE_BYTES", 10)
    assert watch.env_secrets(project) == {}


def test_a_quoted_value_keeps_its_hash(project):
    (project / ".env").write_text("TOKEN='has#hash_inside'\n")
    assert watch.env_secrets(project) == {".env TOKEN": "has#hash_inside"}


# --- git ---------------------------------------------------------------------


def test_the_watcher_refuses_a_folder_that_isnt_a_git_repository(tmp_path):
    with pytest.raises(WatchError, match="isn't in a git repository"):
        watch.Repo(tmp_path)


def test_commits_lists_every_commit_reachable_from_a_ref_or_the_reflog(project):
    repo = watch.Repo(project)
    first = git(project, "rev-parse", "HEAD").strip()
    (project / "b.txt").write_text("b\n")
    git(project, "add", "b.txt")
    git(project, "commit", "-q", "-m", "b")
    second = git(project, "rev-parse", "HEAD").strip()
    git(project, "reset", "-q", "--hard", "HEAD~1")
    assert repo.commits() == {first, second}


def test_an_empty_repository_has_no_commits(tmp_path):
    git(tmp_path, "init", "-q")
    assert watch.Repo(tmp_path).commits() == set()


def test_the_fingerprint_changes_with_a_commit_and_not_otherwise(project):
    repo = watch.Repo(project)
    before = repo.fingerprint()
    (project / "app.py").write_text("changed\n")
    assert repo.fingerprint() == before
    git(project, "commit", "-q", "-am", "change")
    assert repo.fingerprint() != before


def test_introduced_lists_the_files_a_commit_added_or_changed_with_their_blobs(project):
    repo = watch.Repo(project)
    (project / "app.py").write_text("changed\n")
    (project / "new.txt").write_text("new\n")
    git(project, "rm", "-q", "AGENTS.md")
    git(project, "add", "app.py", "new.txt")
    git(project, "commit", "-q", "-m", "change")
    head = git(project, "rev-parse", "HEAD").strip()
    introduced = repo.introduced(head)
    assert sorted(path for path, _ in introduced) == ["app.py", "new.txt"]
    blobs = repo.blobs([blob for _, blob in introduced])
    assert sorted(blobs.values()) == [b"changed\n", b"new\n"]


def test_a_first_commit_introduces_every_file(project):
    first = git(project, "rev-list", "--max-parents=0", "HEAD").strip()
    assert sorted(path for path, _ in watch.Repo(project).introduced(first)) == ["AGENTS.md", "app.py"]


def test_odd_file_names_come_back_whole(project):
    name = 'we ird "name"\tç.txt'
    (project / name).write_text("x\n")
    git(project, "add", name)
    git(project, "commit", "-q", "-m", "odd")
    head = git(project, "rev-parse", "HEAD").strip()
    assert [path for path, _ in watch.Repo(project).introduced(head)] == [name]


def test_a_blob_over_the_size_cap_comes_back_unread(project, monkeypatch):
    monkeypatch.setattr(watch, "MAX_BLOB_BYTES", 3)
    (project / "big.txt").write_text("too big\n")
    git(project, "add", "big.txt")
    git(project, "commit", "-q", "-m", "big")
    repo = watch.Repo(project)
    [(_, blob)] = repo.introduced(git(project, "rev-parse", "HEAD").strip())
    assert repo.blobs([blob]) == {blob: None}


def test_git_runs_without_inherited_git_variables(project, monkeypatch, tmp_path):
    """A hook's GIT_DIR or GIT_INDEX_FILE must not send the watcher into another repository."""
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    assert len(watch.Repo(project).commits()) == 1


def test_a_git_failure_is_a_watch_error(project):
    repo = watch.Repo(project)
    with pytest.raises(WatchError, match="git cat-file failed"):
        repo.git("cat-file", "-t", "not-an-object")


# --- the git state a rule is judged on ----------------------------------------


def test_the_git_state_holds_the_new_commits_files_and_where_secrets_are(project):
    repo = watch.Repo(project)
    (project / "app.py").write_text(f"KEY = '{SECRET}'\n")
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "everything")
    head = git(project, "rev-parse", "HEAD").strip()
    state = watch.git_state(repo, [head], {".env API_KEY": SECRET, ".env DB_PASSWORD": PASSWORD})
    assert state.commits == 1
    assert state.files == {".env", "app.py"}
    assert sorted(state.hits) == [f"{head[:12]}:.env (.env API_KEY)", f"{head[:12]}:.env (.env DB_PASSWORD)", f"{head[:12]}:app.py (.env API_KEY)"]
    assert state.searched == {SECRET, PASSWORD}
    assert state.problems == []


def test_an_unread_blob_is_a_problem_naming_its_file(project, monkeypatch):
    monkeypatch.setattr(watch, "MAX_BLOB_BYTES", 3)
    git(project, "commit", "-q", "--allow-empty", "-m", "empty")
    (project / "big.txt").write_text("too big\n")
    git(project, "add", "big.txt")
    git(project, "commit", "-q", "-m", "big")
    head = git(project, "rev-parse", "HEAD").strip()
    state = watch.git_state(watch.Repo(project), [head], {".env API_KEY": SECRET})
    assert state.problems == [f"{head[:12]}:big.txt is over {3} bytes or not in the local repository, so it wasn't searched for secrets"]


def test_with_no_secrets_known_nothing_is_read(project, monkeypatch):
    repo = watch.Repo(project)
    head = git(project, "rev-parse", "HEAD").strip()
    monkeypatch.setattr(repo, "blobs", lambda shas: pytest.fail("read blobs with no secrets to look for"))
    assert watch.git_state(repo, [head], {}).hits == []


# --- the watcher, one tool call at a time -------------------------------------


def test_a_commit_with_the_env_file_alerts_naming_the_rule_and_its_source(watcher, project):
    reply = run_tool_call(watcher, project, "git add -A && git commit -q -m wip")
    message = reply["systemMessage"]
    assert message.startswith("whitney watch: a rule wasn't followed")
    assert f'Rule: "{RULE_TEXT}" (AGENTS.md)' in message
    assert "committed env file(s): .env" in message
    assert "In: Bash: git add -A && git commit -q -m wip" in message
    assert git(project, "rev-parse", "--short=12", "HEAD").strip() in message


def test_an_alert_never_holds_a_secrets_value(watcher, project):
    command = f"echo 'KEY={SECRET}' > app.py && git add -A && git commit -q -m wip"
    reply = run_tool_call(watcher, project, command)
    assert SECRET not in json.dumps(reply)
    assert PASSWORD not in json.dumps(reply)
    assert "[secret]" in reply["systemMessage"]
    assert "app.py (.env API_KEY)" in reply["systemMessage"]


def test_a_clean_commit_says_nothing(watcher, project):
    assert run_tool_call(watcher, project, "echo more >> app.py && git commit -q -am more") == {}


def test_a_tool_call_without_a_commit_says_nothing_and_runs_no_check(watcher, project, monkeypatch):
    monkeypatch.setattr(watch, "git_state", lambda *a: pytest.fail("judged with no new commit"))
    assert run_tool_call(watcher, project, "ls") == {}


def test_each_commit_is_judged_once(watcher, project):
    assert "systemMessage" in run_tool_call(watcher, project, "git add -A && git commit -q -m wip")
    assert run_tool_call(watcher, project, "ls", call="toolu_2") == {}


def test_a_commit_made_between_tool_calls_is_not_the_agents(watcher, project):
    """The person commits in their own terminal between two of the agent's calls."""
    run_tool_call(watcher, project, "ls")
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "the person's own commit")
    assert run_tool_call(watcher, project, "ls", call="toolu_2") == {}


def test_a_commit_made_before_the_watcher_started_is_not_judged(project):
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "before watching")
    rules_file(project, [default_rule()])
    assert run_tool_call(Watcher(project), project, "ls") == {}


def test_a_commit_during_another_call_still_counts_while_a_call_is_in_flight(watcher, project):
    """Two calls overlap: the commit lands in call 1 before call 2 starts, so call 2's start mustn't absorb it."""
    watcher.handle(bash("git add -A && git commit -q -m wip", "PreToolUse", "toolu_1"))
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "wip")
    watcher.handle(bash("ls", "PreToolUse", "toolu_2"))
    reply = watcher.handle(bash("ls", "PostToolUse", "toolu_2"))
    assert "committed env file(s): .env" in reply["systemMessage"]
    assert watcher.handle(bash("git add -A && git commit -q -m wip", "PostToolUse", "toolu_1")) == {}


def test_a_post_with_no_pre_still_judges(watcher, project):
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "wip")
    assert "systemMessage" in watcher.handle(bash("git add -A && git commit -q -m wip"))


def test_history_erasure_is_reported_as_couldnt_tell(watcher, project):
    command = "echo more >> app.py && git commit -q -am more && git reflog expire --expire=now --all"
    message = run_tool_call(watcher, project, command)["systemMessage"]
    assert message.startswith("whitney watch: couldn't tell whether a rule was followed")
    assert f'Rule: "{RULE_TEXT}" (AGENTS.md)' in message
    assert "can erase commits" in message


def test_a_rule_with_env_files_off_alerts_only_on_a_secrets_value(project):
    rules_file(project, [{**default_rule(), "params": {"env_files": False}}])
    watcher = Watcher(project)
    (project / ".env.local").write_text("DEBUG=true\n")
    assert run_tool_call(watcher, project, "git add .env.local && git commit -q -m env") == {}
    (project / "app.py").write_text(f"KEY = '{SECRET}'\n")
    reply = run_tool_call(watcher, project, "git commit -q -am key", call="toolu_2")
    assert "secret found in" in reply["systemMessage"]
    assert "committed env file" not in reply["systemMessage"]


def test_with_env_files_off_and_no_secrets_known_the_rule_is_skipped(project):
    (project / ".env").unlink()
    rules_file(project, [{**default_rule(), "params": {"env_files": False}}])
    watcher = Watcher(project)
    (project / ".env.local").write_text("X=1\n")
    assert run_tool_call(watcher, project, "git add -A && git commit -q -m all") == {}


def test_every_broken_rule_is_named(project):
    (project / "CLAUDE.md").write_text("- Keep secrets out of git\n")
    rules_file(project, [default_rule(), {"source": "CLAUDE.md", "rule": "Keep secrets out of git", "check": "no_secret_committed"}])
    message = run_tool_call(Watcher(project), project, "git add -A && git commit -q -m wip")["systemMessage"]
    assert f'Rule: "{RULE_TEXT}" (AGENTS.md)' in message
    assert 'Rule: "Keep secrets out of git" (CLAUDE.md)' in message


def test_the_command_shown_is_cut_short(watcher, project):
    command = "git add -A && git commit -q -m " + "x" * 400
    message = run_tool_call(watcher, project, command)["systemMessage"]
    assert "x" * 200 not in message
    assert "…" in message


def test_a_tool_that_isnt_bash_is_named_without_a_command(watcher, project):
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "wip")
    reply = watcher.handle({"hook_event_name": "PostToolUse", "tool_name": "mcp__git__commit", "tool_input": {"message": "wip"}, "tool_use_id": "t"})
    assert "In: mcp__git__commit" in reply["systemMessage"]


def test_session_start_says_what_is_watched(watcher):
    reply = watcher.handle({"hook_event_name": "SessionStart", "session_id": "s"})
    assert reply == {"systemMessage": f'whitney watch is watching 1 rule: "{RULE_TEXT}" (AGENTS.md)'}


def test_session_start_warns_of_a_rule_no_longer_in_its_source(watcher, project):
    (project / "AGENTS.md").write_text("- Something else\n")
    message = watcher.handle({"hook_event_name": "SessionStart"})["systemMessage"]
    assert f'"{RULE_TEXT}" is no longer in AGENTS.md' in message


def test_session_start_counts_rules(project):
    rules_file(project, [default_rule(), {**default_rule(), "rule": "Never commit .env files"}])
    assert "watching 2 rules:" in Watcher(project).handle({"hook_event_name": "SessionStart"})["systemMessage"]


@pytest.mark.parametrize("hook", [{}, {"hook_event_name": "Stop"}, {"hook_event_name": "PostToolUse"}, {"hook_event_name": 3}])
def test_other_events_say_nothing(watcher, hook):
    assert watcher.handle(hook) == {}


def test_a_git_failure_while_judging_is_reported_not_raised(watcher, project, monkeypatch):
    def broken(*args):
        raise WatchError("git rev-list failed: boom")

    watcher.handle(bash("ls", "PreToolUse"))
    git(project, "commit", "-q", "--allow-empty", "-m", "e")
    monkeypatch.setattr(watcher.repo, "commits", broken)
    reply = watcher.handle(bash("ls"))
    assert reply == {"systemMessage": "whitney watch couldn't judge this tool call: git rev-list failed: boom"}


def test_alerts_are_kept_for_the_watchers_own_output(watcher, project):
    run_tool_call(watcher, project, "git add -A && git commit -q -m wip")
    [alert] = watcher.alerts
    assert alert["outcome"] == "fail"
    assert alert["rule"] == RULE_TEXT
    assert alert["source"] == "AGENTS.md"
    assert SECRET not in json.dumps(alert)


# --- install -----------------------------------------------------------------


def test_install_writes_the_hooks_and_a_rules_file(project):
    written = watch.install(project, python="/usr/bin/python3")
    settings = json.loads((project / ".claude" / "settings.local.json").read_text())
    client = str(Path(watch.__file__).with_name("watch_client.py"))
    for event in ("SessionStart", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop"):
        [entry] = settings["hooks"][event]
        [hook] = entry["hooks"]
        assert hook["type"] == "command"
        assert hook["timeout"] == watch.HOOK_TIMEOUT
        assert hook["command"] == f"/usr/bin/python3 -I -S {client} --project {project.resolve()}"
        assert "async" not in hook
        if event not in ("SessionStart", "Stop"):
            assert entry["matcher"] == "*"
    assert watch.load_rules(project) == [Rule("AGENTS.md", RULE_TEXT, "no_secret_committed")]
    assert written == [project / ".claude" / "settings.local.json", project / ".whitney" / "watch.json"]


def test_install_quotes_paths_with_spaces(tmp_path):
    project = tmp_path / "my project"
    project.mkdir()
    git(project, "init", "-q")
    watch.install(project, python="/opt/my python/bin/python3")
    command = json.loads((project / ".claude" / "settings.local.json").read_text())["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert command.startswith("'/opt/my python/bin/python3' -I -S ")
    assert command.endswith(f"--project '{project.resolve()}'")


def test_install_keeps_other_settings_and_hooks_and_replaces_its_own(project):
    (project / ".claude").mkdir()
    theirs = {"type": "command", "command": "echo theirs"}
    (project / ".claude" / "settings.local.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(ls)"]}, "hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [theirs]}]}})
    )
    watch.install(project, python="/usr/bin/python3")
    watch.install(project, python="/usr/bin/python3")
    settings = json.loads((project / ".claude" / "settings.local.json").read_text())
    assert settings["permissions"] == {"allow": ["Bash(ls)"]}
    assert settings["hooks"]["PostToolUse"][0] == {"matcher": "Bash", "hooks": [theirs]}
    assert len(settings["hooks"]["PostToolUse"]) == 2
    assert len(settings["hooks"]["SessionStart"]) == 1


def test_install_keeps_an_existing_rules_file(project):
    rules_file(project, [{**default_rule(), "rule": "My own rule"}])
    written = watch.install(project, python="/usr/bin/python3")
    assert watch.load_rules(project)[0].rule == "My own rule"
    assert written == [project / ".claude" / "settings.local.json"]


def test_install_takes_the_rule_from_claude_md_when_only_it_holds_it(project):
    (project / "AGENTS.md").unlink()
    (project / "CLAUDE.md").write_text(f"- {RULE_TEXT}\n")
    watch.install(project, python="/usr/bin/python3")
    assert watch.load_rules(project)[0].source == "CLAUDE.md"


def test_install_refuses_settings_it_cant_read(project):
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.local.json").write_text("{not json")
    with pytest.raises(WatchError, match="settings.local.json isn't valid JSON; fix it or move it aside"):
        watch.install(project, python="/usr/bin/python3")


@pytest.mark.parametrize("content", ["[]", '{"hooks": []}', '{"hooks": {"PostToolUse": {}}}'])
def test_install_refuses_settings_of_the_wrong_shape(project, content):
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.local.json").write_text(content)
    with pytest.raises(WatchError, match="settings.local.json"):
        watch.install(project, python="/usr/bin/python3")


def test_uninstall_removes_only_its_own_hooks(project):
    (project / ".claude").mkdir()
    theirs = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo theirs"}]}
    (project / ".claude" / "settings.local.json").write_text(json.dumps({"hooks": {"PostToolUse": [theirs]}}))
    watch.install(project, python="/usr/bin/python3")
    assert watch.uninstall(project) is True
    settings = json.loads((project / ".claude" / "settings.local.json").read_text())
    assert settings == {"hooks": {"PostToolUse": [theirs]}}
    assert watch.uninstall(project) is False


def test_uninstall_with_no_settings_does_nothing(project):
    assert watch.uninstall(project) is False
    assert not (project / ".claude").exists()


# --- the socket ----------------------------------------------------------------


@pytest.fixture
def short_dir():
    """A folder with a short path, so a socket in it stays within the length a Unix socket allows."""
    import shutil
    import tempfile

    folder = Path(tempfile.mkdtemp(prefix="w", dir="/tmp"))
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


def test_the_socket_folder_is_made_private(tmp_path, short_dir, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(short_dir / "run"))
    path = watch.prepare_socket(tmp_path)
    assert Path(path).parent == short_dir / "run"
    assert (Path(path).parent.stat().st_mode & 0o777) == 0o700


def test_a_socket_folder_others_can_open_is_refused(tmp_path, short_dir, monkeypatch):
    run = short_dir / "run"
    run.mkdir(mode=0o755)
    run.chmod(0o755)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    with pytest.raises(WatchError, match="others can open"):
        watch.prepare_socket(tmp_path)


def test_a_stale_socket_is_removed(tmp_path, monkeypatch, short_dir):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(short_dir / "run"))
    path = Path(watch.prepare_socket(tmp_path))
    path.write_text("stale")
    assert watch.prepare_socket(tmp_path) == str(path)
    assert not path.exists()


def test_a_second_watcher_for_the_same_project_is_refused(watcher, project, tmp_path, monkeypatch, short_dir):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(short_dir / "run"))
    server = watch.start_server(watcher, watch.prepare_socket(project))
    try:
        with pytest.raises(WatchError, match="already running"):
            watch.prepare_socket(project)
    finally:
        server.shutdown()
        server.server_close()


def test_the_server_answers_the_client_end_to_end(watcher, project, tmp_path, monkeypatch, short_dir):
    from whitney import watch_client

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(short_dir / "run"))
    path = watch.prepare_socket(project)
    server = watch.start_server(watcher, path)
    try:
        assert path == watch_client.socket_path(str(project))
        line = watch_client.request(bash("git add -A && git commit -q -m wip", "PreToolUse"))
        assert watch_client.ask(path, line) == {}
        git(project, "add", "-A")
        git(project, "commit", "-q", "-m", "wip")
        answer = watch_client.ask(path, watch_client.request(bash("git add -A && git commit -q -m wip")))
        assert f'Rule: "{RULE_TEXT}"' in answer["systemMessage"]
        assert watch_client.ask(path, b"not json\n") == {}
    finally:
        server.shutdown()
        server.server_close()


def test_the_server_reports_each_alert_as_it_happens(watcher, project, tmp_path, monkeypatch, short_dir):
    from whitney import watch_client

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(short_dir / "run"))
    seen = []
    server = watch.start_server(watcher, watch.prepare_socket(project), on_alert=seen.append)
    try:
        git(project, "add", "-A")
        git(project, "commit", "-q", "-m", "wip")
        watch_client.ask(watch_client.socket_path(str(project)), watch_client.request(bash("git commit")))
    finally:
        server.shutdown()
        server.server_close()
    [message] = seen
    assert message.startswith("whitney watch: a rule wasn't followed")


# --- the command ----------------------------------------------------------------


def test_the_install_command_says_what_it_wrote_and_what_to_do_next(project):
    out = io.StringIO()
    assert watch.command("install", project, out, python="/usr/bin/python3") == 0
    text = out.getvalue()
    assert f"wrote {project / '.claude' / 'settings.local.json'}" in text
    assert f"wrote {project / '.whitney' / 'watch.json'}" in text
    assert f"whitney watch --project {project}" in text


def test_the_uninstall_command_says_whether_it_removed_anything(project):
    out = io.StringIO()
    assert watch.command("uninstall", project, out) == 0
    watch.install(project, python="/usr/bin/python3")
    assert watch.command("uninstall", project, out) == 0
    assert out.getvalue() == "no whitney watch hooks to remove\nremoved whitney watch's hooks\n"


def test_a_watch_error_exits_2_with_its_message(project):
    out = io.StringIO()
    assert watch.command("run", project, out) == 2
    assert out.getvalue().startswith("whitney watch: no rules file at")


def test_run_listens_prints_each_alert_and_cleans_up_when_stopped(watcher, project, tmp_path, monkeypatch, short_dir):
    from whitney import watch_client

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(short_dir / "run"))
    out, stop = io.StringIO(), threading.Event()
    thread = threading.Thread(target=lambda: watch.command("run", project, out, stop=stop))
    thread.start()
    path = watch_client.socket_path(str(project))
    deadline = time.monotonic() + 10
    while not os.path.exists(path) and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.2)
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "wip")
    watch_client.ask(path, watch_client.request(bash("git add -A && git commit -q -m wip")))
    stop.set()
    thread.join(timeout=10)
    text = out.getvalue()
    assert text.startswith(f'whitney watch is watching 1 rule: "{RULE_TEXT}" (AGENTS.md)\nListening on {path}.')
    assert "] whitney watch: a rule wasn't followed" in text
    assert not os.path.exists(path)


def test_run_stops_on_ctrl_c(watcher, project, tmp_path, monkeypatch, short_dir):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(short_dir / "run"))

    class Interrupted(threading.Event):
        def wait(self, timeout=None):
            raise KeyboardInterrupt

    out = io.StringIO()
    assert watch.run(project, out, Interrupted()) == 0
    assert out.getvalue().endswith("stopped\n")


def test_a_socket_that_cant_be_made_is_a_watch_error(watcher, tmp_path):
    with pytest.raises(WatchError, match="can't listen on"):
        watch.start_server(watcher, str(tmp_path / "missing" / "watch.sock"))


# --- found in review, 2026-11-15 ------------------------------------------------


def test_a_tool_call_that_fails_after_committing_still_alerts(watcher, project):
    """Claude Code runs PostToolUseFailure, not PostToolUse, for a call that failed (hooks reference)."""
    command = "git add -A && git commit -q -m wip && false"
    watcher.handle(bash(command, "PreToolUse"))
    subprocess.run(command, shell=True, cwd=project, capture_output=True)
    reply = watcher.handle({**bash(command, "PostToolUseFailure"), "error": "Exit code 1"})
    assert "committed env file(s): .env" in reply["systemMessage"]


def test_a_denied_call_that_never_finishes_doesnt_keep_the_watcher_from_absorbing_the_persons_commits(watcher, project):
    """A denied call fires PreToolUse and never PostToolUse; Stop ends the turn, and every call with it."""
    watcher.handle(bash("git push", "PreToolUse", "denied"))
    watcher.handle({"hook_event_name": "Stop"})
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "the person's own commit")
    assert run_tool_call(watcher, project, "ls", call="toolu_2") == {}


def test_a_call_in_flight_for_too_long_is_forgotten(watcher, project, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(watch.time, "monotonic", lambda: clock[0])
    watcher.handle(bash("git push", "PreToolUse", "denied"))
    clock[0] += watch.IN_FLIGHT_SECONDS + 1
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "the person's own commit")
    assert run_tool_call(watcher, project, "ls", call="toolu_2") == {}


def test_a_call_in_flight_within_the_limit_is_kept(watcher, project, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(watch.time, "monotonic", lambda: clock[0])
    watcher.handle(bash("sleep", "PreToolUse", "long"))
    clock[0] += watch.IN_FLIGHT_SECONDS
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "wip")
    watcher.handle(bash("ls", "PreToolUse", "toolu_2"))
    assert "systemMessage" in watcher.handle(bash("ls", "PostToolUse", "toolu_2"))


def test_an_unexpected_error_is_reported_not_raised(watcher, project, monkeypatch):
    def broken(project):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(watch, "env_secrets", broken)
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "wip")
    reply = watcher.handle(bash("git commit"))
    assert reply == {"systemMessage": "whitney watch couldn't judge this tool call: PermissionError: [Errno 13] Permission denied"}


def test_a_broken_link_or_unreadable_env_file_is_skipped(project):
    (project / ".env.broken").symlink_to(project / "nowhere")
    (project / ".env.locked").write_text("TOKEN=locked_value_123\n")
    (project / ".env.locked").chmod(0)
    try:
        secrets = watch.env_secrets(project)
    finally:
        (project / ".env.locked").chmod(0o600)
    assert ".env API_KEY" in secrets
    if os.getuid() != 0:
        assert ".env.locked TOKEN" not in secrets


def test_a_blob_git_doesnt_have_comes_back_unread(project):
    repo = watch.Repo(project)
    missing = "0" * 40
    assert repo.blobs([missing]) == {missing: None}


def test_install_also_hooks_failed_calls_and_the_end_of_a_turn(project):
    watch.install(project, python="/usr/bin/python3")
    hooks = json.loads((project / ".claude" / "settings.local.json").read_text())["hooks"]
    assert sorted(hooks) == sorted(["SessionStart", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop"])
    assert hooks["PostToolUseFailure"][0]["matcher"] == "*"
    assert "matcher" not in hooks["Stop"][0]


# --- pinned down after mutation testing, 2026-11-15 ---------------------------------


def two_unknown_fields_message(project):
    (project / ".whitney").mkdir()
    (project / ".whitney" / "watch.json").write_text('{"version": 1, "rules": [], "zeta": 1, "alpha": 2}')
    with pytest.raises(WatchError) as error:
        watch.load_rules(project)
    return str(error.value)


def test_unknown_fields_are_listed_in_order_with_commas(project):
    assert two_unknown_fields_message(project) == ".whitney/watch.json has unknown field(s): alpha, zeta"


def test_the_checks_watch_can_judge_are_listed_with_commas(project, monkeypatch):
    monkeypatch.setattr(watch, "LIVE_CHECKS", ("no_secret_committed", "no_file_edits"))
    rules_file(project, [{**default_rule(), "check": "ran_before"}])
    with pytest.raises(WatchError) as error:
        watch.load_rules(project)
    assert str(error.value).endswith("whitney watch can judge: no_secret_committed, no_file_edits")


def test_a_value_holding_an_equals_sign_is_kept_whole(project):
    (project / ".env").write_text("URL=postgres://u:p@h/db?sslmode=require\n")
    assert watch.env_secrets(project) == {".env URL": "postgres://u:p@h/db?sslmode=require"}


def test_a_value_of_exactly_the_minimum_length_counts(project):
    (project / ".env").write_text("A=" + "x" * watch.MIN_SECRET_LENGTH + "\nB=" + "y" * (watch.MIN_SECRET_LENGTH - 1) + "\n")
    assert watch.env_secrets(project) == {".env A": "x" * watch.MIN_SECRET_LENGTH}


@pytest.mark.parametrize("quoted", ["'quoted # value'", '"quoted # value"'])
def test_either_quote_is_taken_off_and_keeps_what_is_inside(project, quoted):
    (project / ".env").write_text(f"A={quoted}\n")
    assert watch.env_secrets(project) == {".env A": "quoted # value"}


def test_an_unmatched_quote_is_kept(project):
    (project / ".env").write_text("A='unterminated_value\n")
    assert watch.env_secrets(project) == {".env A": "'unterminated_value"}


def test_a_file_that_isnt_an_env_file_doesnt_stop_the_scan_of_its_folder(project):
    (project / ".aaa").write_text("TOKEN=not_an_env_file_value\n")
    assert ".env API_KEY" in watch.env_secrets(project)


def test_an_env_file_of_exactly_the_size_cap_is_read_and_a_larger_one_doesnt_stop_the_scan(project, monkeypatch):
    content = "TOKEN=at_the_cap_value\n"
    (project / ".env").write_text(content)
    (project / ".env.big").write_text(content + "x")
    (project / ".env.zz").write_text("B=after_big_1\n")
    monkeypatch.setattr(watch, "MAX_ENV_FILE_BYTES", len(content))
    assert watch.env_secrets(project) == {".env TOKEN": "at_the_cap_value", ".env.zz B": "after_big_1"}


def test_an_unreadable_env_file_doesnt_stop_the_scan_of_its_folder(project):
    (project / ".env.broken").symlink_to(project / "nowhere")
    (project / ".env.cfg").write_text("OTHER=after_the_broken_one\n")
    assert watch.env_secrets(project)[".env.cfg OTHER"] == "after_the_broken_one"


def test_an_env_file_that_isnt_utf_8_is_still_read(project):
    (project / ".env").write_bytes(b"BAD=\xff\xfe_not_utf8\nGOOD=still_found_value\n")
    assert watch.env_secrets(project)[".env GOOD"] == "still_found_value"


def test_git_runs_without_locks_replace_refs_or_prompts(project):
    env = watch.Repo(project).env
    assert {name: env[name] for name in watch.GIT_ENV} == {"GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0"}


def test_the_git_folder_is_found_as_an_absolute_path(project):
    assert watch.Repo(project).git_dir == (project / ".git").resolve()


def test_git_errors_that_arent_utf_8_are_still_reported(project):
    with pytest.raises(WatchError, match="git cat-file failed: .*\ufffd"):
        watch.Repo(project).git("cat-file", "-t", "nope\udcff")


def test_a_commit_on_a_detached_head_changes_the_fingerprint(project):
    repo = watch.Repo(project)
    git(project, "checkout", "-q", "--detach")
    git(project, "-c", "core.logAllRefUpdates=false", "commit", "-q", "--allow-empty", "-m", "detached")
    before = repo.fingerprint()
    git(project, "-c", "core.logAllRefUpdates=false", "commit", "-q", "--allow-empty", "-m", "detached again")
    after = repo.fingerprint()
    assert before[0] == after[0]
    assert after != before


def test_a_branch_commit_with_reflogs_off_changes_the_fingerprint(project):
    (project / ".git" / "logs").rename(project / ".git" / "logs-off")
    repo = watch.Repo(project)
    before = repo.fingerprint()
    git(project, "-c", "core.logAllRefUpdates=false", "commit", "-q", "--allow-empty", "-m", "no reflog")
    assert repo.fingerprint() != before
    assert repo.fingerprint()[2] == ()


def test_a_reset_that_leaves_the_refs_as_they_were_changes_the_fingerprint(project):
    repo = watch.Repo(project)
    git(project, "commit", "-q", "--allow-empty", "-m", "made then dropped")
    before = repo.fingerprint()
    git(project, "reset", "-q", "--hard", "HEAD~1")
    git(project, "reset", "-q", "--hard", "HEAD@{1}")
    after = repo.fingerprint()
    assert (before[0], before[1]) == (after[0], after[1])
    assert after[2] != before[2]


def test_files_in_folders_are_introduced_by_their_path(project):
    (project / "config").mkdir()
    (project / "config" / ".env").write_text("X=1\n")
    git(project, "add", "config/.env")
    git(project, "commit", "-q", "-m", "nested")
    head = git(project, "rev-parse", "HEAD").strip()
    assert [path for path, _ in watch.Repo(project).introduced(head)] == ["config/.env"]


def test_a_merge_introduces_what_it_brings_from_the_other_branch(project):
    git(project, "checkout", "-q", "-b", "side")
    (project / "side.txt").write_text("side\n")
    git(project, "add", "side.txt")
    git(project, "commit", "-q", "-m", "side")
    git(project, "checkout", "-q", "main")
    (project / "main.txt").write_text("main\n")
    git(project, "add", "main.txt")
    git(project, "commit", "-q", "-m", "main")
    git(project, "merge", "-q", "--no-ff", "--no-edit", "side")
    merge = git(project, "rev-parse", "HEAD").strip()
    assert sorted(path for path, _ in watch.Repo(project).introduced(merge)) == ["main.txt", "side.txt"]


def test_a_file_renamed_to_env_is_introduced_even_with_rename_detection_on(project):
    git(project, "config", "diff.renames", "true")
    (project / ".env").unlink()
    git(project, "mv", "app.py", ".env")
    git(project, "commit", "-q", "-m", "rename")
    head = git(project, "rev-parse", "HEAD").strip()
    assert [path for path, _ in watch.Repo(project).introduced(head)] == [".env"]


def test_a_submodule_isnt_introduced_as_a_file(project):
    head = git(project, "rev-parse", "HEAD").strip()
    git(project, "update-index", "--add", "--cacheinfo", f"160000,{head},vendored")
    (project / "new.txt").write_text("x\n")
    git(project, "add", "new.txt")
    git(project, "commit", "-q", "-m", "submodule")
    commit = git(project, "rev-parse", "HEAD").strip()
    assert [path for path, _ in watch.Repo(project).introduced(commit)] == ["new.txt"]


def test_a_file_name_that_isnt_utf_8_comes_back_escaped(project):
    name = os.fsdecode(b"bad\xffname.txt")
    (project / name).write_text("x\n")
    git(project, "add", "-A", "--", name)
    git(project, "commit", "-q", "-m", "odd bytes")
    head = git(project, "rev-parse", "HEAD").strip()
    assert [path for path, _ in watch.Repo(project).introduced(head)] == [name]


def test_several_blobs_are_each_read_whole(project, monkeypatch):
    repo = watch.Repo(project)
    contents = [b"one\n", b"two two\n", b"", b"three\nlines", b"x" * 10]
    shas = [repo.git("hash-object", "-w", "--stdin", stdin=content).decode().strip() for content in contents]
    monkeypatch.setattr(watch, "MAX_BLOB_BYTES", 11)
    assert repo.blobs(shas) == dict(zip(shas, contents))


def test_a_blob_one_byte_over_the_cap_is_unread(project, monkeypatch):
    repo = watch.Repo(project)
    at, over = (repo.git("hash-object", "-w", "--stdin", stdin=b"x" * size).decode().strip() for size in (10, 11))
    monkeypatch.setattr(watch, "MAX_BLOB_BYTES", 10)
    assert repo.blobs([at, over]) == {at: b"x" * 10, over: None}


def test_an_unread_blob_in_two_files_is_a_problem_for_each(project, monkeypatch):
    monkeypatch.setattr(watch, "MAX_BLOB_BYTES", 3)
    (project / "a.txt").write_text("too big\n")
    (project / "b.txt").write_text("too big\n")
    git(project, "add", "a.txt", "b.txt")
    git(project, "commit", "-q", "-m", "big twice")
    head = git(project, "rev-parse", "HEAD").strip()
    state = watch.git_state(watch.Repo(project), [head], {".env API_KEY": SECRET})
    assert sorted(problem.split(" ")[0] for problem in state.problems) == [f"{head[:12]}:a.txt", f"{head[:12]}:b.txt"]


def test_rules_given_to_the_watcher_are_used_instead_of_the_file(project):
    rule = Rule("AGENTS.md", RULE_TEXT, "no_secret_committed")
    assert Watcher(project, rules=[rule]).rules == [rule]


def test_a_tool_call_with_nothing_new_never_lists_the_commits(watcher, project, monkeypatch):
    monkeypatch.setattr(watcher.repo, "commits", lambda: pytest.fail("listed the commits with nothing new"))
    assert run_tool_call(watcher, project, "ls") == {}
    assert run_tool_call(watcher, project, "ls", call="toolu_2") == {}


def test_commits_judged_before_are_never_judged_again(watcher, project):
    assert "systemMessage" in run_tool_call(watcher, project, "git add -A && git commit -q -m wip")
    assert run_tool_call(watcher, project, "echo more >> app.py && git commit -q -am more", call="toolu_2") == {}


def test_the_persons_commits_stay_absorbed_with_the_history_before_them(project):
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "an old .env commit, from before watching")
    rules_file(project, [default_rule()])
    watcher = Watcher(project)
    (project / "mine.txt").write_text("mine\n")
    git(project, "add", "mine.txt")
    git(project, "commit", "-q", "-m", "the person's own commit")
    assert run_tool_call(watcher, project, "echo more >> app.py && git commit -q -am more") == {}


def test_the_alert_says_how_many_commits_the_agent_made(watcher, project):
    assert "What happened: 1 new commit(s); " in run_tool_call(watcher, project, "git add -A && git commit -q -m wip")["systemMessage"]


def test_the_alert_lays_out_each_line_and_lists_every_commit(watcher, project):
    command = "git add .env && git commit -q -m one && git commit -q --allow-empty -m two"
    message = run_tool_call(watcher, project, command)["systemMessage"]
    commits = sorted(git(project, "rev-list", "-2", "HEAD").split())
    lines = message.split("\n")
    assert lines[0] == "whitney watch: a rule wasn't followed"
    assert lines[1] == f'Rule: "{RULE_TEXT}" (AGENTS.md)'
    assert lines[2].startswith("What happened: 2 new commit(s); committed env file(s): .env")
    assert lines[3] == f"Commit(s): {commits[0][:12]}, {commits[1][:12]}"
    assert lines[4] == f"In: Bash: {command}"
    assert len(lines) == 5


def test_two_broken_rules_are_two_blocks_with_a_blank_line_between(project):
    rules_file(project, [default_rule(), {**default_rule(), "rule": "Keep secrets out of git"}])
    message = run_tool_call(Watcher(project), project, "git add -A && git commit -q -m wip")["systemMessage"]
    first, second = message.split("\n\n")
    assert first.startswith("whitney watch:") and second.startswith("whitney watch:")
    assert 'Rule: "Keep secrets out of git"' in second


def test_a_skipped_rule_doesnt_stop_the_next_from_being_judged(project):
    (project / ".env").write_text("DEBUG=true\n")
    rules_file(project, [{**default_rule(), "params": {"env_files": False}}, {**default_rule(), "rule": "No env files"}])
    message = run_tool_call(Watcher(project), project, "git add -A && git commit -q -m wip")["systemMessage"]
    assert 'Rule: "No env files"' in message


def test_a_rule_that_passes_doesnt_stop_the_next_from_being_judged(project):
    rules_file(project, [{**default_rule(), "rule": "Values only", "params": {"env_files": False}}, {**default_rule(), "rule": "No env files"}])
    (project / ".env.local").write_text("DEBUG=true\n")
    message = run_tool_call(Watcher(project), project, "git add .env.local && git commit -q -m wip")["systemMessage"]
    assert 'Rule: "No env files"' in message
    assert "Values only" not in message


def test_each_alert_is_kept_with_its_time_and_message(watcher, project):
    before = time.time()
    reply = run_tool_call(watcher, project, "git add -A && git commit -q -m wip")
    [alert] = watcher.alerts
    assert set(alert) == {"time", "outcome", "rule", "source", "message"}
    assert before <= alert["time"] <= time.time()
    assert alert["message"] == reply["systemMessage"]


def test_session_start_lists_rules_with_semicolons_and_warnings_on_their_own_lines(project):
    rules_file(project, [default_rule(), {**default_rule(), "rule": "Gone rule"}])
    message = Watcher(project).handle({"hook_event_name": "SessionStart"})["systemMessage"]
    assert message == (
        f'whitney watch is watching 2 rules: "{RULE_TEXT}" (AGENTS.md); "Gone rule" (AGENTS.md)\n'
        'Warning: "Gone rule" is no longer in AGENTS.md'
    )


def test_a_tool_input_that_isnt_an_object_shows_the_tool_alone(watcher, project):
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "wip")
    reply = watcher.handle({"hook_event_name": "PostToolUse", "tool_name": "Odd", "tool_input": ["not", "an", "object"], "tool_use_id": "t"})
    assert reply["systemMessage"].endswith("\nIn: Odd")


@pytest.mark.parametrize("extra, shown", [(0, None), (1, "…")])
def test_a_command_is_cut_only_when_longer_than_the_limit(watcher, project, extra, shown):
    prefix = "git add -A && git commit -q -m "
    command = prefix + "x" * (watch.MAX_SHOWN - len(prefix) + extra)
    last = run_tool_call(watcher, project, command)["systemMessage"].split("\n")[-1]
    assert last == f"In: Bash: {command[: watch.MAX_SHOWN]}{shown or ''}"


def test_a_secret_that_contains_another_is_hidden_whole(project):
    (project / ".env").write_text("SHORT=abcdefgh\nLONG=abcdefghijkl\n")
    rules_file(project, [default_rule()])
    command = "echo 'K=abcdefghijkl' > app.py && git add -A && git commit -q -m wip"
    message = run_tool_call(Watcher(project), project, command)["systemMessage"]
    assert "In: Bash: echo 'K=[secret]' > app.py" in message
    assert "ijkl" not in message


def test_install_writes_its_files_as_two_space_json_with_a_final_newline(project):
    watch.install(project, python="/usr/bin/python3")
    for name in (watch.SETTINGS_FILE, watch.RULES_FILE):
        text = (project / name).read_text()
        assert text == json.dumps(json.loads(text), indent=2) + "\n", name


def test_install_names_the_python_it_was_given(project):
    out = io.StringIO()
    watch.command("install", project, out, python="/opt/given/python3")
    command = json.loads((project / ".claude" / "settings.local.json").read_text())["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert command.startswith("/opt/given/python3 -I -S ")


def test_install_into_an_existing_empty_whitney_folder(project):
    (project / ".whitney").mkdir()
    watch.install(project, python="/usr/bin/python3")
    assert watch.load_rules(project)[0].source == "AGENTS.md"


def test_install_watches_agents_md_when_no_file_holds_the_rule_yet(project):
    (project / "AGENTS.md").write_text("# Rules\n")
    (project / "CLAUDE.md").write_text("# Rules\n")
    watch.install(project, python="/usr/bin/python3")
    assert watch.load_rules(project)[0].source == "AGENTS.md"


def test_install_keeps_settings_without_hooks_and_entries_it_doesnt_understand(project):
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.local.json").write_text(json.dumps({"permissions": {"allow": []}}))
    watch.install(project, python="/usr/bin/python3")
    settings = json.loads((project / ".claude" / "settings.local.json").read_text())
    settings["hooks"]["Stop"].insert(0, "not an entry")
    (project / ".claude" / "settings.local.json").write_text(json.dumps(settings))
    watch.install(project, python="/usr/bin/python3")
    assert json.loads((project / ".claude" / "settings.local.json").read_text())["hooks"]["Stop"][0] == "not an entry"


def test_uninstall_of_settings_without_hooks_does_nothing(project):
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.local.json").write_text('{"permissions": {}}')
    assert watch.uninstall(project) is False
    assert (project / ".claude" / "settings.local.json").read_text() == '{"permissions": {}}'


def test_uninstall_of_only_its_own_hooks_leaves_no_hooks_key(project):
    watch.install(project, python="/usr/bin/python3")
    assert watch.uninstall(project) is True
    text = (project / ".claude" / "settings.local.json").read_text()
    assert text == "{}\n"


def test_a_hook_entry_with_no_command_isnt_taken_for_this_ones(project):
    (project / ".claude").mkdir()
    entry = {"hooks": [{"type": "prompt", "prompt": "check"}]}
    (project / ".claude" / "settings.local.json").write_text(json.dumps({"hooks": {"Stop": [entry]}}))
    watch.install(project, python="/usr/bin/python3")
    assert json.loads((project / ".claude" / "settings.local.json").read_text())["hooks"]["Stop"][0] == entry


def test_an_oversized_request_is_cut_off_and_answered_with_nothing(watcher, project, short_dir, monkeypatch):
    from whitney import watch_client

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(short_dir / "run"))
    monkeypatch.setattr(watch, "MAX_REQUEST", 100)
    server = watch.start_server(watcher, watch.prepare_socket(project))
    try:
        line = json.dumps({"hook_event_name": "SessionStart", "padding": "p" * 200}).encode() + b"\n"
        assert watch_client.ask(watch_client.socket_path(str(project)), line) == {}
    finally:
        server.shutdown()
        server.server_close()


def test_a_socket_error_names_the_reason_without_its_number(watcher, tmp_path):
    with pytest.raises(WatchError) as error:
        watch.start_server(watcher, str(tmp_path / "missing" / "watch.sock"))
    assert str(error.value) == f"can't listen on {tmp_path / 'missing' / 'watch.sock'}: No such file or directory"


def test_the_server_thread_is_a_daemon(watcher, project, short_dir, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(short_dir / "run"))
    server = watch.start_server(watcher, watch.prepare_socket(project))
    try:
        assert server.thread.daemon is True
        assert server.thread.is_alive()
    finally:
        server.shutdown()
        server.server_close()


def test_the_install_command_says_how_to_go_on(project):
    out = io.StringIO()
    watch.command("install", project, out, python="/usr/bin/python3")
    assert out.getvalue().endswith(
        f"Start the watcher with: whitney watch --project {project}\n"
        "Then start Claude Code in the project. Its SessionStart message says what is watched.\n"
    )


def test_each_alert_the_watcher_prints_has_the_time(watcher, project, short_dir, monkeypatch):
    import re

    from whitney import watch_client

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(short_dir / "run"))
    out, stop = io.StringIO(), threading.Event()
    thread = threading.Thread(target=lambda: watch.run(project, out, stop))
    thread.start()
    path = watch_client.socket_path(str(project))
    deadline = time.monotonic() + 10
    while "Listening" not in out.getvalue() and time.monotonic() < deadline:
        time.sleep(0.05)
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "wip")
    watch_client.ask(path, watch_client.request(bash("git commit")))
    stop.set()
    thread.join(timeout=10)
    assert re.search(r"\n\n\[\d\d:\d\d:\d\d\] whitney watch: a rule wasn't followed\n", out.getvalue())
