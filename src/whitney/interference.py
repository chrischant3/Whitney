"""IFEvalCode as an instruction-interference check using sanitized local data."""

import json
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample

from whitney import targets
from whitney.project_files import load_project_files, load_template_files
from whitney.tasks import AGENTS_MD_PERMISSIONS

ROOT = Path(__file__).resolve().parents[2]
LOCAL_DATASET = ROOT / "dataset/ifevalcode_cleaned.jsonl"
SUPPORTED_LANGUAGES = ("cpp", "csharp", "java", "javascript", "php", "python", "shell", "typescript")
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
    sanitized JSONL output and never contacts Hugging Face. target picks the
    harness and template, and api_key_env and base_url the provider key, as
    in whitney.tasks.agents_md.
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
            f"Sanitized local IFEvalCode data not found: {dataset_file}. "
            "Run `python scripts/audit_ifevalcode_dataset.py` first."
        )

    try:
        from inspect_evals.ifevalcode.ifevalcode import COMPOSE_FILE
        from inspect_evals.ifevalcode.scorer import ifevalcode_scorer
    except ImportError as error:
        raise RuntimeError('Install the optional extra with `pip install -e ".[ifevalcode]"`.') from error

    records = _load_records(dataset_file, selected_languages, samples_per_language)
    if not records:
        raise ValueError("No local IFEvalCode samples match the selected languages")

    project_files = load_project_files(project_path) if project_path else load_template_files(targets.template_dir(target))
    if variant == "baseline":
        project_files.pop("AGENTS.md", None)

    samples = []
    for record in records:
        language = record["programming_language"]
        prompt = IFEVALCODE_SYSTEM_PROMPT.format(language=language) + "\n\n" + record["english_question"]
        samples.append(
            Sample(
                id=record["id"],
                input=prompt,
                metadata={
                    "check_correctness": record.get("check_correctness", ""),
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
            AGENTS_MD_PERMISSIONS,
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
            "harness_permissions": AGENTS_MD_PERMISSIONS[target],
            "evaluation_preamble": IFEVALCODE_SYSTEM_PROMPT,
            "dataset_source": "local sanitized IFEvalCode JSONL",
            "chinese_question_column_removed": True,
            "candidate_records": len(records),
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


def _load_records(path: Path, languages: set[str], limit: int | None) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if "chinese_question" in record:
                raise ValueError(f"Chinese question column remains in {path}, row {line_number}")
            if "english_question" not in record or "programming_language" not in record:
                raise ValueError(f"Required IFEvalCode fields missing from row {line_number}")
            if str(record["programming_language"]).lower() in languages:
                records.append(record)

    records.sort(key=lambda record: str(record["programming_language"]).lower())
    if limit is None:
        return records

    selected = []
    counts: dict[str, int] = {}
    for record in records:
        language = str(record["programming_language"]).lower()
        if counts.get(language, 0) >= limit:
            continue
        counts[language] = counts.get(language, 0) + 1
        selected.append(record)
    return selected
