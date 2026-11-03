import shutil
from pathlib import Path

import pytest

from whitney.parity import compare_resolved, resolve_config

TEMPLATE = Path(__file__).parent.parent / "template" / "kilo"


def test_identical_configs_have_no_differences():
    cfg = {"permission": {"external_directory": "ask"}, "instructions": ["AGENTS.md"], "model": "x"}
    assert compare_resolved(cfg, dict(cfg)) == []


def test_permission_difference_is_reported():
    cli = {"permission": {"external_directory": "ask"}}
    ext = {"permission": {"external_directory": "allow"}}
    [difference] = compare_resolved(cli, ext)
    assert "permission" in difference and "allow" in difference


def test_missing_instructions_on_one_side_is_reported():
    assert compare_resolved({"instructions": ["team.md"]}, {})


def test_keys_outside_the_compared_set_are_ignored():
    # e.g. provider settings differ legitimately between the two surfaces
    assert compare_resolved({"provider": {"a": 1}}, {"provider": {"b": 2}}) == []


@pytest.mark.skipif(shutil.which("kilo") is None, reason="Kilo CLI not installed")
def test_cli_resolves_the_shipped_template(tmp_path):
    resolved = resolve_config(shutil.which("kilo"), TEMPLATE, tmp_path)
    assert resolved["permission"]["external_directory"] == "ask"


FAKE_KILO = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "  {version}  "; exit 0; fi
if [ "$*" = "debug config --pure" ]; then
  [ -f AGENTS.md ] && agents=true || agents=false
  echo "INFO resolving config"
  printf '{{"permission": {permission}, "instructions": ["AGENTS.md"], "home": "%s", "config_home": "%s", "data_home": "%s", "path": "%s", "cwd": "%s", "has_agents": %s}}' \\
    "$HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$PATH" "$PWD" "$agents"
  exit 0
