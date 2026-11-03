"""Fetch the pinned Harbor BigCodeBench-Hard package once, cache it locally, and audit text.

The first invocation contacts Harbor Hub to resolve/download the package. Later
invocations reuse the project-local copy and perform no Hub calls. No Harbor
task, Docker image, verifier, or model is run by this script.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from whitney.prompt_injection import scan_text

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "bigcode/bigcodebench-hard-complete"
PACKAGE_REF = "sha256:4c881f46251c98f6af182ec8eedbacc2c144db0761fcbbd789a60d7e69c30f69"
DEFAULT_DATASET_DIR = ROOT / "dataset/harbor/bigcodebench-hard-complete"
DEFAULT_REPORT = ROOT / "results/bigcodebench-hard-complete-security-audit.md"
MANIFEST_NAME = "inspect-harbor-manifest.json"
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache"}
MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_ -]?key|token|password|secret|credential)\b\s*[:=]\s*[\"']?([^\s\"',;}]+)"
)
BEARER_VALUE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-_") or "task"


async def _fetch_package() -> tuple[object, list[Path]]:
    """Resolve and download the pinned package; this is the only Hub call path."""
    from inspect_harbor._harbor.hub import (
        HubClient,
        download_hub_tasks,
        resolve_hub_dataset,
    )

    client = HubClient()
    try:
        metadata = await resolve_hub_dataset(f"{PACKAGE_NAME}@{PACKAGE_REF}", client=client)
        paths = await download_hub_tasks(metadata.task_refs, client=client)
        return metadata, paths
    finally:
        await client.aclose()


def ensure_local_dataset(dataset_dir: Path) -> dict:
    """Use a local manifest if present; otherwise perform one pinned Hub sync."""
    manifest_path = dataset_dir / MANIFEST_NAME
    if dataset_dir.exists():
        if not manifest_path.is_file():
            raise FileExistsError(
                f"{dataset_dir} exists without {MANIFEST_NAME}; use --audit-only to inspect it, "
                "or move it before downloading the pinned package."
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("package") != PACKAGE_NAME or manifest.get("requested_ref") != PACKAGE_REF:
            raise ValueError(f"Local Harbor cache manifest does not match the requested package: {manifest_path}")
        return manifest

    dataset_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".bigcodebench-harbor-", dir=dataset_dir.parent))
    try:
        metadata, task_paths = asyncio.run(_fetch_package())
        if not task_paths:
            raise ValueError("Harbor Hub returned no tasks for BigCodeBench-Hard Complete")
        for index, task_path in enumerate(task_paths):
            destination = staging / f"{index:05d}_{_safe_name(task_path.name)}"
            shutil.copytree(task_path, destination, symlinks=True)

        manifest = {
            "package": PACKAGE_NAME,
            "requested_ref": PACKAGE_REF,
            "resolved_version": getattr(metadata, "version", None),
            "task_count": len(task_paths),
            "cached_at_utc": datetime.now(timezone.utc).isoformat(),
            "task_directories": [path.name for path in task_paths],
        }
        (staging / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        os.replace(staging, dataset_dir)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _task_directories(dataset_dir: Path) -> list[Path]:
    if (dataset_dir / "task.toml").is_file():
        return [dataset_dir]
    return sorted(path for path in dataset_dir.iterdir() if path.is_dir() and (path / "task.toml").is_file())


def _text_files(task_dir: Path) -> Iterator[Path]:
    for current, directories, filenames in os.walk(task_dir, followlinks=False):
        directories[:] = [
            name for name in directories
            if name not in SKIP_DIRS and not (Path(current) / name).is_symlink()
        ]
        for name in filenames:
            path = Path(current) / name
            if path.is_symlink():
                continue
            yield path


def _redact(text: str) -> str:
    text = SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    return BEARER_VALUE.sub("Bearer <redacted>", text)


def audit_dataset(dataset_dir: Path) -> tuple[int, list[dict[str, str]], int, int]:
    task_dirs = _task_directories(dataset_dir)
    if not task_dirs:
        raise ValueError(f"No Harbor task directories found under {dataset_dir}")

    findings = []
    files_scanned = 0
    files_skipped = 0
    for task_dir in task_dirs:
        for path in _text_files(task_dir):
            if path.stat().st_size > MAX_TEXT_FILE_BYTES:
                files_skipped += 1
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                files_skipped += 1
                continue
            files_scanned += 1
            for finding in scan_text(text):
                findings.append(
                    {
                        "task": task_dir.name,
                        "file": path.relative_to(dataset_dir).as_posix(),
                        **finding,
                        "snippet": _redact(finding["snippet"]),
                        "matched": _redact(finding["matched"]),
                    }
                )
    return len(task_dirs), findings, files_scanned, files_skipped


def write_report(
    report_path: Path,
    dataset_dir: Path,
    manifest: dict | None,
    task_count: int,
    findings: list[dict[str, str]],
    files_scanned: int,
    files_skipped: int,
) -> None:
    lines = [
        "# BigCodeBench-Hard Complete Harbor Dataset Security Audit",
        "",
        f"- Local dataset directory: `{dataset_dir}`",
        f"- Tasks scanned: {task_count}",
        f"- Text files scanned: {files_scanned}",
        f"- Non-UTF8/unreadable/oversized files skipped: {files_skipped}",
        f"- Prompt-injection indicators: {len(findings)}",
        "- Dataset task instructions, verifier scripts, and source text were inspected as text only; none were executed.",
        "- Secret-like values in finding snippets are redacted.",
    ]
    if manifest:
        lines.extend(
            [
                f"- Harbor package: `{manifest.get('package')}`",
                f"- Requested ref: `{manifest.get('requested_ref')}`",
                f"- Resolved dataset version: `{manifest.get('resolved_version')}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Findings",
            "",
            "The detector is heuristic; findings need human review and an empty report does not prove safety.",
        ]
    )
    if findings:
        lines.extend(
            [
                "",
                "| Task directory | File | Indicator | Severity | Matched evidence | Context |",
                "|---|---|---|---|---|---|",
            ]
        )
        for finding in findings:
            task = finding["task"].replace("|", "\\|")
            file = finding["file"].replace("|", "\\|")
            matched = finding["matched"].replace("|", "\\|")
            snippet = finding["snippet"].replace("|", "\\|")
            lines.append(
                f"| `{task}` | `{file}` | `{finding['category']}` | {finding['severity']} | "
                f"{matched} | {snippet} |"
            )
    else:
        lines.append("\nNo configured prompt-injection indicators were found in the scanned text.")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--audit-only", action="store_true", help="scan an existing local Harbor task directory without Hub access")
    parser.add_argument("--fail-on-findings", action="store_true")
    args = parser.parse_args()

    manifest = None
    if not args.audit_only:
        manifest = ensure_local_dataset(args.dataset_dir)
    elif (args.dataset_dir / MANIFEST_NAME).is_file():
        manifest = json.loads((args.dataset_dir / MANIFEST_NAME).read_text(encoding="utf-8"))

    task_count, findings, files_scanned, files_skipped = audit_dataset(args.dataset_dir)
    write_report(args.report, args.dataset_dir, manifest, task_count, findings, files_scanned, files_skipped)
    print(f"Local Harbor task dataset: {args.dataset_dir}")
    print(f"Prompt-injection report: {args.report}")
    print(f"Tasks: {task_count}; text files: {files_scanned}; findings: {len(findings)}")
    return 2 if args.fail_on_findings and findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
