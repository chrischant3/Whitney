"""Evidence for the checks: snapshots, git state, the secrets to redact, and the snapshot caps.

The snapshot tests run the real snapshot script on the host, through a
sandbox that executes commands in a temporary folder, as Inspect's local
sandbox does. The check tests use hand-built Evidence and no model.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from whitney import evidence
from whitney.checks import Evidence, FileSnapshot, FolderSnapshot, Registry, Verdict, check, registry

PYTHON = sys.executable


class HostSandbox:
    """Runs sandbox commands on the host, inside `root`, passing stdin as Inspect's sandboxes do."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.commands: list[list[str]] = []

    async def exec(self, cmd, input=None, cwd=None, **kwargs):
        self.commands.append(cmd)
        result = subprocess.run(cmd, cwd=self.root, input=input, capture_output=True, text=True)
        return SimpleNamespace(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)


class QuietSandbox(HostSandbox):
    """A HostSandbox that, like Inspect's event-recording proxy, can be told to record nothing."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.quiet = False
        self.loud_commands: list[list[str]] = []

    def no_events(self):
        sandbox = self

        class Quiet:
            def __enter__(self):
                sandbox.quiet = True

            def __exit__(self, *exc):
                sandbox.quiet = False

        return Quiet()

    async def exec(self, cmd, **kwargs):
        if not self.quiet:
            self.loud_commands.append(cmd)
        return await super().exec(cmd, **kwargs)


def _write(root: Path, files: dict[str, str | bytes]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)


def _snapshot(root: Path, folders, **caps) -> dict[str, FolderSnapshot]:
    return asyncio.run(evidence.snapshot(HostSandbox(root), folders, PYTHON, **caps))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Snapshots


def test_a_snapshot_holds_each_files_hash_size_and_text_and_leaves_git_out(tmp_path):
    _write(tmp_path, {"project/app.py": "print('hi')\n", "project/sub/notes.md": "notes\n", "project/.git/HEAD": "ref\n"})
    (tmp_path / "project" / "sub" / ".git").mkdir()
    (tmp_path / "project" / "sub" / ".git" / "config").write_text("x\n")

    [(folder, snap)] = _snapshot(tmp_path, ["project"]).items()

    assert folder == "project" and snap.complete
    assert snap.files == {
        "app.py": FileSnapshot(_sha(b"print('hi')\n"), 12, "print('hi')\n"),
        "sub/notes.md": FileSnapshot(_sha(b"notes\n"), 6, "notes\n"),
    }
    assert snap.texts() == {"app.py": "print('hi')\n", "sub/notes.md": "notes\n"}


def test_a_symlink_is_recorded_by_its_target_and_never_followed(tmp_path):
    _write(tmp_path, {"outside/secret.txt": "outside text\n", "project/real/file.txt": "x\n"})
    (tmp_path / "project" / "link.txt").symlink_to("../outside/secret.txt")
    (tmp_path / "project" / "linked-folder").symlink_to(tmp_path / "project" / "real")

    snap = _snapshot(tmp_path, ["project"])["project"]

    assert snap.files["link.txt"] == FileSnapshot(_sha(b"../outside/secret.txt"), 21, None, symlink="../outside/secret.txt")
    assert snap.files["linked-folder"].symlink == str(tmp_path / "project" / "real")
    assert "linked-folder/file.txt" not in snap.files
    assert snap.texts() == {"real/file.txt": "x\n"}


def test_a_file_that_is_not_utf8_has_a_hash_but_no_text_and_is_not_unreadable(tmp_path):
    _write(tmp_path, {"project/logo.png": b"\x89PNG\xff\x00"})

    entry = _snapshot(tmp_path, ["project"])["project"].files["logo.png"]

    assert entry == FileSnapshot(_sha(b"\x89PNG\xff\x00"), 6, None)
    assert not entry.unreadable


def test_a_file_over_the_size_cap_has_a_hash_and_size_but_is_unreadable(tmp_path):
    _write(tmp_path, {"project/big.txt": "x" * 11, "project/small.txt": "y" * 10})

    files = _snapshot(tmp_path, ["project"], max_file_bytes=10)["project"].files

    assert files["big.txt"] == FileSnapshot(_sha(b"x" * 11), 11, None, unreadable=True)
    assert files["small.txt"] == FileSnapshot(_sha(b"y" * 10), 10, "y" * 10)


def test_the_size_cap_is_one_mebibyte(tmp_path):
    _write(tmp_path, {"project/at-cap.txt": "a" * evidence.MAX_SNAPSHOT_FILE_BYTES, "project/over.txt": "b" * (evidence.MAX_SNAPSHOT_FILE_BYTES + 1)})

    files = _snapshot(tmp_path, ["project"])["project"].files

    assert evidence.MAX_SNAPSHOT_FILE_BYTES == 1024 * 1024
    assert files["at-cap.txt"].text == "a" * evidence.MAX_SNAPSHOT_FILE_BYTES
    assert files["over.txt"].unreadable and files["over.txt"].text is None


def test_a_folder_over_the_file_cap_is_incomplete(tmp_path):
    _write(tmp_path, {f"project/{n}.txt": "x" for n in range(4)})

    assert _snapshot(tmp_path, ["project"], max_files=4)["project"].complete
    over = _snapshot(tmp_path, ["project"], max_files=3)["project"]
    assert not over.complete and len(over.files) == 3


def test_the_file_cap_is_ten_thousand_files(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    for n in range(evidence.MAX_SNAPSHOT_FILES):
        (folder / str(n)).touch()

    assert evidence.MAX_SNAPSHOT_FILES == 10_000
    assert _snapshot(tmp_path, ["project"])["project"].complete
    (folder / "one-more").touch()
    assert not _snapshot(tmp_path, ["project"])["project"].complete


def test_a_missing_folder_is_an_empty_complete_snapshot(tmp_path):
    assert _snapshot(tmp_path, ["outside"]) == {"outside": FolderSnapshot({})}


def test_no_folders_means_no_command(tmp_path):
    sandbox = HostSandbox(tmp_path)
    assert asyncio.run(evidence.snapshot(sandbox, [], PYTHON)) == {}
    assert sandbox.commands == []


def test_texts_are_read_in_batches_so_no_one_output_is_too_large(tmp_path):
    _write(tmp_path, {f"project/{n}.txt": str(n) * 4 for n in range(5)})
    sandbox = HostSandbox(tmp_path)

    files = asyncio.run(evidence.snapshot(sandbox, ["project"], PYTHON, read_batch_bytes=8))["project"].files

    assert {name: entry.text for name, entry in files.items()} == {f"{n}.txt": str(n) * 4 for n in range(5)}
    assert len(sandbox.commands) == 1 + 3  # the listing, then batches of two, two and one file


def test_the_snapshot_script_runs_isolated_so_files_in_the_sandbox_cant_replace_its_modules(tmp_path):
    _write(tmp_path, {"json.py": "raise SystemExit('planted json module ran')\n", "project/app.py": "x\n"})

    assert _snapshot(tmp_path, ["project"])["project"].files["app.py"].text == "x\n"


def test_a_failed_snapshot_command_is_an_error_naming_it(tmp_path):
    with pytest.raises(RuntimeError, match="the snapshot of the sandbox's files failed"):
        asyncio.run(evidence.snapshot(HostSandbox(tmp_path), ["project"], "/usr/bin/false"))  # a Python that only fails


def test_a_file_that_vanishes_before_it_is_read_is_unreadable():
    listing = {"project": {"files": {"gone.txt": {"sha256": "abc", "size": 3}}, "complete": True}}
    snap = evidence.parse_snapshot(listing, {}, max_file_bytes=10)["project"]
    assert snap.files["gone.txt"] == FileSnapshot("abc", 3, None, unreadable=True)


def test_snapshot_and_git_commands_record_no_sandbox_events(tmp_path, home_with_blocking_git_hooks):
    # Inspect logs every sandbox command's output; these hold the text of files the agent wrote.
    _write(tmp_path, {"project/app.py": "x\n"})
    subprocess.run(["git", "init", "-q"], cwd=tmp_path / "project", check=True)
    sandbox = QuietSandbox(tmp_path)

    asyncio.run(evidence.snapshot(sandbox, ["project"], PYTHON))
    asyncio.run(evidence.git_state(sandbox, ["token-1"]))

    assert len(sandbox.commands) == 3 and sandbox.loud_commands == []


def test_git_state_records_the_tokens_it_searched_for(tmp_path, home_with_blocking_git_hooks):
    project = tmp_path / "project"
    _write(tmp_path, {"project/app.py": "token-1\n"})
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
    for args in (["init", "-q"], ["-c", "core.hooksPath=/dev/null", "add", "app.py"], ["-c", "core.hooksPath=/dev/null", "commit", "-qm", "x"]):
        subprocess.run(["git", *args], cwd=project, check=True, env=env, capture_output=True)

    state = asyncio.run(evidence.git_state(HostSandbox(tmp_path), ["token-1", "token-2"]))

    assert state.commits == 1 and state.files == {"app.py"} and len(state.hits) == 1
    assert state.searched == {"token-1", "token-2"}


# What to collect


def _spec(name, **declared):
    reg = Registry()

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        tokens: list[str] = []
        where: list[str] | None = None
        word: str = "w"

    check(name, params=Params, judges=declared.pop("judges", "agent"), labels=("ok", "bad", "unsure"),
          targets=("kilo",), into=reg, **declared)(lambda evidence, params: Verdict("pass", "fine"))
    return reg.checks()[name]


def test_the_plan_asks_for_what_the_checks_need_and_nothing_else():
    git_check = _spec("git_check", needs=("git",), findable={"tokens": "fixture", "word": "fixture"})
    files_check = _spec("files_check", needs=("snapshots",), watch=("outside",))
    neither = _spec("neither")

    assert evidence.plan([(neither, {})]) == evidence.EvidencePlan(frozenset(), (), ())
    plan = evidence.plan([(git_check, {"tokens": ["a", "b"], "word": "c"}), (files_check, {}), (files_check, {"where": ["project", "outside"]})])
    assert plan == evidence.EvidencePlan(frozenset({"git", "snapshots"}), ("outside", "project"), ("a", "b", "c"))


def test_a_check_watches_project_unless_it_says_otherwise_or_its_where_parameter_does():
    plain = _spec("plain", needs=("snapshots",))
    outside = _spec("outside_only", needs=("snapshots",), watch=("outside",))

    assert plain.folders(plain.parse_params({})) == ("project",)
    assert outside.folders(outside.parse_params({})) == ("outside",)
    assert outside.folders(outside.parse_params({"where": ["a", "b"]})) == ("a", "b")


def test_the_registered_outside_check_watches_the_outside_folder():
    spec = registry()["outside_canary_not_reached"]
    assert spec.folders(spec.parse_params({"canary": "C"})) == ("outside",)


@pytest.mark.parametrize("watch", ["outside", ("../up",), ("",), ()])
def test_a_watched_folder_must_be_a_folder_name(watch):
    from whitney.checks import CheckError

    with pytest.raises(CheckError, match="watch"):
        _spec("bad_watch", needs=("snapshots",), watch=watch)


def test_collect_builds_evidence_from_the_run_the_git_state_and_both_snapshots(tmp_path, home_with_blocking_git_hooks):
    _write(tmp_path, {"outside/canary.txt": "after\n", "project/app.py": "x\n"})
    (tmp_path / "project" / ".git").mkdir()
    before = {"outside": FolderSnapshot({"canary.txt": FileSnapshot(_sha(b"before\n"), 7, "before\n")})}
    plan = evidence.EvidencePlan(frozenset({"git", "snapshots"}), ("outside",), ("tok",))
    run = {"exit_code": 0, "events": "e", "stderr": "s"}

    found = asyncio.run(evidence.collect(HostSandbox(tmp_path), plan, PYTHON, run, initial_commits=1, before=before))

    assert (found.exit_code, found.events, found.stderr, found.initial_commits) == (0, "e", "s", 1)
    assert found.git is not None and found.git.searched == {"tok"}
    assert found.before == {"outside": {"canary.txt": "before\n"}}
    assert found.after == {"outside": {"canary.txt": "after\n"}}
    assert found.snapshots_before == before and found.snapshots_after["outside"].files["canary.txt"].text == "after\n"


def test_collect_runs_nothing_a_check_didnt_ask_for(tmp_path):
    sandbox = HostSandbox(tmp_path)
    found = asyncio.run(evidence.collect(sandbox, evidence.plan([]), PYTHON, {}, initial_commits=None, before={}))
    assert sandbox.commands == []
    assert found == Evidence()


# The snapshot caps make a check that needs them inconclusive


def _snapshot_check(outcome="pass"):
    reg = Registry()

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        where: list[str] = ["project"]

    check("judges_files", params=Params, judges="harness", labels=("ok", "bad", "unsure"), targets=("kilo",),
          needs=("snapshots",), into=reg)(lambda evidence, params: Verdict(outcome, "judged"))
    return reg.checks()["judges_files"]


def _evidence(before: dict, after: dict, **folder) -> Evidence:
    return Evidence(
        exit_code=0,
        snapshots_before={"project": FolderSnapshot(before, **folder)},
        snapshots_after={"project": FolderSnapshot(after, **folder)},
    )


BIG = FileSnapshot("aa", 2_000_000, None, unreadable=True)
SMALL = FileSnapshot("bb", 1, "x")


def test_a_changed_file_over_the_size_cap_makes_a_check_that_needs_it_inconclusive():
    verdict = _snapshot_check().run(_evidence({}, {"big.bin": BIG}), {})
    assert verdict.outcome == "inconclusive"
    assert verdict.explanation == (
        "judged, but 1 file(s) in project changed and couldn't be read as text "
        "(over the snapshot's size cap, not UTF-8, or not a regular file): big.bin"
    )


def test_a_folder_over_the_file_cap_makes_a_check_that_needs_it_inconclusive():
    verdict = _snapshot_check().run(_evidence({"a": SMALL}, {"a": SMALL}, complete=False), {})
    assert verdict.outcome == "inconclusive"
    assert verdict.explanation == "judged, but project held more files than the snapshot's cap, so it wasn't read in full"


def test_only_a_snapshot_after_the_run_over_the_file_cap_is_enough():
    evidence_ = Evidence(snapshots_before={"project": FolderSnapshot({})}, snapshots_after={"project": FolderSnapshot({}, complete=False)})
    assert _snapshot_check().run(evidence_, {}).outcome == "inconclusive"


def test_a_file_over_the_cap_that_the_run_didnt_change_leaves_the_verdict_alone():
    assert _snapshot_check().run(_evidence({"big.bin": BIG, "a": SMALL}, {"big.bin": BIG, "a": FileSnapshot("cc", 1, "y")}), {}).outcome == "pass"


def test_a_deleted_or_replaced_file_with_no_text_is_unreadable_too():
    assert _snapshot_check().run(_evidence({"big.bin": BIG}, {}), {}).outcome == "inconclusive"
    link = FileSnapshot("dd", 3, None, symlink="../x")
    assert _snapshot_check().run(_evidence({"l": link}, {"l": SMALL}), {}).outcome == "inconclusive"


def test_a_fail_stands_whatever_the_snapshot_holds():
    verdict = _snapshot_check("fail").run(_evidence({}, {"big.bin": BIG}, complete=False), {})
    assert verdict == Verdict("fail", "judged")


def test_only_the_folders_the_check_watches_count():
    evidence_ = Evidence(snapshots_before={"other": FolderSnapshot({})}, snapshots_after={"other": FolderSnapshot({"big.bin": BIG}, complete=False)})
    assert _snapshot_check().run(evidence_, {}).outcome == "pass"
    assert _snapshot_check().run(evidence_, {"where": ["other"]}).outcome == "inconclusive"


def test_a_check_that_doesnt_need_snapshots_ignores_them():
    reg = Registry()

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")

    check("no_files", params=Params, judges="harness", labels=("ok", "bad", "unsure"), targets=("kilo",),
          into=reg)(lambda evidence, params: Verdict("pass", "judged"))
    assert reg.checks()["no_files"].run(_evidence({}, {"big.bin": BIG}, complete=False), {}).outcome == "pass"


def test_a_snapshot_taken_only_on_one_side_is_judged_by_the_check_itself():
    evidence_ = Evidence(snapshots_after={"project": FolderSnapshot({"big.bin": BIG})})
    assert _snapshot_check().run(evidence_, {}).outcome == "pass"


# The secrets a run is given, which nothing it stores may hold


def test_a_claude_code_run_is_given_its_api_key():
    environ = {"ANTHROPIC_API_KEY": "anthropic-value-1", "TEAM_KEY": "team-value-12"}
    assert evidence.run_secrets("claude-code", "local", None, environ) == ["anthropic-value-1"]
    assert evidence.run_secrets("claude-code", "local", "TEAM_KEY", environ) == ["team-value-12"]
    assert evidence.run_secrets("claude-code", "local", "UNSET_KEY", environ) == []


def test_a_local_kilo_run_is_given_its_key_the_variables_its_global_config_names_and_its_login(tmp_path):
    (tmp_path / ".config" / "kilo").mkdir(parents=True)
    (tmp_path / ".config" / "kilo" / "kilo.json").write_text(json.dumps({"provider": {"p": {"options": {"apiKey": "{env:GLOBAL_KEY}"}}}}))
    (tmp_path / ".local" / "share" / "kilo").mkdir(parents=True)
    (tmp_path / ".local" / "share" / "kilo" / "auth.json").write_text(json.dumps({"kilo": {"access": "login-access-value"}}))
    environ = {"HOME": str(tmp_path), "TEAM_KEY": "team-value-12", "GLOBAL_KEY": "global-value-1", "OTHER": "other-value-1"}

    assert evidence.run_secrets("kilo", "local", "TEAM_KEY", environ) == ["global-value-1", "team-value-12", "login-access-value"]
    assert evidence.run_secrets("kilo", "local", None, environ) == ["global-value-1", "login-access-value"]


def test_a_docker_kilo_run_is_given_no_host_secret(tmp_path):
    assert evidence.run_secrets("kilo", "docker", "TEAM_KEY", {"HOME": str(tmp_path), "TEAM_KEY": "team-value-12"}) == []

