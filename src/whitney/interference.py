"""IFEvalCode as an instruction-interference check using sanitised local data."""

import ast
import codecs
import json
import re
import warnings
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample

from whitney import shipped_evals, targets
from whitney.harbor_tasks import HARBOR_PERMISSIONS
from whitney.project_files import (
    files_not_staged,
    files_not_staged_reasons,
    files_renamed,
    load_project_files_for,
    load_template_files,
    without_instructions,
)
from whitney.prompt_injection import scan_record

ROOT = Path(__file__).resolve().parents[2]
LOCAL_DATASET = ROOT / "dataset/ifevalcode_cleaned.jsonl"
# IFEvalCode is scored from the agent's final message, so it needs no shell,
# and every prompt comes from a downloaded dataset: an injected row could
# otherwise run commands on the host. The same grants as the Harbor eval.
IFEVALCODE_PERMISSIONS = HARBOR_PERMISSIONS
SUPPORTED_LANGUAGES = ("cpp", "csharp", "java", "javascript", "php", "python", "shell", "typescript")
# The languages whose check code inspect_evals 0.27.0 decodes with unicode_escape (scorer_check_code).
UPSTREAM_DECODES = frozenset({"python", "cpp", "java", "csharp"})
_BARE_QUOTE = re.compile(r'(?<!\\)"')
IFEVALCODE_SYSTEM_PROMPT = """You are an expert programmer. You will be given a task description with specific constraints and requirements. Write code in {language} that solves the problem while adhering to all the specified constraints.

Provide your solution in a markdown code block with the appropriate language tag."""


@task
def ifevalcode(
    variant: str = "template",
    *,
    target: str,
    languages: str | list[str] | None = "python",
    samples_per_language: int | None = 3,
    model: str,
    project_path: str | None = None,
    dataset_path: str | None = None,
    api_key_env: str | None = None,
    base_url: str | None = None,
) -> Task:
    """Compare baseline/template with the official IFEvalCode scorer on local data.

    The local audit script removes the Chinese question column and checks all
    text fields for prompt-injection indicators. This task reads only its
    sanitised JSONL output and never contacts Hugging Face. It scans every
    row it loads with the same checks and leaves flagged rows out, recording
    their ids as injection_scan_excluded, so data that skipped the audit
    can't reach the agent either. The agent gets no shell
    (IFEVALCODE_PERMISSIONS). target picks the harness and template,
    project_path a project to read instead with that target's loader, and
    api_key_env and base_url the provider key, as in whitney.tasks.agents_md.
    """
    if variant not in {"baseline", "template"}:
        raise ValueError("variant must be 'baseline' or 'template'")
    targets.check_target(target, base_url=base_url)
    if samples_per_language is not None and samples_per_language < 1:
        raise ValueError("samples_per_language must be positive")

    selected_languages = _normalise_languages(languages)
    dataset_file = Path(dataset_path).expanduser() if dataset_path else LOCAL_DATASET
    if not dataset_file.is_file():
        raise FileNotFoundError(
            f"Sanitised local IFEvalCode data not found: {dataset_file}. "
            "Run `python scripts/audit_ifevalcode_dataset.py` first."
        )

    try:
        from inspect_evals.ifevalcode.ifevalcode import COMPOSE_FILE
        from inspect_evals.ifevalcode.scorer import ifevalcode_scorer
    except ImportError as error:
        raise RuntimeError('Install the optional extra with `pip install -e ".[ifevalcode]"`.') from error

    records, excluded = _load_records(dataset_file, selected_languages, samples_per_language)
    if not records:
        raise ValueError("No local IFEvalCode samples match the selected languages")

    project_files = (
        load_project_files_for(target, project_path, evals_path=shipped_evals.EVALS_DIR)
        if project_path
        else load_template_files(targets.template_dir(target))
    )
    if variant == "baseline":
        project_files = without_instructions(target, project_files)

    samples = []
    for record in records:
        language = record["programming_language"]
        prompt = IFEVALCODE_SYSTEM_PROMPT.format(language=language) + "\n\n" + record["english_question"]
        samples.append(
            Sample(
                id=record["id"],
                input=prompt,
                metadata={
                    "check_correctness": scorer_check_code(language, record.get("check_correctness", "")),
                    "check_correctness_as_written": record.get("check_correctness", ""),
                    "check_instruction": record.get("check_instruction", ""),
                    "entry_point": record.get("entry_point", []),
                    "programming_language": language,
                    "instruction_types": record.get("instruction_types", []),
                    "domain_types": record.get("domain_types", []),
                },
            )
        )

    return Task(
        name=f"ifevalcode_{variant}",
        dataset=MemoryDataset(samples, name=f"ifevalcode-local-{variant}"),
        solver=targets.build_agent(
            target,
            model,
            IFEVALCODE_PERMISSIONS,
            local_project_files=project_files,
            api_key_env=api_key_env,
            base_url=base_url,
        ),
        scorer=ifevalcode_scorer(),
        sandbox=("docker", str(COMPOSE_FILE)),
        metadata={
            "variant": variant,
            "target": target,
            "execution": "local",
            "model": model,
            "model_access": targets.model_access(target, api_key_env, base_url),
            "asset_source": "local project" if project_path else "template",
            "evaluation_assets": project_files,
            "renamed": files_renamed(target, project_path, project_files) if project_path else {},
            "not_staged": files_not_staged(target, project_path, evals_path=shipped_evals.EVALS_DIR) if project_path else [],
            "not_staged_reasons": files_not_staged_reasons(target, project_path, evals_path=shipped_evals.EVALS_DIR) if project_path else {},
            "harness_permissions": IFEVALCODE_PERMISSIONS[target],
            "evaluation_preamble": IFEVALCODE_SYSTEM_PROMPT,
            "dataset_source": "local sanitised IFEvalCode JSONL",
            "chinese_question_column_removed": True,
            "candidate_records": len(records),
            "injection_scan_excluded": excluded,
        },
    )


