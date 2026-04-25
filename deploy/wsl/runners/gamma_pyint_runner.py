from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict


def _load_manifest(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Gamma/PyINT runtime V1 runner scaffold.")
    parser.add_argument("--manifest", required=True, help="WSL path to the staged job manifest.")
    parser.add_argument("--dry-run", action="store_true", help="Emit manifest summary and exit.")
    args = parser.parse_args()

    manifest = _load_manifest(args.manifest)
    summary = {
        "runner": "gamma_pyint_runtime_v1",
        "manifest": args.manifest,
        "job_id": manifest.get("job_id"),
        "operation": manifest.get("operation"),
        "payload_keys": sorted((manifest.get("payload") or {}).keys()),
        "dry_run": bool(args.dry_run),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
