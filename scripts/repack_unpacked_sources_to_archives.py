"""Repack unpacked LT1/Sentinel-1 source directories back to archives.

This script never deletes the unpacked source directories. It only creates
missing archives in the configured local ZIP pools, then can optionally run the
source-archive migration script.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_LT1_SRC = Path(r"D:\LuTan1_Image_Pool")
DEFAULT_LT1_DST = Path(r"D:\LuTan1_Image_Pool_Zip")
DEFAULT_S1_SRC = Path(r"D:\Sentinel1_Image_Pool")
DEFAULT_S1_DST = Path(r"D:\Sentinel1_Image_Pool_ZIP")


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(message: str) -> None:
    print(f"[{_ts()}] {message}", flush=True)


def _existing_lt1_archive(dst: Path, name: str) -> Path | None:
    for suffix in (".tar.gz", ".tgz", ".tar", ".zip"):
        candidate = dst / f"{name}{suffix}"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _run_tar(args: Sequence[str]) -> None:
    command = ["tar.exe", *args]
    result = subprocess.run(command, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        raise RuntimeError(f"tar.exe failed with code {result.returncode}: {' '.join(command)}")


def _pack_with_tar(src_root: Path, item_name: str, target_tmp: Path, target_final: Path, *, mode: str) -> None:
    if target_tmp.exists():
        target_tmp.unlink()
    if target_final.exists():
        target_final.unlink()

    if mode == "tar.gz":
        _run_tar(["-czf", str(target_tmp), "-C", str(src_root), item_name])
    elif mode == "zip":
        _run_tar(["-a", "-cf", str(target_tmp), "-C", str(src_root), item_name])
    else:
        raise ValueError(f"Unsupported archive mode: {mode}")

    if not target_tmp.is_file() or target_tmp.stat().st_size <= 0:
        raise RuntimeError(f"Archive was not created: {target_tmp}")
    target_tmp.replace(target_final)


def _iter_dirs(root: Path, pattern: str = "*") -> List[Path]:
    if not root.is_dir():
        raise FileNotFoundError(f"Source directory not found: {root}")
    return sorted([item for item in root.glob(pattern) if item.is_dir()], key=lambda p: p.name)


def repack_lt1(src: Path, dst: Path, *, limit: int = 0, dry_run: bool = False) -> tuple[int, int]:
    dst.mkdir(parents=True, exist_ok=True)
    dirs = [item for item in _iter_dirs(src) if _existing_lt1_archive(dst, item.name) is None]
    if limit > 0:
        dirs = dirs[:limit]
    _log(f"LT1 missing archives: {len(dirs)}")

    packed = 0
    for index, item in enumerate(dirs, start=1):
        final_path = dst / f"{item.name}.tar.gz"
        tmp_path = dst / f"{item.name}.tmp.tar.gz"
        _log(f"PACK LT1 [{index}/{len(dirs)}] {item.name}")
        if dry_run:
            continue
        try:
            _pack_with_tar(src, item.name, tmp_path, final_path, mode="tar.gz")
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise
        packed += 1
    return packed, len(dirs)


def repack_s1(src: Path, dst: Path, *, limit: int = 0, dry_run: bool = False) -> tuple[int, int]:
    dst.mkdir(parents=True, exist_ok=True)
    missing = []
    for item in _iter_dirs(src, "*.SAFE"):
        base = item.name[:-5] if item.name.upper().endswith(".SAFE") else item.name
        final_path = dst / f"{base}.zip"
        if not final_path.is_file() or final_path.stat().st_size <= 0:
            missing.append(item)
    if limit > 0:
        missing = missing[:limit]
    _log(f"Sentinel-1 missing ZIP archives: {len(missing)}")

    packed = 0
    for index, item in enumerate(missing, start=1):
        base = item.name[:-5] if item.name.upper().endswith(".SAFE") else item.name
        final_path = dst / f"{base}.zip"
        tmp_path = dst / f"{base}.tmp.zip"
        _log(f"PACK S1 [{index}/{len(missing)}] {item.name}")
        if dry_run:
            continue
        try:
            _pack_with_tar(src, item.name, tmp_path, final_path, mode="zip")
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise
        packed += 1
    return packed, len(missing)


def run_migration(python_exe: str) -> None:
    migration_script = PROJECT_ROOT / "scripts" / "migrate_source_archives_to_zip.py"
    commands = [
        [
            python_exe,
            str(migration_script),
            "--apply",
            "--no-bind-orbits",
            "--preview-sample",
            "5",
        ],
        [
            python_exe,
            str(migration_script),
            "--apply",
            "--no-scan-archives",
            "--no-bind-orbits",
            "--build-archive-previews",
            "--preview-build-limit",
            "0",
            "--preview-sample",
            "5",
        ],
        [
            python_exe,
            str(migration_script),
            "--preview-sample",
            "5",
        ],
    ]
    for command in commands:
        _log(f"RUN {' '.join(command)}")
        subprocess.run(command, cwd=str(PROJECT_ROOT), check=True)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lt-src", default=str(DEFAULT_LT1_SRC))
    parser.add_argument("--lt-dst", default=str(DEFAULT_LT1_DST))
    parser.add_argument("--s1-src", default=str(DEFAULT_S1_SRC))
    parser.add_argument("--s1-dst", default=str(DEFAULT_S1_DST))
    parser.add_argument("--only", choices=["all", "lt1", "s1"], default="all")
    parser.add_argument("--limit", type=int, default=0, help="limit each sensor; 0 means no limit")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-migration", action="store_true")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(list(argv) if argv is not None else None)

    _log("Repack started. Existing archives are skipped. Source directories are never deleted.")
    if args.only in {"all", "lt1"}:
        packed, total = repack_lt1(Path(args.lt_src), Path(args.lt_dst), limit=args.limit, dry_run=args.dry_run)
        _log(f"LT1 done: packed={packed}, planned={total}")
    if args.only in {"all", "s1"}:
        packed, total = repack_s1(Path(args.s1_src), Path(args.s1_dst), limit=args.limit, dry_run=args.dry_run)
        _log(f"Sentinel-1 done: packed={packed}, planned={total}")
    if args.run_migration and not args.dry_run:
        run_migration(args.python)
    _log("Repack finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
