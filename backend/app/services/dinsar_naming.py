from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, Optional

from ..utils import normalize_satellite_family


PAIR_META_FILENAME = ".dinsar_pair.json"
RUN_META_FILENAME = ".dinsar_run.json"

_DATE_RE = re.compile(r"^\d{8}$")
_SAFE_TEXT_RE = re.compile(r"[^0-9A-Za-z._-]+")


def normalize_path(value: Any) -> str:
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(os.path.abspath(text)))


def slugify(value: Any, *, default: str = "item", max_len: int = 48) -> str:
    text = _SAFE_TEXT_RE.sub("_", str(value or "").strip()).strip("._")
    if not text:
        text = default
    return text[:max_len]


def _normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    if _DATE_RE.match(text):
        return text
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        return digits[:8]
    return "unknown"


def build_task_alias(master_date: Any, slave_date: Any) -> str:
    return f"Task_{_normalize_date(master_date)}_{_normalize_date(slave_date)}"


def build_pair_key(
    master_path: Any,
    slave_path: Any,
    master_date: Any = None,
    slave_date: Any = None,
    satellite_family: Any = None,
) -> str:
    master_date_text = _normalize_date(master_date)
    slave_date_text = _normalize_date(slave_date)
    payload = "||".join(
        [
            normalize_path(master_path),
            normalize_path(slave_path),
            master_date_text,
            slave_date_text,
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:10]
    family = str(normalize_satellite_family(satellite_family) or "").strip().lower()
    if not family:
        family = "pair"
    return f"{family}_{master_date_text}_{slave_date_text}_{digest}"


def build_fallback_pair_key(task_alias: Any, source_hint: Any = None, satellite_family: Any = None) -> str:
    alias = str(task_alias or "").strip() or "Task_unknown_unknown"
    parts = alias.split("_")
    master_date = parts[1] if len(parts) > 2 else "unknown"
    slave_date = parts[2] if len(parts) > 2 else "unknown"
    payload = "||".join([alias, normalize_path(source_hint)])
    digest = hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:10]
    family = str(normalize_satellite_family(satellite_family) or "").strip().lower()
    if not family:
        family = "pair"
    return f"{family}_{_normalize_date(master_date)}_{_normalize_date(slave_date)}_{digest}"


def build_run_key(
    engine_code: str,
    profile_code: str,
    *,
    started_at: Optional[datetime] = None,
) -> str:
    timestamp = started_at or datetime.utcnow()
    ts_text = timestamp.strftime("%Y%m%dT%H%M%SZ")
    engine_slug = slugify(engine_code, default="engine", max_len=16)
    profile_slug = slugify(profile_code, default="profile", max_len=24)
    return f"run_{ts_text}_{engine_slug}_{profile_slug}"


def ensure_unique_task_aliases(pairs: list[Any]) -> list[Any]:
    counts: Dict[str, int] = {}
    final_pairs: list[Any] = []
    for pair in pairs:
        base_alias = str(
            getattr(pair, "task_alias", None)
            or getattr(pair, "task_name", None)
            or build_task_alias(
                getattr(getattr(pair, "master", None), "imaging_date", None),
                getattr(getattr(pair, "slave", None), "imaging_date", None),
            )
        ).strip()
        count = counts.get(base_alias, 0) + 1
        counts[base_alias] = count
        unique_alias = base_alias if count == 1 else f"{base_alias}_{count - 1}"
        setattr(pair, "task_alias", unique_alias)
        setattr(pair, "task_name", unique_alias)
        final_pairs.append(pair)
    return final_pairs


def load_json_sidecar(directory: Any, filename: str) -> Optional[Dict[str, Any]]:
    base_dir = normalize_path(directory)
    if not base_dir or not os.path.isdir(base_dir):
        return None
    path = os.path.join(base_dir, filename)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def find_json_sidecar(directory: Any, filename: str, *, max_levels: int = 4) -> Optional[Dict[str, Any]]:
    current = normalize_path(directory)
    levels = 0
    while current and levels <= max_levels:
        payload = load_json_sidecar(current, filename)
        if payload is not None:
            return payload
        parent = os.path.dirname(current)
        if not parent or parent == current:
            break
        current = parent
        levels += 1
    return None


def write_json_sidecar(directory: Any, filename: str, payload: Dict[str, Any]) -> str:
    target_dir = normalize_path(directory)
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, filename)
    with open(target_path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    return target_path


def write_pair_metadata(directory: Any, payload: Dict[str, Any]) -> str:
    return write_json_sidecar(directory, PAIR_META_FILENAME, payload)


def write_run_metadata(directory: Any, payload: Dict[str, Any]) -> str:
    return write_json_sidecar(directory, RUN_META_FILENAME, payload)
