from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple


MANIFEST_FILENAME = "manifest.json"


def _normalize_path(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))


def _hash_file(path: str, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ManifestEntrySnapshot:
    absolute_path: str
    relative_path: str
    fingerprint: str
    size_bytes: Optional[int]
    mtime: Optional[float]
    ctime: Optional[float]


@dataclass(frozen=True)
class ManifestSnapshot:
    root: str
    manifest_count: int
    tree_fingerprint: str
    entries: Tuple[ManifestEntrySnapshot, ...]
    manifest_paths: Tuple[str, ...]


def iter_manifest_paths(root: str) -> List[str]:
    normalized_root = _normalize_path(root)
    if not os.path.isdir(normalized_root):
        return []

    manifest_paths: List[str] = []
    stack = [normalized_root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                            continue
                        if entry.is_file(follow_symlinks=False) and entry.name == MANIFEST_FILENAME:
                            manifest_paths.append(_normalize_path(entry.path))
                    except OSError:
                        continue
        except OSError:
            continue

    manifest_paths.sort()
    return manifest_paths


def build_manifest_snapshot(root: str) -> ManifestSnapshot:
    normalized_root = _normalize_path(root)
    manifest_paths = tuple(iter_manifest_paths(normalized_root))
    digest = hashlib.sha1()
    entries: List[ManifestEntrySnapshot] = []

    for manifest_path in manifest_paths:
        try:
            relative_path = os.path.relpath(manifest_path, normalized_root).replace("\\", "/")
        except ValueError:
            relative_path = os.path.basename(manifest_path)
        try:
            file_fingerprint = _hash_file(manifest_path)
        except OSError as exc:
            file_fingerprint = f"!read_error:{exc.__class__.__name__}"
        try:
            stat = os.stat(manifest_path)
            size_bytes = int(stat.st_size)
            mtime = float(stat.st_mtime)
            ctime = float(stat.st_ctime)
        except OSError:
            size_bytes = None
            mtime = None
            ctime = None
        digest.update(relative_path.encode("utf-8", errors="ignore"))
        digest.update(b"\0")
        digest.update(file_fingerprint.encode("utf-8", errors="ignore"))
        digest.update(b"\n")
        entries.append(
            ManifestEntrySnapshot(
                absolute_path=manifest_path,
                relative_path=relative_path,
                fingerprint=file_fingerprint,
                size_bytes=size_bytes,
                mtime=mtime,
                ctime=ctime,
            )
        )

    return ManifestSnapshot(
        root=normalized_root,
        manifest_count=len(manifest_paths),
        tree_fingerprint=digest.hexdigest() if manifest_paths else "",
        entries=tuple(entries),
        manifest_paths=manifest_paths,
    )


def evaluate_manifest_reconcile(
    *,
    manifest_count: int,
    db_count: int,
    current_fingerprint: str,
    indexed_fingerprint: Optional[str],
) -> dict:
    reasons: List[str] = []
    normalized_indexed_fingerprint = str(indexed_fingerprint or "").strip()
    normalized_current_fingerprint = str(current_fingerprint or "").strip()

    if int(db_count or 0) != int(manifest_count or 0):
        reasons.append("count_mismatch")
    if manifest_count > 0 and normalized_indexed_fingerprint != normalized_current_fingerprint:
        reasons.append("fingerprint_mismatch")

    return {
        "needs_rebuild": bool(reasons),
        "reasons": reasons,
        "current_fingerprint": normalized_current_fingerprint,
        "indexed_fingerprint": normalized_indexed_fingerprint,
    }