def _normalise_languages(languages: str | list[str] | None) -> set[str]:
    if languages is None:
        selected = set(SUPPORTED_LANGUAGES)
    elif isinstance(languages, str):
        selected = {languages.lower()}
    else:
        selected = {language.lower() for language in languages}
    unsupported = selected.difference(SUPPORTED_LANGUAGES)
    if unsupported:
        raise ValueError(f"Unsupported IFEvalCode languages: {sorted(unsupported)}")
    return selected


def _load_records(path: Path, languages: set[str], limit: int | None) -> tuple[list[dict], list[str]]:
    """Rows in `languages`, at most `limit` per language, and the ids of rows the injection scan left out."""
    found: list[tuple[str, dict]] = []  # (language, record), the language lower-cased once
    excluded = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if "chinese_question" in record:
                raise ValueError(f"Chinese question column remains in {path}, row {line_number}")
            if "english_question" not in record or "programming_language" not in record:
                raise ValueError(f"Required IFEvalCode fields missing from row {line_number}")
            language = str(record["programming_language"]).lower()
            if language not in languages:
                continue
            if scan_record(record):
                excluded.append(str(record.get("id")))
            else:
                found.append((language, record))

    found.sort(key=lambda item: item[0])
    counts: dict[str, int] = {}
    selected = []
    for language, record in found:
        if limit is not None and counts.get(language, 0) >= limit:
            continue
        counts[language] = counts.get(language, 0) + 1
        selected.append(record)
    return selected, excluded


def scorer_check_code(language: str, check_code: str) -> str:
    """The check code to give the upstream scorer so the sandbox gets the row's code.

    inspect_evals 0.27.0's post-processors for UPSTREAM_DECODES languages run
    the check code through `unicode_escape` before writing it to the sandbox.
    That repairs rows the dataset over-escaped (`\\"` where `"` was meant) but
    breaks rows already valid as written (row 505's `'<>&\\"\\''` loses its
    backslashes), turns non-ASCII text into mojibake and drops backslash-newline
    continuations. So Whitney decodes only an over-escaped row, itself and
    without the mojibake, then escapes the result so upstream's decode gives it
    back unchanged.
    """
    language = language.lower()
    if language not in UPSTREAM_DECODES:
        return check_code
    decoded = _decode_escapes(check_code)
    if decoded is not None and _over_escaped(language, check_code, decoded):
        check_code = decoded
    return _survives_upstream_decode(check_code)


def _over_escaped(language: str, as_written: str, decoded: str) -> bool:
    """Whether the row only makes sense once its escapes are decoded.

    Python: it doesn't parse as written and does once decoded. The C family
    writes strings only in double quotes, so a row holding `\\"` and no bare
    `"` has no string the `\\"` could sit in.
    """
    if language == "python":
        return not _parses(as_written) and _parses(decoded)
    return '\\"' in as_written and not _BARE_QUOTE.search(as_written)


def _parses(source: str) -> bool:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)  # invalid escapes such as "\\d" only warn
        try:
            ast.parse(source)
        except (SyntaxError, ValueError):
            return False
    return True


def _decode_escapes(text: str) -> str | None:
    """Escape sequences decoded and every other character kept, or None if an escape is malformed."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)  # an unknown escape such as "\\d" is kept, with a warning
        try:
            return codecs.unicode_escape_decode(codecs.latin_1_encode(text, "backslashreplace")[0])[0]
        except UnicodeDecodeError:
            return None


def _survives_upstream_decode(text: str) -> str:
    """The ASCII text upstream's `.encode("utf-8").decode("unicode_escape")` turns back into text."""
    return codecs.ascii_encode(text.replace("\\", "\\\\"), "backslashreplace")[0].decode()
