from __future__ import annotations

import csv
import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable


LT1_ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar", ".zip")
LT1_ORBIT_SUFFIXES = (".txt",)
ProgressCallback = Callable[[str], None]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def stamp_text() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def normalize_path(value: str) -> Path:
    return Path(value.strip().strip('"')).expanduser()


def parse_path_list(value: str) -> list[Path]:
    paths: list[Path] = []
    for line in str(value or "").replace(";", "\n").splitlines():
        text = line.strip().strip('"')
        if text:
            paths.append(normalize_path(text))
    return paths


def safe_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def is_asset_file(path: Path) -> bool:
    lower = path.name.lower()
    if lower.startswith("lt1") and lower.endswith(LT1_ARCHIVE_SUFFIXES):
        return True
    if lower.startswith("lt1") and lower.endswith(LT1_ORBIT_SUFFIXES):
        return True
    return False


def classify_asset(path: Path) -> str:
    lower = path.name.lower()
    if lower.endswith(LT1_ARCHIVE_SUFFIXES):
        return "lt1_archive"
    if lower.endswith(LT1_ORBIT_SUFFIXES):
        return "lt1_orbit"
    return "unknown"


@dataclass(frozen=True)
class AssetRecord:
    kind: str
    name: str
    path: str
    size: int
    mtime: float


@dataclass(frozen=True)
class CopyRecord:
    name: str
    kind: str
    source_path: str
    target_path: str
    size: int
    action: str
    reason: str


class FileLogger:
    def __init__(self, path: Path, ui_log: ProgressCallback | None = None) -> None:
        self.path = path
        self.ui_log = ui_log
        safe_mkdir(path.parent)

    def __call__(self, message: str) -> None:
        line = f"{now_text()} {message}"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        if self.ui_log:
            self.ui_log(line)


def scan_assets(paths: Iterable[Path], *, log: ProgressCallback | None = None) -> list[AssetRecord]:
    records: list[AssetRecord] = []
    for root in paths:
        if not root.exists():
            if log:
                log(f"路径不存在，跳过：{root}")
            continue
        if not root.is_dir():
            if log:
                log(f"不是目录，跳过：{root}")
            continue
        count = 0
        for entry in root.iterdir():
            if not entry.is_file() or not is_asset_file(entry):
                continue
            stat = entry.stat()
            records.append(
                AssetRecord(
                    kind=classify_asset(entry),
                    name=entry.name,
                    path=str(entry),
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                )
            )
            count += 1
        if log:
            log(f"扫描完成：{root}，资产 {count} 个")
    records.sort(key=lambda item: (item.kind, item.name.lower(), item.path.lower()))
    return records


def asset_key(record: AssetRecord) -> tuple[str, str]:
    return record.kind, record.name.lower()


def build_asset_index(records: Iterable[AssetRecord]) -> dict[tuple[str, str], AssetRecord]:
    index: dict[tuple[str, str], AssetRecord] = {}
    for record in records:
        key = asset_key(record)
        if key not in index:
            index[key] = record
    return index


def read_inventory(path: Path) -> list[AssetRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records: list[AssetRecord] = []
    for item in payload.get("assets", payload.get("files", [])):
        try:
            records.append(
                AssetRecord(
                    kind=str(item.get("kind") or ""),
                    name=str(item.get("name") or ""),
                    path=str(item.get("path") or ""),
                    size=int(item.get("size") or 0),
                    mtime=float(item.get("mtime") or 0),
                )
            )
        except (TypeError, ValueError):
            continue
    return [item for item in records if item.kind and item.name]


def write_inventory(path: Path, records: list[AssetRecord], roots: list[str]) -> None:
    safe_mkdir(path.parent)
    payload = {
        "schema": "lt1_asset_inventory.v2",
        "generated_at": now_text(),
        "roots": roots,
        "asset_count": len(records),
        "assets": [asdict(item) for item in records],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, records: list[CopyRecord]) -> None:
    safe_mkdir(path.parent)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["name", "kind", "source_path", "target_path", "size", "action", "reason"],
        )
        writer.writeheader()
        for record in records:
            writer.writerow(asdict(record))


def choose_target_root(target_roots: list[Path], filename: str, size: int) -> tuple[Path | None, str]:
    for root in target_roots:
        dest = root / filename
        if dest.exists():
            try:
                if dest.stat().st_size == size:
                    return None, f"目标已存在且大小一致：{dest}"
                return None, f"目标已存在但大小不同：{dest}"
            except OSError:
                return None, f"无法读取目标文件状态：{dest}"
    for root in target_roots:
        try:
            safe_mkdir(root)
            free_bytes = shutil.disk_usage(root).free
            if free_bytes > size:
                return root, "选择第一个空间足够的目标路径"
        except OSError:
            continue
    return None, "没有空间足够的目标路径"


