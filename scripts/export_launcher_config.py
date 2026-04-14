#!/usr/bin/env python3
"""
Emit launcher-facing runtime configuration as JSON.
"""
import json
import os
import sys


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


PROJECT_ROOT = _project_root()
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


from backend.app.config import export_launcher_config


def main() -> int:
    payload = export_launcher_config()
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
