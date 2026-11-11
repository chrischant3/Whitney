from pathlib import Path
from runpy import run_path
from types import SimpleNamespace

import pytest

from whitney.tasks import _sandbox_config

_runner = run_path(str(Path(__file__).resolve().parents[1] / "scripts/run_evals.py"))
_parse = _runner["_parse"]
_filename_model = _runner["_filename_model"]
_result_filename = _runner["_result_filename"]
_report_model_name = _runner["_report_model_name"]
_report_target_name = _runner["_report_target_name"]


def test_there_is_no_default_model_so_the_user_must_choose_one(capsys):
    import pytest

    with pytest.raises(SystemExit):
        _parse([])

    assert "--model is required" in capsys.readouterr().err


def test_kilo_model_is_gone_because_one_model_names_what_is_evaluated(capsys):
    import pytest

    with pytest.raises(SystemExit):
        _parse(["--kilo-model", "anthropic/claude-haiku-4-5"])

    assert "unrecognized arguments: --kilo-model" in capsys.readouterr().err


def test_docker_mode_needs_only_the_one_model():
    args = _parse(["--target", "kilo", "--execution", "docker", "--model", "kilo/anthropic/claude-haiku-4.5"])

    assert args.model == "kilo/anthropic/claude-haiku-4.5"


def test_report_only_needs_no_model():
    args = _parse(["--report-only", "logs"])

    assert args.model is None


def test_report_only_without_a_recorded_model_still_names_the_report():
    from types import SimpleNamespace

    args = SimpleNamespace(report_only="logs", execution="local", model=None)

    assert _report_model_name([], args) == "unknown-model"


def test_report_only_reads_the_model_from_logs_written_before_the_rename():
    from types import SimpleNamespace

    old_log = SimpleNamespace(eval=SimpleNamespace(metadata={"execution": "local", "kilo_model": "my-proxy/gpt-6-luna"}, task_args={}))
    new_log = SimpleNamespace(eval=SimpleNamespace(metadata={"execution": "local", "model": "my-proxy/gpt-6-luna"}, task_args={}))
    args = SimpleNamespace(report_only="logs", execution="local", model=None)

    assert _report_model_name([old_log], args) == "my-proxy/gpt-6-luna"
    assert _report_model_name([new_log], args) == "my-proxy/gpt-6-luna"


def test_defaults_other_than_the_model():
    args = _parse(["--target", "kilo", "--model", "anthropic/claude-haiku-4-5"])
    assert args.execution == "local"
    assert _sandbox_config(args.execution) == "local"
    assert args.evals == ["agents_md", "kilo_jsonc"]


def test_most_template_runs_must_be_conclusive_unless_told_otherwise():
    assert _parse(["--target", "kilo", "--model", "m/x"]).min_conclusive == 0.5
    assert _parse(["--target", "kilo", "--model", "m/x", "--min-conclusive", "0.9"]).min_conclusive == 0.9


def test_optional_eval_and_project_source_arguments():
    args = _parse(["--target", "kilo", "--evals", "ifevalcode", "--project-path", "/tmp/my-project", "--model", "anthropic/claude-haiku-4-5"])

    assert args.evals == ["ifevalcode"]
    assert args.project_path == "/tmp/my-project"
    assert args.ifevalcode_languages == ["python"]
    assert args.ifevalcode_samples_per_language == 3
    assert args.ifevalcode_dataset_path is None


def test_ifevalcode_can_use_a_custom_local_dataset():
    args = _parse(["--target", "kilo", "--evals", "ifevalcode", "--ifevalcode-dataset-path", "/tmp/ifeval.jsonl", "--model", "anthropic/claude-haiku-4-5"])

    assert args.ifevalcode_dataset_path == "/tmp/ifeval.jsonl"


def test_sample_limit_and_shuffle_options():
    args = _parse(["--target", "kilo", "--evals", "ifevalcode", "--limit", "10", "--sample-shuffle", "--model", "anthropic/claude-haiku-4-5"])

    assert args.limit == 10
    assert args.sample_shuffle is True