fi
echo "unexpected arguments: $*" >&2
exit 3
"""


def _fake_kilo(directory, version="7.8.18", permission='{"external_directory": "ask"}'):
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / "kilo"
    binary.write_text(FAKE_KILO.format(version=version, permission=permission))
    binary.chmod(0o755)
    return str(binary)


@pytest.fixture
def template(tmp_path):
    directory = tmp_path / "template"
    directory.mkdir()
    (directory / "AGENTS.md").write_text("- Never commit .env files or secrets\n")
    return directory


def test_resolve_config_runs_in_a_copy_of_the_template_with_an_empty_home(tmp_path, template):
    binary = _fake_kilo(tmp_path / "bin")
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    resolved = resolve_config(binary, template, scratch)

    assert resolved["has_agents"] is True
    assert resolved["cwd"] == str(scratch / "project")
    assert resolved["home"] == str(scratch / "home")
    assert resolved["config_home"] == str(scratch / "home" / ".config")
    assert resolved["data_home"] == str(scratch / "home" / ".local/share")
    assert resolved["path"].split(":")[0] == str(tmp_path / "bin")
    assert (scratch / "home").is_dir()
    assert (scratch / "project" / "AGENTS.md").is_file()


def test_resolve_config_failure_carries_kilo_stderr(tmp_path, template):
    from whitney import parity

    binary = tmp_path / "broken"
    binary.write_text("#!/bin/sh\necho 'config is invalid' >&2\nexit 2\n")
    binary.chmod(0o755)
    with pytest.raises(RuntimeError, match=f"{binary} debug config failed: config is invalid"):
        parity.resolve_config(str(binary), template, tmp_path)


def test_runtime_version_is_stripped(tmp_path):
    from whitney.parity import runtime_version

    assert runtime_version(_fake_kilo(tmp_path / "bin", version="7.8.18")) == "7.8.18"


def test_difference_message_shows_both_values_as_json():
    assert compare_resolved({"instructions": ["a.md"]}, {"instructions": None}) == [
        'instructions: CLI=["a.md"] other=null'
    ]


def test_main_reports_parity_when_both_runtimes_agree(tmp_path, template, capsys):
    from whitney.parity import main

    cli, other = _fake_kilo(tmp_path / "cli"), _fake_kilo(tmp_path / "ext")

    assert main(["--cli", cli, "--other", other, "--template", str(template)]) == 0
    assert capsys.readouterr().out.splitlines() == ["versions: CLI 7.8.18, other 7.8.18", "parity OK"]


def test_main_lists_every_difference_and_fails(tmp_path, template, capsys):
    from whitney.parity import main

    cli = _fake_kilo(tmp_path / "cli", version="7.8.18")
    other = _fake_kilo(tmp_path / "ext", version="7.9.0", permission='{"external_directory": "allow"}')

    assert main(["--cli", cli, "--other", other, "--template", str(template)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "versions: CLI 7.8.18, other 7.9.0",
        "runtime versions differ: {'CLI': '7.8.18', 'other': '7.9.0'}",
        'permission: CLI={"external_directory": "ask"} other={"external_directory": "allow"}',
        "2 difference(s)",
    ]


def test_main_requires_both_runtimes(capsys):
    from whitney.parity import main

    with pytest.raises(SystemExit):
        main(["--cli", "kilo"])
    assert "--other" in capsys.readouterr().err


def test_main_requires_the_cli_too(capsys):
    from whitney.parity import main

    with pytest.raises(SystemExit):
        main(["--other", "kilo"])
    assert "--cli" in capsys.readouterr().err


def test_main_uses_the_template_folder_by_default(tmp_path, template, monkeypatch, capsys):
    from whitney.parity import main

    cli, other = _fake_kilo(tmp_path / "cli"), _fake_kilo(tmp_path / "ext")
    moved = tmp_path / "moved"
    template.rename(moved)
    (tmp_path / "template").mkdir()
    moved.rename(tmp_path / "template" / "kilo")  # the default is the Kilo target's template
    monkeypatch.chdir(tmp_path)

    assert main(["--cli", cli, "--other", other]) == 0


def test_runtime_sees_its_own_folder_then_the_standard_system_paths(tmp_path, template):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    resolved = resolve_config(_fake_kilo(tmp_path / "bin"), template, scratch)
    assert resolved["path"].split(":") == [str(tmp_path / "bin"), "/usr/local/bin", "/usr/bin", "/bin", "/opt/homebrew/bin"]


def test_help_describes_the_check_and_both_runtimes(capsys):
    from whitney.parity import main

    with pytest.raises(SystemExit):
        main(["--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "Check that two Kilo runtimes (e.g. the CLI and the VS Code extension's bundled runtime) resolve the template the same way." in text
    assert "path to the Kilo CLI binary" in text
    assert "manual check" not in text  # only the docstring's first line, not the whole module docs
    assert "path to the other runtime, e.g. the extension's bundled kilo" in text


def test_a_runtime_that_hangs_is_stopped(tmp_path, template, monkeypatch):
    import subprocess

    from whitney import parity

    binary = tmp_path / "hangs"
    binary.write_text("#!/bin/sh\nsleep 5\n")
    binary.chmod(0o755)
    monkeypatch.setattr(parity, "DEBUG_CONFIG_TIMEOUT", 0.5)
    monkeypatch.setattr(parity, "VERSION_TIMEOUT", 0.5)

    with pytest.raises(subprocess.TimeoutExpired):
        parity.resolve_config(str(binary), template, tmp_path)
    with pytest.raises(subprocess.TimeoutExpired):
        parity.runtime_version(str(binary))


def test_a_failure_quotes_the_last_500_characters_of_stderr(tmp_path, template):
    from whitney import parity

    binary = tmp_path / "broken"
    binary.write_text("#!/bin/sh\nprintf '%0100d' 0 >&2\nprintf 'y%.0s' $(seq 500) >&2\nexit 2\n")
    binary.chmod(0o755)
    with pytest.raises(RuntimeError) as raised:
        parity.resolve_config(str(binary), template, tmp_path)
    assert str(raised.value) == f"{binary} debug config failed: {'y' * 500}"


def test_the_default_template_is_the_shipped_one_wherever_it_runs(tmp_path, monkeypatch, capsys):
    from whitney.parity import main

    cli = _fake_kilo(tmp_path / "cli")
    monkeypatch.chdir(tmp_path)  # "template/kilo" relative to here doesn't exist

    assert main(["--cli", cli, "--other", cli]) == 0


def test_help_describes_each_option(capsys, monkeypatch):
    import re

    from whitney.parity import main

    monkeypatch.setenv("COLUMNS", "200")  # one line per option
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "\nCheck that two Kilo runtimes" in out
    assert re.search(r"--cli CLI +path to the Kilo CLI binary\n", out)
    assert re.search(r"--other OTHER +path to the other runtime, e.g. the extension's bundled kilo\n", out)
    assert re.search(r"--template TEMPLATE +the template to resolve \(default: the shipped Kilo template\)\n", out)
