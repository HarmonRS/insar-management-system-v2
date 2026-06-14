"""Migrate repo-local runtime data to configured external roots."""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
LEGACY_RUNTIME = BACKEND_DIR / "runtime"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import settings  # noqa: E402
from backend.app import database  # noqa: E402
from backend.app.services.sbas_insar_catalog_service import sbas_insar_catalog_service  # noqa: E402
from backend.app.services.sbas_insar_production_service import sbas_insar_production_service  # noqa: E402


def _norm(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _measure(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    if path.is_file():
        return path.stat().st_size, 1
    size = 0
    files = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                size += item.stat().st_size
                files += 1
            except OSError:
                continue
    return size, files


def _gb(size: int) -> str:
    return f"{size / (1024 ** 3):.3f} GB"


def _copy_tree(source: Path, target: Path, *, delete_source: bool) -> dict[str, object]:
    if not source.exists():
        return {"source": str(source), "target": str(target), "exists": False, "copied": False}
    if _norm(target) == _norm(source):
        return {"source": str(source), "target": str(target), "exists": True, "copied": False, "reason": "same_path"}
    if _is_under(_norm(target), _norm(source)):
        raise ValueError(f"refusing to copy {source} into itself: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"target already exists: {target}")
    shutil.copytree(source, target)
    size, files = _measure(target)
    if delete_source:
        shutil.rmtree(source)
    return {
        "source": str(source),
        "target": str(target),
        "exists": True,
        "copied": True,
        "deleted_source": bool(delete_source),
        "size": _gb(size),
        "files": files,
    }


def migrate_pyint_dem(*, apply: bool, delete_source: bool) -> dict[str, object]:
    source = LEGACY_RUNTIME / "pyint_dem"
    target = Path(settings.PYINT_DEM_ROOT)
    size, files = _measure(source)
    plan = {
        "source": str(source),
        "target": str(target),
        "source_exists": source.exists(),
        "source_size": _gb(size),
        "source_files": files,
        "apply": apply,
        "delete_source": delete_source,
    }
    if not apply:
        return plan
    return {**plan, "result": _copy_tree(source, target, delete_source=delete_source)}


def sync_sbas_products(*, apply: bool) -> dict[str, object]:
    legacy_root = LEGACY_RUNTIME / "sbas_insar_production" / "runs"
    configured_work_root = Path(settings.GAMMA_SBAS_WORK_ROOT) / "runs"
    roots = []
    for root in (legacy_root, configured_work_root):
        if root.is_dir() and root not in roots:
            roots.append(root)
    run_ids = sorted(
        {
            path.parent.name
            for root in roots
            for path in root.glob("*/run_manifest.json")
        }
    )
    plan: dict[str, object] = {
        "source_roots": [str(root) for root in roots],
        "product_root": str(Path(settings.GAMMA_SBAS_PRODUCT_ROOT) / "runs"),
        "run_ids": run_ids,
        "apply": apply,
    }
    if not apply:
        return plan
    synced = []
    original_root = sbas_insar_production_service.production_root
    try:
        for run_id in run_ids:
            selected_root = next((root for root in roots if (root / run_id / "run_manifest.json").is_file()), None)
            if selected_root is None:
                continue
            sbas_insar_production_service.production_root = selected_root.parent
            synced.append(sbas_insar_production_service.sync_product_package(run_id))
    finally:
        sbas_insar_production_service.production_root = original_root
    plan["synced"] = synced
    return plan


async def rebuild_sbas_catalog() -> dict[str, object]:
    if database.AsyncSessionLocal is None:
        database.init_db(settings.DATABASE_URL)
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized")
    async with database.AsyncSessionLocal() as db:
        return await sbas_insar_catalog_service.rebuild_catalog(db, full_rebuild=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the migration; default only prints the plan")
    parser.add_argument("--move-pyint-dem", action="store_true", help="copy legacy pyint_dem to PYINT_DEM_ROOT")
    parser.add_argument("--delete-legacy-pyint-dem", action="store_true", help="delete legacy pyint_dem after copy")
    parser.add_argument("--sync-sbas-products", action="store_true", help="sync lightweight SBAS products into GAMMA_SBAS_PRODUCT_ROOT")
    parser.add_argument("--rebuild-sbas-catalog", action="store_true", help="rebuild SBAS result catalog after syncing products")
    args = parser.parse_args()

    actions: dict[str, object] = {}
    if args.move_pyint_dem:
        actions["pyint_dem"] = migrate_pyint_dem(apply=args.apply, delete_source=args.delete_legacy_pyint_dem)
    if args.sync_sbas_products:
        actions["sbas_products"] = sync_sbas_products(apply=args.apply)
    if args.rebuild_sbas_catalog:
        if args.apply:
            actions["sbas_catalog"] = asyncio.run(rebuild_sbas_catalog())
        else:
            actions["sbas_catalog"] = {"apply": False, "action": "rebuild_sbas_catalog"}
    if not actions:
        actions["message"] = "no actions selected"

    import json

    print(json.dumps(actions, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