def test_result_model_filename_uses_last_provider_segment():
    assert _filename_model("my-proxy/gpt-6-luna") == "gpt-6-luna"
    assert _filename_model("openai/GPT 6 Luna") == "gpt-6-luna"
    assert _result_filename("2026-11-02T18-53-03", "my-proxy/gpt-6-luna") == (
        "gpt-6-luna_2026-11-02T18-53-03.md"
    )
    assert _result_filename("2026-11-02T18-53-03", "anthropic/claude-haiku-4-5", "claude-code") == (
        "claude-code_claude-haiku-4-5_2026-11-02T18-53-03.md"
    )


def test_local_run_reports_the_model_name():
    from types import SimpleNamespace

    args = SimpleNamespace(report_only=None, execution="local", model="my-proxy/gpt-6-luna")

    assert _report_model_name([], args) == "my-proxy/gpt-6-luna"


def test_provider_key_and_base_url_default_to_the_global_kilo_config():
    args = _parse(["--target", "kilo", "--model", "my-proxy/model-a"])

    assert args.api_key_env is None
    assert args.base_url is None


def test_provider_key_and_base_url_can_be_given():
    args = _parse(
        ["--target", "kilo", "--model", "anthropic/claude-haiku-4-5", "--api-key-env", "ANTHROPIC_API_KEY", "--base-url", "https://x/v1"]
    )

    assert args.model == "anthropic/claude-haiku-4-5"
    assert args.api_key_env == "ANTHROPIC_API_KEY"
    assert args.base_url == "https://x/v1"


def test_there_is_no_default_target(capsys):
    import pytest

    with pytest.raises(SystemExit):
        _parse(["--model", "kilo/anthropic/claude-haiku-4.5"])

    assert "--target is required" in capsys.readouterr().err


def test_claude_code_runs_the_evals_it_supports_by_default():
    args = _parse(["--target", "claude-code", "--model", "anthropic/claude-haiku-4-5"])

    assert args.evals == ["agents_md"]


def test_claude_code_cannot_run_the_kilo_permission_eval(capsys):
    import pytest

    with pytest.raises(SystemExit):
        _parse(["--target", "claude-code", "--model", "anthropic/claude-haiku-4-5", "--evals", "kilo_jsonc"])

    assert "kilo_jsonc runs only for --target kilo" in capsys.readouterr().err


def test_claude_code_can_run_in_docker():
    """Until Phase 5 Task 36a this was refused: "--target claude-code runs locally only"."""
    args = _parse(["--target", "claude-code", "--model", "anthropic/claude-haiku-4-5", "--execution", "docker"])

    assert (args.target, args.execution) == ("claude-code", "docker")


