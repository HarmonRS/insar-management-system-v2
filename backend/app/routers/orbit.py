"""Precise orbit management APIs."""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models.orm import AuthUserORM, RadarDataORM
from ..routers.dependencies import _get_current_user as get_current_user, _require_admin as require_admin
from ..services.orbit_converter import (
    check_orbit_consistency,
    get_orbit_pool_inventory,
    organize_orbit_dir,
    quarantine_bad_orbits,
    repair_orbit_pools,
    scan_orbit_dir,
    summarize_source_orbit_gaps,
)

router = APIRouter(prefix="/orbit", tags=["orbit"])


class OrbitPoolActionRequest(BaseModel):
    repair: bool = False
    quarantine_bad: bool = False


async def _build_orbit_database_stats(
    db: AsyncSession,
    pool_inventory: Dict[str, Any],
    *,
    isce2_enabled: bool = False,
) -> Dict[str, Any]:
    total_radar_count = (
        await db.execute(select(func.count(RadarDataORM.id)))
    ).scalar_one()
    with_orbit_data_count = (
        await db.execute(
            select(func.count(RadarDataORM.id)).where(RadarDataORM.has_orbit_data == True)
        )
    ).scalar_one()
    without_orbit_data_count = total_radar_count - with_orbit_data_count
    has_orbit_but_missing_path_count = (
        await db.execute(
            select(func.count(RadarDataORM.id)).where(
                RadarDataORM.has_orbit_data == True,
                or_(RadarDataORM.orbit_file_path.is_(None), RadarDataORM.orbit_file_path == ""),
            )
        )
    ).scalar_one()
    without_orbit_but_path_present_count = (
        await db.execute(
            select(func.count(RadarDataORM.id)).where(
                RadarDataORM.has_orbit_data == False,
                RadarDataORM.orbit_file_path.is_not(None),
                RadarDataORM.orbit_file_path != "",
            )
        )
    ).scalar_one()

    path_rows = await db.execute(
        select(RadarDataORM.orbit_file_path)
        .where(RadarDataORM.orbit_file_path.is_not(None), RadarDataORM.orbit_file_path != "")
        .distinct()
    )
    distinct_paths = [item for item in path_rows.scalars().all() if item]

    db_existing_path_count = 0
    db_missing_path_count = 0
    db_path_errors: list[str] = []
    for path in distinct_paths:
        try:
            if os.path.exists(path):
                db_existing_path_count += 1
            else:
                db_missing_path_count += 1
        except OSError as exc:
            db_missing_path_count += 1
            if len(db_path_errors) < 10:
                db_path_errors.append(f"{path}: {exc}")

    db_expected_rows = await db.execute(
        select(RadarDataORM.satellite, RadarDataORM.imaging_date).where(RadarDataORM.has_orbit_data == True)
    )
    db_expected_stems = {
        f"{satellite}_GpsData_GAS_C_{imaging_date}"
        for satellite, imaging_date in db_expected_rows.all()
        if satellite and imaging_date
    }

    envi_stems = set(pool_inventory["envi"]["files"].keys())
    isce2_stems = set(pool_inventory["isce2"]["files"].keys()) if isce2_enabled else set()
    missing_in_envi = sorted(db_expected_stems - envi_stems)
    missing_in_isce2 = sorted(db_expected_stems - isce2_stems) if isce2_enabled else []

    return {
        "total_radar_count": int(total_radar_count or 0),
        "with_orbit_data_count": int(with_orbit_data_count or 0),
        "without_orbit_data_count": int(without_orbit_data_count or 0),
        "has_orbit_but_missing_path_count": int(has_orbit_but_missing_path_count or 0),
        "without_orbit_but_path_present_count": int(without_orbit_but_path_present_count or 0),
        "distinct_orbit_path_count": len(distinct_paths),
        "db_existing_path_count": db_existing_path_count,
        "db_missing_path_count": db_missing_path_count,
        "db_expected_stem_count": len(db_expected_stems),
        "stems_missing_in_envi_count": len(missing_in_envi),
        "stems_missing_in_isce2_count": len(missing_in_isce2),
        "isce2_enabled": bool(isce2_enabled),
        "sample_missing_in_envi": missing_in_envi[:20],
        "sample_missing_in_isce2": missing_in_isce2[:20],
        "path_errors": db_path_errors,
    }


