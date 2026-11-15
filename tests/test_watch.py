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
    assert watch.rule_missing(project, Rule("AGENTS.md", RULE_TEXT, "no_secret_committed")) is None


def test_a_rule_edited_out_of_its_source_is_reported(project):
    (project / "AGENTS.md").write_text("- Commit what you like\n")
    missing = watch.rule_missing(project, Rule("AGENTS.md", RULE_TEXT, "no_secret_committed"))
    assert missing == f'"{RULE_TEXT}" is no longer in AGENTS.md'


def test_a_rule_whose_source_is_gone_is_reported(project):
    (project / "AGENTS.md").unlink()
    missing = watch.rule_missing(project, Rule("AGENTS.md", RULE_TEXT, "no_secret_committed"))
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