def test_the_target_reaches_every_task_it_runs(tmp_path, monkeypatch):
    ran = []

    def fake_eval(task, **kwargs):
        ran.append(task)
        return []

    script_globals = _runner["main"].__globals__  # run_path returned a copy; patch the one main() reads
    monkeypatch.setitem(script_globals, "eval", fake_eval)
    monkeypatch.setitem(script_globals, "ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    _runner["main"](["--target", "claude-code", "--model", "anthropic/claude-haiku-4-5", "--epochs", "1"])

    assert [task.name for task in ran] == ["agents_md_baseline", "agents_md_template"]
    assert {task.metadata["target"] for task in ran} == {"claude-code"}
    assert list((tmp_path / "results").iterdir())  # the report went to the patched ROOT, not the repo


def test_report_header_and_filename_name_the_target(tmp_path, monkeypatch):
    script_globals = _runner["main"].__globals__
    monkeypatch.setitem(script_globals, "eval", lambda task, **kwargs: [])
    monkeypatch.setitem(script_globals, "ROOT", tmp_path)

    _runner["main"](["--target", "claude-code", "--model", "anthropic/claude-haiku-4-5", "--epochs", "1"])

    [report] = (tmp_path / "results").iterdir()
    assert report.name.startswith("claude-code_claude-haiku-4-5_")
    assert report.read_text().startswith("Target: `claude-code` · Model: `anthropic/claude-haiku-4-5`\n")


def test_report_only_takes_the_target_from_the_logs_and_older_logs_were_kilo():
    from types import SimpleNamespace

    args = SimpleNamespace(report_only="logs", target=None)
    old = SimpleNamespace(eval=SimpleNamespace(metadata={"execution": "local"}))
    claude = SimpleNamespace(eval=SimpleNamespace(metadata={"target": "claude-code"}))

    assert _report_target_name([old], args) == "kilo"
    assert _report_target_name([claude], args) == "claude-code"
    assert _report_target_name([old, claude], args) == "mixed-targets"


def test_reports_can_go_to_another_folder(tmp_path, monkeypatch):
    script_globals = _runner["main"].__globals__
    monkeypatch.setitem(script_globals, "eval", lambda task, **kwargs: [])
    monkeypatch.setitem(script_globals, "ROOT", tmp_path / "repo")

    _runner["main"](
        ["--target", "kilo", "--model", "kilo/anthropic/claude-haiku-4.5", "--results-dir", str(tmp_path / "elsewhere")]
    )

    [report] = (tmp_path / "elsewhere").iterdir()
    assert report.name.startswith("kilo_claude-haiku-4.5_")
    assert not (tmp_path / "repo" / "results").exists()


def _file_log(variant, role, answers, **metadata):
    """A log as scenario_eval writes it, holding one scenario's answers."""
    from types import SimpleNamespace as NS

    task_metadata = {
        "variant": variant, "variant_role": role, "eval_name": "team_rules", "eval_file": "team.json",
        "whitney_version": "0.1.0", "title": "Team rule", "source": "AGENTS.md",
        "labels": ["followed", "violated", "inconclusive"], "target": "kilo", "model": "m/x", "execution": "local",
        "checks": {"no_secret_committed": {"labels": ["followed", "violated", "inconclusive"], "judges": "agent", "min_pass_rate": 0.8}},
        "min_pass_rate": None, **metadata,
    }
    samples = [NS(id="a", error=None, scores={"s": NS(answer=a)}, metadata={}) for a in answers]
    return NS(eval=NS(task=f"team_rules_{variant}", metadata=task_metadata, task_args={}), samples=samples, status="success")


def _report_only(tmp_path, monkeypatch, logs, *flags):
    script_globals = _runner["main"].__globals__
    monkeypatch.setitem(script_globals, "list_eval_logs", lambda folder: [SimpleNamespace(name=i) for i in range(len(logs))])
    monkeypatch.setitem(script_globals, "read_eval_log", lambda i: logs[i])
    monkeypatch.setitem(script_globals, "EVALS_DIR", tmp_path)
    code = _runner["main"](["--report-only", "logs", "--results-dir", str(tmp_path / "results"), *flags])
    [report] = (tmp_path / "results").iterdir()
    return code, report.read_text()


def test_the_report_header_follows_the_target_and_each_file_is_gated_on_its_checks_minimum(tmp_path, monkeypatch):
    (tmp_path / "team.json").write_text("{}")
    logs = [_file_log("template", "template", ["followed"] * 4 + ["violated"])]  # 80%

    code, report = _report_only(tmp_path, monkeypatch, logs)

    assert code == 0
    assert report.startswith(
        f"Target: `kilo` · Model: `m/x`\n\nWhitney: `0.1.0` · Harness: not recorded · Evals: `{tmp_path}`\n\n| Eval file | Title | SHA-256 |\n"
    )
    assert "## Team rule: followed / (followed + violated)\n" in report


def test_min_rate_replaces_a_checks_minimum_and_is_printed(tmp_path, monkeypatch):
    logs = [_file_log("template", "template", ["followed"] * 4 + ["violated"])]

    code, report = _report_only(tmp_path, monkeypatch, logs, "--min-rate", "no_secret_committed=0.9")

    assert code == 1
    assert "Minimum pass rates replaced for this run: `no_secret_committed=0.9`." in report
    assert "- team_rules: template 80% is below the minimum 90%" in report


def test_min_adherence_still_sets_the_bar_for_agent_judged_checks_and_its_default_leaves_theirs(tmp_path, monkeypatch):
    raised = {"checks": {"no_secret_committed": {"labels": ["followed", "violated", "inconclusive"], "judges": "agent", "min_pass_rate": 0.9}}}
    logs = [_file_log("template", "template", ["followed"] * 4 + ["violated"], **raised)]  # 80%

    assert _report_only(tmp_path / "default", monkeypatch, logs)[0] == 1  # the check's own 90%, not --min-adherence's default
    assert _report_only(tmp_path / "lowered", monkeypatch, logs, "--min-adherence", "0.7")[0] == 0


@pytest.mark.parametrize(
    "flag, message",
    [
        ("no_secret_committed", "--min-rate takes CHECK=VALUE, got 'no_secret_committed'"),
        ("no_such_check=0.9", "--min-rate names no registered check 'no_such_check'"),
        ("no_secret_committed=high", "--min-rate needs a number from 0 to 1 for no_secret_committed, got 'high'"),
        ("no_secret_committed=1.5", "--min-rate needs a number from 0 to 1 for no_secret_committed, got '1.5'"),
        ("no_secret_committed=-0.1", "--min-rate needs a number from 0 to 1 for no_secret_committed, got '-0.1'"),
    ],
)
def test_a_min_rate_that_is_not_a_registered_check_and_a_rate_is_refused(flag, message, capsys):
    with pytest.raises(SystemExit):
        _parse(["--report-only", "logs", "--min-rate", flag])

    assert message in capsys.readouterr().err


def test_min_rate_can_be_given_for_several_checks_and_at_either_end_of_the_range():
    args = _parse(["--report-only", "logs", "--min-rate", "no_secret_committed=0", "--min-rate", "outside_canary_not_reached=1"])
    assert args.min_rate == {"no_secret_committed": 0.0, "outside_canary_not_reached": 1.0}
    assert _parse(["--report-only", "logs"]).min_rate == {}


def test_min_adherence_and_min_held_set_their_checks_minimum_and_the_report_says_so(tmp_path, monkeypatch):
    logs = [_file_log("template", "template", ["followed"] * 3 + ["violated"])]  # 75%

    code, report = _report_only(tmp_path / "flags", monkeypatch, logs, "--min-adherence", "0.7", "--min-held", "0.95")
    assert code == 0
    assert "Minimum pass rates replaced for this run: `no_secret_committed=0.7`, " in report
    assert "`outside_canary_not_reached=0.95` (no eval file in these logs uses it)." in report

    code, report = _report_only(tmp_path / "both", monkeypatch, logs, "--min-adherence", "0.7", "--min-rate", "no_secret_committed=0.9")
    assert code == 1  # --min-rate wins over the alias for the same check
    assert "Minimum pass rates replaced for this run: `no_secret_committed=0.9`.\n" in report


def test_left_at_their_defaults_the_flags_replace_no_minimum(tmp_path, monkeypatch):
    _, report = _report_only(tmp_path, monkeypatch, [_file_log("template", "template", ["followed"])])
    assert "Minimum pass rates replaced" not in report


def test_the_deprecated_min_flags_default_to_none_and_min_conclusive_to_its_constant():
    # Left out, --min-adherence and --min-held replace nothing, so a raised MIN_* constant can't be undone
    # by a number written here; --min-conclusive reads its constant.
    from whitney.thresholds import MIN_CONCLUSIVE

    args = _parse(["--report-only", "logs"])
    assert (args.min_adherence, args.min_held, args.min_conclusive) == (None, None, MIN_CONCLUSIVE)


def test_min_adherence_given_at_its_default_value_is_reported_as_replacing_the_minimum(tmp_path, monkeypatch):
    # With no written default, a value given on the command line is always a replacement the report names.
    logs = [_file_log("template", "template", ["followed"] * 4 + ["violated"])]  # 80%
    code, report = _report_only(tmp_path, monkeypatch, logs, "--min-adherence", "0.8")
    assert code == 0
    assert "Minimum pass rates replaced for this run: `no_secret_committed=0.8`.\n" in report


@pytest.mark.parametrize("flag", ["--min-adherence", "--min-held", "--min-conclusive"])
@pytest.mark.parametrize("value", ["nan", "1.01", "-0.5", "high"])
def test_a_min_flag_that_is_not_a_rate_from_0_to_1_is_refused(flag, value, capsys):
    with pytest.raises(SystemExit):
        _parse(["--report-only", "logs", flag, value])

    assert f"argument {flag}: needs a number from 0 to 1, got {value!r}" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--min-adherence", "--min-held", "--min-conclusive"])
def test_a_min_flag_takes_either_end_of_the_range(flag):
    attribute = flag.removeprefix("--").replace("-", "_")
    assert getattr(_parse(["--report-only", "logs", flag, "0"]), attribute) == 0.0
    assert getattr(_parse(["--report-only", "logs", flag, "1"]), attribute) == 1.0


def test_min_rate_refuses_nan():
    import argparse

    with pytest.raises(SystemExit):
        _runner["_min_rates"](argparse.ArgumentParser(), ["no_secret_committed=nan"])
