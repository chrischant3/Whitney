"""Integrity of the local Harbor task cache: a SHA-256 of every file, recorded at sync and checked before use.

The cache holds the verifiers and Dockerfiles that the Harbor eval builds and
runs, and anything that can write to the folder (a local-mode agent among
them) could change them between runs.
"""

import hashlib
import json
from pathlib import Path

MANIFEST_NAME = "inspect-harbor-manifest.json"


class CacheIntegrityError(ValueError):
    """The local Harbor cache holds a symlink, or changed since it was synced."""


def file_digests(root: Path) -> dict[str, str]:
    """SHA-256 of every file under `root` except the manifest, keyed by POSIX relative path.

    Raises:
        CacheIntegrityError: a symlink anywhere under `root`, which could point
            a task's build or verifier at files on the host.
    """
    digests = {}
    for path in sorted(Path(root).rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise CacheIntegrityError(f"{root}: {relative} is a symlink; the Harbor cache must hold plain files")
        if path.is_file() and relative != MANIFEST_NAME:
            digests[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def check_cache(dataset_dir: Path) -> str:
    """Compare `dataset_dir` with the digests in its manifest.

    Returns:
        "verified", or why it couldn't be: no manifest, or a manifest written
        before digests were recorded (re-sync to add them).

    Raises:
        CacheIntegrityError: a symlink, or a file added, removed or changed since the sync.
    """
    current = file_digests(dataset_dir)
    manifest_path = Path(dataset_dir) / MANIFEST_NAME
    if not manifest_path.is_file():
        return "unverified: no manifest"
    recorded = json.loads(manifest_path.read_text(encoding="utf-8")).get("files")
    if recorded is None:
        return "unverified: the manifest has no file digests; re-sync the cache to add them"
    changed = sorted(name for name in current.keys() | recorded.keys() if current.get(name) != recorded.get(name))
    if changed:
        more = f" and {len(changed) - 5} more" if len(changed) > 5 else ""
        raise CacheIntegrityError(f"{dataset_dir} changed since it was synced: {', '.join(changed[:5])}{more}")
    return "verified"
