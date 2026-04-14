from __future__ import annotations

import csv
import io
import subprocess
from typing import Iterable, Set


_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def list_process_names() -> Set[str]:
    """Return lowercase process names visible to the current account."""
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
            shell=False,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:
        return set()

    names: Set[str] = set()
    reader = csv.reader(io.StringIO(result.stdout))
    for row in reader:
        if not row:
            continue
        name = str(row[0] or "").strip().lower()
        if name:
            names.add(name)
    return names


def is_any_process_running(target_names: Iterable[str]) -> bool:
    normalized_targets = {
        str(name or "").strip().lower()
        for name in target_names
        if str(name or "").strip()
    }
    if not normalized_targets:
        return False
    return bool(list_process_names() & normalized_targets)
