"""Remove the Chinese question column and audit local IFEvalCode text for injection markers.

This script reads the already-downloaded Arrow file directly with PyArrow. It
does not import Hugging Face libraries, execute dataset code, or make network
requests. The original Arrow source is left unchanged.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from whitney.prompt_injection import scan_record  # the IFEvalCode task scans the rows it loads with the same checks

if TYPE_CHECKING:
    import pyarrow as pa

ROOT = Path(__file__).resolve().parents[1]
DATASET_REVISION = "7d2918680c806a235beed1224b2dc726a6771021"
DEFAULT_INPUT = (
    ROOT
    / "dataset/Multilingual-Multimodal-NLP-if-eval-code/default/0.0.0"
    / DATASET_REVISION
    / "if_eval_code-testset-train.arrow"
)
DEFAULT_OUTPUT = ROOT / "dataset/ifevalcode_cleaned.jsonl"
DEFAULT_REPORT = ROOT / "dataset/ifevalcode_prompt_injection_audit.md"

CHINESE_QUESTION_FIELD = "chinese_question"

def read_arrow(path: Path) -> pa.Table:
    """Read either Arrow IPC stream or file format without using a Hub loader."""
    # Imported here, not at the top, so report rendering works without the optional pyarrow.
    import pyarrow as pa
    import pyarrow.ipc as ipc

    with pa.memory_map(str(path), "r") as source:
        try:
            return ipc.open_stream(source).read_all()
        except pa.ArrowInvalid:
            source.seek(0)
            return ipc.open_file(source).read_all()


def _display_path(path: Path) -> str:
    """The path relative to the repo, or just its name if outside it.

    The report is committed, so it must not reveal where the repo or the
    user's home folder is on the machine that ran the audit.
    """
    try:
        return f"`{Path(path).resolve().relative_to(ROOT)}`"
    except ValueError:
        return f"`{Path(path).name}` (outside the repository)"


def render_report(
    source: Path,
    output: Path,
    source_rows: int,
    findings: list[dict[str, str]],
) -> str:
    categories = Counter(item["category"] for item in findings)
    lines = [
        "# IFEvalCode Dataset Security Audit",
        "",
        "## Processing",
        f"- Source: {_display_path(source)}",
        f"- Sanitised copy: {_display_path(output)}",
        f"- Rows retained: {source_rows}; rows removed: 0",
        f"- Removed field: `{CHINESE_QUESTION_FIELD}` (the Chinese-language companion question)",
        "- The original Arrow file was not modified.",
        "- All text fields (including correctness/instruction checks and the removed Chinese question) were scanned as data.",
        "- No dataset-provided code was executed.",
        "",
        "## Prompt-Injection Scan",
        "The scan uses high-signal text patterns. It can flag benign quoted examples and cannot prove a dataset is safe.",
        f"- Findings: {len(findings)}",
    ]
    if categories:
        lines.extend(f"- `{name}`: {count}" for name, count in sorted(categories.items()))
        lines.extend(
            [
                "",
                "### Findings",
                "",
                "| Row ID | Language | Field | Category | Severity | Matched evidence | Context |",
                "|---:|---|---|---|---|---|---|",
            ]
        )
        for item in findings:
            matched = item["matched"].replace("|", "\\|")
            snippet = item["snippet"].replace("|", "\\|")
            lines.append(
                f"| {item['row_id']} | {item['language']} | `{item['field']}` | "
                f"`{item['category']}` | {item['severity']} | {matched} | {snippet} |"
            )
    else:
        lines.append("- No configured prompt-injection indicators were detected.")
    return "\n".join(lines) + "\n"


def audit_dataset(input_path: Path, output_path: Path, report_path: Path) -> list[dict[str, str]]:
    if not input_path.is_file():
        raise FileNotFoundError(f"IFEvalCode Arrow dataset not found: {input_path}")

    table = read_arrow(input_path)
    required = {"id", "english_question", CHINESE_QUESTION_FIELD}
    missing = required.difference(table.column_names)
    if missing:
        raise ValueError(f"Dataset is missing required columns: {sorted(missing)}")

    cleaned_rows = []
    findings = []
    for record in table.to_pylist():
        row_id = str(record.get("id", "<missing>"))
        language = str(record.get("programming_language", "unknown"))
        for finding in scan_record(record):
            findings.append({"row_id": row_id, "language": language, **finding})
        record.pop(CHINESE_QUESTION_FIELD)
        cleaned_rows.append(record)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary_output.open("w", encoding="utf-8", newline="\n") as output:
        for record in cleaned_rows:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
    temporary_output.replace(output_path)

    report = render_report(input_path, output_path, len(cleaned_rows), findings)
    report_path.write_text(report, encoding="utf-8")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="local Arrow IPC dataset file")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="sanitised JSONL output path")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="Markdown security report path")
    parser.add_argument("--fail-on-findings", action="store_true", help="exit 2 if any injection indicators are found")
    args = parser.parse_args()

    findings = audit_dataset(args.input, args.output, args.report)
    print(f"Sanitised dataset: {args.output}")
    print(f"Audit report: {args.report}")
    print(f"Prompt-injection findings: {len(findings)}")
    return 2 if args.fail_on_findings and findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