@router.get("/status")
async def get_orbit_status(
    current_user: AuthUserORM = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return source, pool, consistency, and database orbit status."""
    source_dir = settings.MONITOR_ORBIT_DIR
    isce2_pool = settings.ORBIT_POOL_ISCE2 if settings.ISCE2_ENABLED else ""
    source_stats = await asyncio.to_thread(scan_orbit_dir, source_dir)
    pool_inventory = await asyncio.to_thread(
        get_orbit_pool_inventory,
        settings.ORBIT_POOL_ENVI,
        isce2_pool,
        True,
    )
    consistency = await asyncio.to_thread(
        check_orbit_consistency,
        settings.ORBIT_POOL_ENVI,
        isce2_pool,
    )
    source_gap_summary = await asyncio.to_thread(
        summarize_source_orbit_gaps,
        source_dir,
        settings.ORBIT_POOL_ENVI,
        isce2_pool,
        settings.ORBIT_QUARANTINE_DIR,
    )
    database_stats = await _build_orbit_database_stats(db, pool_inventory, isce2_enabled=bool(settings.ISCE2_ENABLED))

    return {
        "orbit_root": source_stats.orbit_root,
        "root_loose": source_stats.root_loose,
        "by_satellite": source_stats.by_satellite,
        "converted": source_stats.converted,
        "total_source": source_stats.total_source,
        "supported_formats": source_stats.supported_formats,
        "source": {
            "path": source_stats.orbit_root,
            "root_loose": source_stats.root_loose,
            "by_satellite": source_stats.by_satellite,
            "converted": source_stats.converted,
            "total_source": source_stats.total_source,
            "supported_formats": source_stats.supported_formats,
            "duplicate_count": source_stats.duplicate_count,
            "errors": source_stats.errors,
            "quarantine_path": source_gap_summary["quarantine_path"],
            "sample_limit": source_gap_summary["sample_limit"],
            "source_without_isce2_count": source_gap_summary["source_without_isce2_count"],
            "source_without_envi_count": source_gap_summary["source_without_envi_count"],
            "envi_without_source_count": source_gap_summary["envi_without_source_count"],
            "isce2_without_source_count": source_gap_summary["isce2_without_source_count"],
            "suspect_bad_count": source_gap_summary["suspect_bad_count"],
            "suspect_bad_samples": source_gap_summary["suspect_bad_samples"],
            "bad_source_sample_count": source_gap_summary["bad_source_sample_count"],
            "bad_source_samples": source_gap_summary["bad_source_samples"],
            "source_without_envi_samples": source_gap_summary["source_without_envi_samples"],
            "envi_without_source_samples": source_gap_summary["envi_without_source_samples"],
            "isce2_without_source_samples": source_gap_summary["isce2_without_source_samples"],
        },
        "source_gaps": source_gap_summary,
        "pools": {
            "envi": {
                "path": settings.ORBIT_POOL_ENVI,
                "total": pool_inventory["envi"]["total"],
                "by_satellite": pool_inventory["envi"]["by_satellite"],
                "duplicate_count": pool_inventory["envi"]["duplicate_count"],
                "errors": pool_inventory["envi"]["errors"],
            },
            "isce2": {
                "path": isce2_pool,
                "enabled": bool(settings.ISCE2_ENABLED),
                "total": pool_inventory["isce2"]["total"],
                "duplicate_count": pool_inventory["isce2"]["duplicate_count"],
                "errors": pool_inventory["isce2"]["errors"],
            },
            "landsar": {
                "path": settings.ORBIT_POOL_LANDSAR,
            },
        },
        "consistency": consistency,
        "database": database_stats,
    }


@router.post("/sync-pools")
async def sync_orbit_pool_action(
    payload: Optional[OrbitPoolActionRequest] = None,
    current_user: AuthUserORM = Depends(require_admin),
):
    """
    Check pool consistency, or repair missing entries when repair=true.
    """
    if payload and payload.quarantine_bad:
        isce2_pool = settings.ORBIT_POOL_ISCE2 if settings.ISCE2_ENABLED else ""
        return await asyncio.to_thread(
            quarantine_bad_orbits,
            settings.MONITOR_ORBIT_DIR,
            settings.ORBIT_POOL_ENVI,
            isce2_pool,
            settings.ORBIT_QUARANTINE_DIR,
        )
    if payload and payload.repair:
        isce2_pool = settings.ORBIT_POOL_ISCE2 if settings.ISCE2_ENABLED else ""
        return await asyncio.to_thread(
            repair_orbit_pools,
            settings.MONITOR_ORBIT_DIR,
            settings.ORBIT_POOL_ENVI,
            isce2_pool,
            settings.ORBIT_POOL_LANDSAR,
        )
    isce2_pool = settings.ORBIT_POOL_ISCE2 if settings.ISCE2_ENABLED else ""
    return await asyncio.to_thread(
        check_orbit_consistency,
        settings.ORBIT_POOL_ENVI,
        isce2_pool,
    )


@router.post("/organize")
async def organize_orbits(
    current_user: AuthUserORM = Depends(require_admin),
):
    """Move loose ENVI orbit TXT files into satellite subdirectories."""
    envi_pool = settings.ORBIT_POOL_ENVI
    result = await asyncio.to_thread(organize_orbit_dir, envi_pool)
    return {
        "orbit_root": envi_pool,
        "moved_count": len(result["moved"]),
        "skipped_count": len(result["skipped"]),
        "error_count": len(result["errors"]),
        "moved": result["moved"],
        "skipped": result["skipped"],
        "errors": result["errors"],
    }