def copy_file_atomic(source: Path, target: Path, *, move: bool = False) -> None:
    safe_mkdir(target.parent)
    part = target.with_name(target.name + ".part")
    if part.exists():
        part.unlink()
    if target.exists():
        raise FileExistsError(str(target))
    if move:
        shutil.copy2(source, part)
        if part.stat().st_size != source.stat().st_size:
            raise OSError(f"复制后大小不一致：{source} -> {target}")
        os.replace(part, target)
        source.unlink()
    else:
        shutil.copy2(source, part)
        if part.stat().st_size != source.stat().st_size:
            raise OSError(f"复制后大小不一致：{source} -> {target}")
        os.replace(part, target)


def copy_unc_missing_assets(
    unc_roots: list[Path],
    server_inventory_json: Path,
    target_roots: list[Path],
    report_dir: Path,
    *,
    execute: bool,
    log: ProgressCallback | None = None,
) -> list[CopyRecord]:
    server_assets = read_inventory(server_inventory_json)
    server_index = build_asset_index(server_assets)
    source_assets = scan_assets(unc_roots, log=log)
    report: list[CopyRecord] = []

    for asset in source_assets:
        server_asset = server_index.get(asset_key(asset))
        if server_asset and server_asset.size == asset.size:
            report.append(
                CopyRecord(asset.name, asset.kind, asset.path, server_asset.path, asset.size, "skip", "服务器清单已有且大小一致")
            )
            continue
        if server_asset and server_asset.size != asset.size:
            report.append(
                CopyRecord(asset.name, asset.kind, asset.path, server_asset.path, asset.size, "conflict", "服务器清单有同名资产但大小不同")
            )
            continue

        target_root, reason = choose_target_root(target_roots, asset.name, asset.size)
        if target_root is None:
            report.append(CopyRecord(asset.name, asset.kind, asset.path, "", asset.size, "skip", reason))
            continue
        target_path = target_root / asset.name
        if not execute:
            report.append(CopyRecord(asset.name, asset.kind, asset.path, str(target_path), asset.size, "planned", reason))
            continue
        try:
            copy_file_atomic(Path(asset.path), target_path)
            report.append(CopyRecord(asset.name, asset.kind, asset.path, str(target_path), asset.size, "copied", reason))
            if log:
                log(f"已复制：{asset.name} -> {target_path}")
        except Exception as exc:
            report.append(CopyRecord(asset.name, asset.kind, asset.path, str(target_path), asset.size, "failed", str(exc)))
            if log:
                log(f"复制失败：{asset.name}，{exc}")

    write_csv(report_dir / f"unc_copy_report_{stamp_text()}.csv", report)
    return report


def import_disk_assets_to_server(
    disk_roots: list[Path],
    server_asset_roots: list[Path],
    report_dir: Path,
    *,
    execute: bool,
    move: bool,
    log: ProgressCallback | None = None,
) -> list[CopyRecord]:
    source_assets = scan_assets(disk_roots, log=log)
    server_assets = scan_assets(server_asset_roots, log=log)
    server_index = build_asset_index(server_assets)
    report: list[CopyRecord] = []

    for asset in source_assets:
        existing = server_index.get(asset_key(asset))
        if existing and existing.size == asset.size:
            report.append(CopyRecord(asset.name, asset.kind, asset.path, existing.path, asset.size, "skip", "服务器已存在且大小一致"))
            continue
        if existing and existing.size != asset.size:
            report.append(CopyRecord(asset.name, asset.kind, asset.path, existing.path, asset.size, "conflict", "服务器有同名资产但大小不同"))
            continue

        target_root, reason = choose_target_root(server_asset_roots, asset.name, asset.size)
        if target_root is None:
            report.append(CopyRecord(asset.name, asset.kind, asset.path, "", asset.size, "skip", reason))
            continue
        target_path = target_root / asset.name
        if not execute:
            report.append(CopyRecord(asset.name, asset.kind, asset.path, str(target_path), asset.size, "planned", reason))
            continue
        try:
            copy_file_atomic(Path(asset.path), target_path, move=move)
            action = "moved" if move else "copied"
            report.append(CopyRecord(asset.name, asset.kind, asset.path, str(target_path), asset.size, action, reason))
            if log:
                log(f"已{('剪切' if move else '复制')}：{asset.name} -> {target_path}")
        except Exception as exc:
            report.append(CopyRecord(asset.name, asset.kind, asset.path, str(target_path), asset.size, "failed", str(exc)))
            if log:
                log(f"导入失败：{asset.name}，{exc}")

    write_csv(report_dir / f"disk_import_report_{stamp_text()}.csv", report)
    return report
