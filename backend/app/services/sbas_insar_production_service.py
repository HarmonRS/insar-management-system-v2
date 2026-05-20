from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ..config import settings


PRODUCT_DEFINITIONS = (
    {
        "key": "los_rate_toward_mm_per_year_geo_preview_png",
        "label": "LOS velocity geocoded preview, toward radar positive",
        "role": "primary_geocoded_preview",
        "relative_path": "publish/geotiff/los_rate_toward_mm_per_year.geo_preview.png",
    },
    {
        "key": "los_rate_toward_mm_per_year_bmp",
        "label": "LOS velocity RDC processing preview, toward radar positive",
        "role": "rdc_processing_preview",
        "relative_path": "publish/geotiff/los_rate_toward_mm_per_year.bmp",
    },
    {
        "key": "los_rate_toward_mm_per_year_tif",
        "label": "LOS velocity GeoTIFF, toward radar positive",
        "role": "primary_geotiff",
        "relative_path": "publish/geotiff/los_rate_toward_mm_per_year.tif",
    },
    {
        "key": "los_rate_away_mm_per_year_bmp",
        "label": "LOS velocity RDC processing preview, away from radar positive",
        "role": "rdc_processing_preview",
        "relative_path": "publish/geotiff/los_rate_away_mm_per_year.bmp",
    },
    {
        "key": "los_rate_away_mm_per_year_tif",
        "label": "LOS velocity GeoTIFF, away from radar positive",
        "role": "alternate_geotiff",
        "relative_path": "publish/geotiff/los_rate_away_mm_per_year.tif",
    },
    {
        "key": "los_sigma_mm_per_year_geo_preview_png",
        "label": "LOS velocity sigma geocoded preview",
        "role": "quality_geocoded_preview",
        "relative_path": "publish/geotiff/los_sigma_mm_per_year.geo_preview.png",
    },
    {
        "key": "los_sigma_mm_per_year_bmp",
        "label": "LOS velocity sigma RDC processing preview",
        "role": "rdc_processing_preview",
        "relative_path": "publish/geotiff/los_sigma_mm_per_year.bmp",
    },
    {
        "key": "los_sigma_mm_per_year_tif",
        "label": "LOS velocity sigma GeoTIFF",
        "role": "quality_geotiff",
        "relative_path": "publish/geotiff/los_sigma_mm_per_year.tif",
    },
    {
        "key": "ts_rate_rad_per_year_tif",
        "label": "Gamma ts_rate phase-rate GeoTIFF",
        "role": "gamma_phase_rate",
        "relative_path": "publish/geotiff/ts_rate_rad_per_year.tif",
    },
    {
        "key": "sigma_rate_rad_per_year_tif",
        "label": "Gamma sigma_rate GeoTIFF",
        "role": "gamma_sigma_rate",
        "relative_path": "publish/geotiff/sigma_rate_rad_per_year.tif",
    },
    {
        "key": "trial_summary_json",
        "label": "Trial summary JSON",
        "role": "summary",
        "relative_path": "publish/trial_summary.json",
    },
)


MONITOR_ARTIFACT_SUFFIXES = (
    ("timeseries_png", "Monitoring point curve", ".png"),
    ("timeseries_csv", "Monitoring point values", ".csv"),
    ("metadata_json", "Monitoring point metadata", ".json"),
)

GAMMA_STAGE_PLAN = (
    {
        "stage_id": "prepare_slc",
        "label": "Prepare LT1 SLCs",
        "gamma_tools": ["par_LT1_SLC", "LT1_precision_orbit.py", "multi_look"],
        "status": "PLANNED",
    },
    {
        "stage_id": "baseline_audit",
        "label": "Gamma baseline audit and itab approval",
        "gamma_tools": ["base_calc"],
        "status": "PENDING_REQUIRED_AUDIT",
    },
    {
        "stage_id": "coregistration",
        "label": "Stack co-registration",
        "gamma_tools": ["SLC_coreg.py"],
        "status": "PLANNED_AFTER_BASELINE_AUDIT",
    },
    {
        "stage_id": "rdc_dem",
        "label": "RDC DEM and lookup table",
        "gamma_tools": ["gc_map1", "geocode", "gc_map_fine"],
        "status": "PLANNED_AFTER_BASELINE_AUDIT",
    },
    {
        "stage_id": "interferograms",
        "label": "Differential interferograms",
        "gamma_tools": ["phase_sim_orb", "SLC_diff_intf", "adf", "mcf"],
        "status": "PLANNED_AFTER_BASELINE_AUDIT",
    },
    {
        "stage_id": "ipta_timeseries",
        "label": "IPTA SBAS time-series inversion",
        "gamma_tools": ["mb", "ts_rate"],
        "status": "PLANNED_AFTER_BASELINE_AUDIT",
    },
    {
        "stage_id": "publish_products",
        "label": "Geocode and publish products",
        "gamma_tools": ["geocode_back", "data2geotiff", "dispmap"],
        "status": "PLANNED_AFTER_BASELINE_AUDIT",
    },
    {
        "stage_id": "monitor_points",
        "label": "Monitoring-point time-series extraction",
        "gamma_tools": [],
        "status": "PLANNED_AFTER_PRODUCTS",
    },
)

LT1_SCENE_RE = re.compile(
    r"^(?P<satellite>LT1[AB])_"
    r"(?P<satellite_mode>[A-Z0-9]+)_"
    r"(?P<receiving_station>[A-Z0-9]+)_"
    r"(?P<imaging_mode>[A-Z0-9]+)_"
    r"(?P<absolute_orbit>\d+)_"
    r"E(?P<center_lon>-?\d+(?:\.\d+)?)_"
    r"N(?P<center_lat>-?\d+(?:\.\d+)?)_"
    r"(?P<date>\d{8})_"
    r"(?P<product_type>[A-Z0-9]+)_"
    r"(?P<polarization>[A-Z0-9]+)_",
    re.IGNORECASE,
)


class SbasInsarProductionService:
    def __init__(self) -> None:
        self.trial_root = Path(settings.BACKEND_DIR) / "runtime" / "gamma_ipta_trials"
        self.production_root = Path(settings.BACKEND_DIR) / "runtime" / "sbas_insar_production"

    def get_capabilities(self) -> dict[str, Any]:
        return {
            "workflow_code": "sbas_insar",
            "processor_code": "gamma_ipta_sbas",
            "engine_code": "gamma",
            "implementation_state": "baseline_audit_and_coregistration_queue",
            "trial_root": str(self.trial_root),
            "production_root": str(self.production_root),
            "supported_sensors": ["LT1"],
            "supported_products": [item["key"] for item in PRODUCT_DEFINITIONS],
            "run_submission": {
                "enabled": True,
                "execution_enabled": True,
                "status_after_submit": "PLANNED_GAMMA_BASELINE_AUDIT",
                "description": "Creates reproducible filesystem manifests and can execute the Gamma SLC preparation plus base_calc baseline-audit stage.",
            },
            "baseline_audit": {
                "enabled": True,
                "default_rlks": 8,
                "default_azlks": 8,
                "default_max_delta_n": 1,
                "stage_status_after_success": "BASELINE_AUDIT_READY",
            },
            "coregistration": {
                "enabled": True,
                "execution_enabled": True,
                "execution_mode": "queued_background_task",
                "job_type": "SBAS_COREGISTRATION",
                "default_strategy": "common_reference_to_stack_reference_date",
                "requires_status": "ITAB_APPROVED",
            },
            "monitor_point_modes": ["auto_low_sigma_high_rate", "manual_lonlat"],
            "default_los_convention": {
                "key": "los_rate_toward_mm_per_year",
                "description": "toward radar positive; away from radar negative",
                "gamma_dispmap_equivalent": "sflg=0",
            },
            "sign_conventions": [
                {
                    "key": "away_positive",
                    "formula": "phase_rate*wavelength/(4*pi)*1000",
                    "description": "away from radar positive; same sign as Gamma phase",
                },
                {
                    "key": "toward_positive",
                    "formula": "-phase_rate*wavelength/(4*pi)*1000",
                    "description": "toward radar positive; Gamma dispmap default sflg=0",
                },
            ],
            "next_enabled_operation": "gamma_coregistration_background_job",
        }

    def discover_stacks(
        self,
        *,
        source_roots: list[str] | None = None,
        orbit_roots: list[str] | None = None,
        min_scenes: int = 3,
        require_orbits: bool = True,
        include_scenes: bool = False,
        limit: int = 30,
        platform: str | None = None,
        relative_orbit: str | None = None,
        orbit_direction: str | None = None,
    ) -> dict[str, Any]:
        source_paths = self._resolve_source_roots(source_roots)
        orbit_paths = self._resolve_orbit_roots(orbit_roots)
        scenes: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        platform_filter = str(platform or "").strip().upper()
        rel_filter = str(relative_orbit or "").strip()
        direction_filter = str(orbit_direction or "").strip().upper()

        for root in source_paths:
            try:
                for scene_dir in self._iter_lt1_scene_dirs(root):
                    try:
                        scene = self._parse_lt1_scene(scene_dir, orbit_paths)
                    except Exception as exc:
                        errors.append({"scene_dir": str(scene_dir), "error": str(exc)})
                        continue
                    if platform_filter and scene.get("satellite") != platform_filter:
                        continue
                    if rel_filter and str(scene.get("relative_orbit") or "") != rel_filter:
                        continue
                    if direction_filter and str(scene.get("orbit_direction") or "").upper() != direction_filter:
                        continue
                    scenes.append(scene)
            except Exception as exc:
                errors.append({"source_root": str(root), "error": str(exc)})

        grouped: dict[str, list[dict[str, Any]]] = {}
        for scene in scenes:
            grouped.setdefault(self._stack_group_key(scene), []).append(scene)

        candidates = [
            self._build_stack_candidate(group_scenes, min_scenes=min_scenes, require_orbits=require_orbits)
            for group_scenes in grouped.values()
        ]
        candidates.sort(
            key=lambda item: (
                int(item.get("status") != "READY"),
                -int(item.get("orbit_ready_scene_count") or 0),
                -int(item.get("scene_count") or 0),
                str(item.get("date_start") or ""),
            )
        )
        if not include_scenes:
            for candidate in candidates:
                candidate.pop("scenes", None)
        if limit > 0:
            candidates = candidates[:limit]

        snapshot = {
            "schema": "insar.sbas-stack-discovery/v1",
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "source_roots": [str(path) for path in source_paths],
            "orbit_roots": [str(path) for path in orbit_paths],
            "min_scenes": min_scenes,
            "require_orbits": require_orbits,
            "scene_count": len(scenes),
            "candidate_count": len(candidates),
            "errors": errors[:50],
            "items": candidates,
        }
        snapshot_path = self._write_runtime_json(
            "discoveries",
            f"discovery_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.json",
            snapshot,
        )
        snapshot["snapshot_path"] = str(snapshot_path)
        return snapshot

    def audit_stack(
        self,
        stack_id: str,
        *,
        source_roots: list[str] | None = None,
        orbit_roots: list[str] | None = None,
        min_scenes: int = 3,
        require_orbits: bool = True,
    ) -> dict[str, Any]:
        discovery = self.discover_stacks(
            source_roots=source_roots,
            orbit_roots=orbit_roots,
            min_scenes=min_scenes,
            require_orbits=require_orbits,
            include_scenes=True,
            limit=0,
        )
        candidate = next(
            (item for item in discovery.get("items", []) if item.get("stack_id") == stack_id),
            None,
        )
        if not candidate:
            raise FileNotFoundError(f"stack candidate not found: {stack_id}")

        usable_scenes = [
            scene for scene in candidate.get("scenes", [])
            if (scene.get("has_orbit") or not require_orbits)
        ]
        usable_scenes.sort(key=lambda item: str(item.get("date") or ""))
        pairs = self._build_adjacent_pairs(usable_scenes)
        blockers: list[str] = []
        warnings: list[str] = []

        if len(usable_scenes) < min_scenes:
            blockers.append(
                f"Only {len(usable_scenes)} usable scenes; minimum required is {min_scenes}."
            )
        if require_orbits and candidate.get("missing_orbit_count"):
            warnings.append(
                f"{candidate.get('missing_orbit_count')} scenes are excluded because precise orbit TXT is missing."
            )
        if len(pairs) < max(0, len(usable_scenes) - 1):
            blockers.append("Adjacent pair network is not fully connected.")
        for pair in pairs:
            if int(pair.get("delta_days") or 0) > 180:
                warnings.append(
                    f"Long temporal gap: {pair.get('master_date')} -> {pair.get('slave_date')} "
                    f"({pair.get('delta_days')} days)."
                )

        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
        manifest = {
            "schema": "insar.gamma-ipta-sbas-stack-manifest/v1",
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "stack_id": stack_id,
            "processor_code": "gamma_ipta_sbas",
            "engine_code": "gamma",
            "workflow": "Gamma DIFF + IPTA mb/ts_rate",
            "status": "READY_FOR_GAMMA_BASELINE_AUDIT" if not blockers else "BLOCKED",
            "require_orbits": require_orbits,
            "min_scenes": min_scenes,
            "stack": {
                key: candidate.get(key)
                for key in [
                    "satellite",
                    "satellite_mode",
                    "receiving_station",
                    "relative_orbit",
                    "orbit_direction",
                    "imaging_mode",
                    "polarization",
                    "center_bucket",
                    "reference_date",
                ]
            },
            "scenes": usable_scenes,
            "excluded_scenes": [
                scene for scene in candidate.get("scenes", [])
                if scene not in usable_scenes
            ],
            "pair_network": {
                "strategy": "adjacent_temporal_initial",
                "gamma_baseline_status": "PENDING",
                "pairs": pairs,
            },
            "blockers": blockers,
            "warnings": sorted(set(warnings)),
            "next_stage": "convert selected LT1 scenes with par_LT1_SLC, then run Gamma base_calc before final itab approval",
        }
        manifest_path = self._write_runtime_json(
            Path("stack_manifests") / stack_id,
            f"{timestamp}_stack_manifest.json",
            manifest,
        )
        pair_network_path = self._write_runtime_json(
            Path("stack_manifests") / stack_id,
            f"{timestamp}_pair_network.json",
            manifest["pair_network"],
        )
        return {
            "stack_id": stack_id,
            "status": manifest["status"],
            "manifest_path": str(manifest_path),
            "pair_network_path": str(pair_network_path),
            "manifest": manifest,
        }

    def create_run(
        self,
        stack_id: str,
        *,
        run_label: str | None = None,
        source_roots: list[str] | None = None,
        orbit_roots: list[str] | None = None,
        min_scenes: int = 3,
        require_orbits: bool = True,
        monitor_points: list[dict[str, Any]] | None = None,
        monitor_point_strategy: str = "auto_low_sigma_high_rate",
        dry_run: bool = True,
    ) -> dict[str, Any]:
        if not dry_run:
            raise ValueError("Gamma SBAS execution is not wired yet; submit with dry_run=true.")

        audit = self.audit_stack(
            stack_id,
            source_roots=source_roots,
            orbit_roots=orbit_roots,
            min_scenes=min_scenes,
            require_orbits=require_orbits,
        )
        manifest = audit["manifest"]
        if manifest.get("status") != "READY_FOR_GAMMA_BASELINE_AUDIT":
            raise ValueError("stack manifest is not ready for run planning")

        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        run_id = self._stable_id(f"{stack_id}|{timestamp}|{run_label or ''}")
        run_dir = self.production_root / "runs" / run_id
        work_dir = run_dir / "work"
        publish_dir = run_dir / "publish"
        log_dir = run_dir / "logs"
        for path in (work_dir, publish_dir, log_dir):
            path.mkdir(parents=True, exist_ok=True)

        monitor_config = self._build_monitor_point_config(
            monitor_points=monitor_points,
            strategy=monitor_point_strategy,
            stack_manifest=manifest,
        )
        run_manifest = {
            "schema": "insar.gamma-ipta-sbas-run/v1",
            "run_id": run_id,
            "run_label": run_label or None,
            "workflow_code": "sbas_insar",
            "processor_code": "gamma_ipta_sbas",
            "engine_code": "gamma",
            "execution_mode": "dry_run_plan",
            "status": "PLANNED_GAMMA_BASELINE_AUDIT",
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "stack_id": stack_id,
            "stack_manifest_path": audit["manifest_path"],
            "pair_network_path": audit["pair_network_path"],
            "work_root": str(work_dir),
            "publish_root": str(publish_dir),
            "log_root": str(log_dir),
            "stack": manifest.get("stack") or {},
            "scene_count": len(manifest.get("scenes") or []),
            "pair_count": len(((manifest.get("pair_network") or {}).get("pairs")) or []),
            "next_stage": "baseline_audit",
            "requires_user_action": [
                "Review Gamma base_calc baseline table before approving final itab.",
                "Confirm monitoring-point source: manual points, imported layer, or automatic sampler.",
                "Confirm geocoded preview products are published from EPSG:4326 GeoTIFFs.",
            ],
            "monitor_points": monitor_config,
            "dry_run": dry_run,
        }
        command_manifest = self._build_command_manifest(run_manifest, manifest)

        run_manifest_path = self._write_json(run_dir / "run_manifest.json", run_manifest)
        command_manifest_path = self._write_json(run_dir / "gamma_command_manifest.json", command_manifest)
        monitor_config_path = self._write_json(run_dir / "monitor_points.json", monitor_config)
        self._write_json(run_dir / "stack_manifest.json", manifest)
        self._write_json(run_dir / "pair_network.json", manifest.get("pair_network") or {})

        index_item = {
            **self._build_run_card(run_dir, run_manifest),
            "run_manifest_path": str(run_manifest_path),
            "gamma_command_manifest_path": str(command_manifest_path),
            "monitor_config_path": str(monitor_config_path),
        }
        return {
            "run": index_item,
            "manifest": run_manifest,
            "command_manifest": command_manifest,
            "monitor_points": monitor_config,
        }

    def list_runs(self) -> dict[str, Any]:
        run_root = self.production_root / "runs"
        items: list[dict[str, Any]] = []
        if not run_root.exists():
            return {"items": items, "count": 0, "run_root": str(run_root)}

        for manifest_path in sorted(run_root.glob("*/run_manifest.json")):
            try:
                manifest = self._read_json(manifest_path)
                items.append(self._build_run_card(manifest_path.parent, manifest))
            except Exception as exc:
                items.append(
                    {
                        "run_id": manifest_path.parent.name,
                        "status": "RUN_MANIFEST_UNREADABLE",
                        "run_dir": str(manifest_path.parent),
                        "error": str(exc),
                    }
                )
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return {"items": items, "count": len(items), "run_root": str(run_root)}

    def get_run_detail(self, run_id: str) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest = self._read_json(run_dir / "run_manifest.json")
        command_manifest = self._read_optional_json(run_dir / "gamma_command_manifest.json")
        monitor_points = self._read_optional_json(run_dir / "monitor_points.json")
        return {
            "run": self._build_run_card(run_dir, manifest),
            "manifest": manifest,
            "command_manifest": command_manifest,
            "monitor_points": monitor_points,
            "artifacts": self._build_run_artifacts(run_dir),
        }

    def run_baseline_audit(
        self,
        run_id: str,
        *,
        execute: bool = True,
        rlks: int = 8,
        azlks: int = 8,
        max_delta_n: int = 1,
        timeout_seconds: int = 21600,
    ) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        stack_manifest = self._read_json(run_dir / "stack_manifest.json")
        if manifest.get("status") not in {
            "PLANNED_GAMMA_BASELINE_AUDIT",
            "BASELINE_AUDIT_SCRIPT_READY",
            "BASELINE_AUDIT_FAILED",
            "BASELINE_AUDIT_READY",
        }:
            raise ValueError(f"run status does not allow baseline audit: {manifest.get('status')}")

        rlks = self._bounded_int(rlks, default=8, minimum=1, maximum=64)
        azlks = self._bounded_int(azlks, default=8, minimum=1, maximum=64)
        max_delta_n = self._bounded_int(max_delta_n, default=1, minimum=1, maximum=100)
        timeout_seconds = self._bounded_int(timeout_seconds, default=21600, minimum=60, maximum=86400)

        script_path = self._write_baseline_audit_script(
            run_dir,
            stack_manifest=stack_manifest,
            rlks=rlks,
            azlks=azlks,
            max_delta_n=max_delta_n,
        )
        manifest["baseline_audit"] = {
            "script_path": str(script_path),
            "rlks": rlks,
            "azlks": azlks,
            "max_delta_n": max_delta_n,
            "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        if not execute:
            baseline_summary = self._build_baseline_summary(run_dir)
            if baseline_summary.get("adjacent_pair_count"):
                manifest["status"] = "BASELINE_AUDIT_READY"
                manifest["next_stage"] = "approve_itab"
                manifest["baseline_audit"]["summary"] = baseline_summary
                manifest["baseline_audit"]["approved_for_next_stage"] = False
                self._write_json(run_dir / "baseline_audit_summary.json", baseline_summary)
                self._write_json(run_dir / "pair_network_baseline_audit.json", baseline_summary.get("pair_network") or {})
                self._write_json(run_dir / "pair_network.json", baseline_summary.get("pair_network") or {})
            else:
                manifest["status"] = "BASELINE_AUDIT_SCRIPT_READY"
                manifest["next_stage"] = "execute_baseline_audit"
            self._write_json(manifest_path, manifest)
            self._refresh_command_manifest_after_baseline(run_dir, manifest, baseline_summary if baseline_summary.get("adjacent_pair_count") else None)
            return self.get_run_detail(run_id)

        started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        script_wsl = self._windows_path_to_wsl_mount(str(script_path))
        command = self._baseline_execution_command(str(script_wsl))
        completed = subprocess.run(
            command,
            cwd=str(run_dir),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        execution = {
            "started_at": started_at,
            "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "command": command,
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
        }

        baseline_summary = self._build_baseline_summary(run_dir)
        manifest["baseline_audit"] = {
            **manifest["baseline_audit"],
            "execution": execution,
            "summary": baseline_summary,
        }
        if completed.returncode == 0 and baseline_summary.get("adjacent_pair_count"):
            manifest["status"] = "BASELINE_AUDIT_READY"
            manifest["next_stage"] = "approve_itab"
            manifest["baseline_audit"]["approved_for_next_stage"] = False
            self._write_json(run_dir / "baseline_audit_summary.json", baseline_summary)
            self._write_json(run_dir / "pair_network_baseline_audit.json", baseline_summary.get("pair_network") or {})
            self._write_json(run_dir / "pair_network.json", baseline_summary.get("pair_network") or {})
        else:
            manifest["status"] = "BASELINE_AUDIT_FAILED"
            manifest["next_stage"] = "fix_baseline_audit"

        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_baseline(run_dir, manifest, baseline_summary)
        return self.get_run_detail(run_id)

    def _baseline_execution_command(self, script_wsl: str) -> list[str]:
        return self._script_execution_command(script_wsl)

    def _script_execution_command(self, script_wsl: str) -> list[str]:
        if os.name != "nt":
            return ["bash", script_wsl]
        return [
            "wsl.exe",
            "-d",
            settings.WSL_DISTRO or settings.PYINT_WSL_DISTRO or "Ubuntu-24.04",
            "bash",
            script_wsl,
        ]

    def decide_itab(
        self,
        run_id: str,
        *,
        decision: str,
        reviewer: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        normalized_decision = str(decision or "").strip().lower()
        if normalized_decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        if manifest.get("status") in {"COREGISTRATION_SCRIPT_READY", "COREGISTRATION_RUNNING", "COREGISTRATION_READY"}:
            existing_decision = ((manifest.get("baseline_audit") or {}).get("itab_decision") or {}).get("decision")
            if normalized_decision == "approve" and existing_decision == "approve":
                return self.get_run_detail(run_id)
        if manifest.get("status") not in {"BASELINE_AUDIT_READY", "ITAB_APPROVED", "ITAB_REJECTED"}:
            raise ValueError(f"run status does not allow itab decision: {manifest.get('status')}")

        baseline_summary = self._read_optional_json(run_dir / "baseline_audit_summary.json")
        if not baseline_summary or not baseline_summary.get("adjacent_pair_count"):
            raise ValueError("baseline audit summary is missing or empty")

        decided_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        decision_payload = {
            "schema": "insar.sbas-itab-decision/v1",
            "run_id": run_id,
            "decision": normalized_decision,
            "reviewer": str(reviewer or "system").strip()[:120],
            "note": str(note or "").strip()[:1000],
            "decided_at": decided_at,
            "baseline_summary": {
                "adjacent_pair_count": baseline_summary.get("adjacent_pair_count"),
                "max_abs_bperp_m": baseline_summary.get("max_abs_bperp_m"),
                "max_delta_days": baseline_summary.get("max_delta_days"),
            },
        }

        baseline_state = manifest.setdefault("baseline_audit", {})
        if normalized_decision == "approve":
            source_itab = run_dir / "work" / "gamma" / "diff" / "itab_adjacent"
            if not source_itab.is_file():
                raise FileNotFoundError(f"Gamma adjacent itab not found: {source_itab}")
            approved_itab = run_dir / "work" / "gamma" / "diff" / "itab_approved"
            shutil.copyfile(source_itab, approved_itab)
            self._write_json(run_dir / "itab_decision.json", decision_payload)
            baseline_state["approved_for_next_stage"] = True
            baseline_state["itab_decision"] = decision_payload
            baseline_state["approved_itab_path"] = str(approved_itab)
            manifest["status"] = "ITAB_APPROVED"
            manifest["next_stage"] = "coregistration"
        else:
            self._write_json(run_dir / "itab_decision.json", decision_payload)
            baseline_state["approved_for_next_stage"] = False
            baseline_state["itab_decision"] = decision_payload
            manifest["status"] = "ITAB_REJECTED"
            manifest["next_stage"] = "revise_pair_network"

        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_itab_decision(run_dir, manifest)
        return self.get_run_detail(run_id)

    def prepare_coregistration(
        self,
        run_id: str,
        *,
        execute: bool = False,
        rlks: int = 8,
        azlks: int = 8,
    ) -> dict[str, Any]:
        if execute:
            raise ValueError("Coregistration execution is not enabled yet; submit with execute=false.")
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        if manifest.get("status") not in {"ITAB_APPROVED", "COREGISTRATION_SCRIPT_READY", "COREGISTRATION_FAILED"}:
            raise ValueError(f"run status does not allow coregistration preparation: {manifest.get('status')}")
        approved_itab = run_dir / "work" / "gamma" / "diff" / "itab_approved"
        if not approved_itab.is_file():
            raise FileNotFoundError(f"approved itab not found: {approved_itab}")
        stack_manifest = self._read_json(run_dir / "stack_manifest.json")
        scenes = sorted(stack_manifest.get("scenes") or [], key=lambda item: str(item.get("date") or ""))
        reference_date = str((stack_manifest.get("stack") or {}).get("reference_date") or "").strip()
        if reference_date not in {str(scene.get("date")) for scene in scenes}:
            reference_date = str(scenes[len(scenes) // 2].get("date"))

        rlks = self._bounded_int(rlks, default=8, minimum=1, maximum=64)
        azlks = self._bounded_int(azlks, default=8, minimum=1, maximum=64)
        itab_rows = self._parse_itab(approved_itab)
        if not itab_rows:
            raise ValueError("approved itab is empty")

        script_path = self._write_coregistration_script(
            run_dir,
            scenes=scenes,
            reference_date=reference_date,
            rlks=rlks,
            azlks=azlks,
        )
        coregistration = {
            "schema": "insar.gamma-coregistration-stage/v1",
            "strategy": "common_reference_to_stack_reference_date",
            "script_path": str(script_path),
            "approved_itab_path": str(approved_itab),
            "reference_date": reference_date,
            "scene_count": len(scenes),
            "approved_pair_count": len(itab_rows),
            "rlks": rlks,
            "azlks": azlks,
            "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "outputs": {
                "common_dir": str(run_dir / "work" / "gamma" / f"common_{reference_date}"),
                "slc_tab": str(run_dir / "work" / "gamma" / f"common_{reference_date}" / "SLC_tab"),
                "rmli_tab": str(run_dir / "work" / "gamma" / f"common_{reference_date}" / "RMLI_tab"),
            },
        }
        manifest["coregistration"] = coregistration
        manifest["status"] = "COREGISTRATION_SCRIPT_READY"
        manifest["next_stage"] = "execute_coregistration"
        self._write_json(run_dir / "coregistration_plan.json", coregistration)
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_coregistration(run_dir, manifest)
        return self.get_run_detail(run_id)

    def execute_coregistration(
        self,
        run_id: str,
        *,
        rlks: int = 8,
        azlks: int = 8,
        timeout_seconds: int = 43200,
    ) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = self._read_json(manifest_path)
        status = str(manifest.get("status") or "").strip()
        if status == "COREGISTRATION_READY":
            return self.get_run_detail(run_id)
        if status in {"ITAB_APPROVED", "COREGISTRATION_FAILED"}:
            self.prepare_coregistration(run_id, execute=False, rlks=rlks, azlks=azlks)
            manifest = self._read_json(manifest_path)
            status = str(manifest.get("status") or "").strip()
        if status not in {"COREGISTRATION_SCRIPT_READY", "COREGISTRATION_RUNNING"}:
            raise ValueError(f"run status does not allow coregistration execution: {manifest.get('status')}")

        coregistration = dict(manifest.get("coregistration") or {})
        script_path = Path(self._path_to_windows(str(coregistration.get("script_path") or "")) or "")
        if not script_path.is_file():
            raise FileNotFoundError(f"coregistration script not found: {script_path}")

        timeout_seconds = self._bounded_int(timeout_seconds, default=43200, minimum=60, maximum=172800)
        started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        command = self._script_execution_command(str(self._windows_path_to_wsl_mount(str(script_path))))

        coregistration["execution"] = {
            "started_at": started_at,
            "command": command,
            "timeout_seconds": timeout_seconds,
            "status": "RUNNING",
        }
        manifest["coregistration"] = coregistration
        manifest["status"] = "COREGISTRATION_RUNNING"
        manifest["next_stage"] = "coregistration"
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_coregistration(run_dir, manifest)

        try:
            completed = subprocess.run(
                command,
                cwd=str(run_dir),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            summary = self._build_coregistration_summary(
                run_dir,
                reference_date=coregistration.get("reference_date"),
            )
            execution = {
                **coregistration.get("execution", {}),
                "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "status": "TIMEOUT",
                "timed_out": True,
                "stdout_tail": self._tail_text(exc.stdout),
                "stderr_tail": self._tail_text(exc.stderr),
            }
            coregistration = {**coregistration, "execution": execution, "summary": summary}
            manifest["coregistration"] = coregistration
            manifest["status"] = "COREGISTRATION_FAILED"
            manifest["next_stage"] = "fix_coregistration"
            self._write_json(run_dir / "coregistration_summary.json", summary)
            self._write_json(manifest_path, manifest)
            self._refresh_command_manifest_after_coregistration(run_dir, manifest)
            raise

        summary = self._build_coregistration_summary(
            run_dir,
            reference_date=coregistration.get("reference_date"),
        )
        execution = {
            **coregistration.get("execution", {}),
            "ended_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "status": "COMPLETED" if completed.returncode == 0 else "FAILED",
            "returncode": completed.returncode,
            "stdout_tail": self._tail_text(completed.stdout),
            "stderr_tail": self._tail_text(completed.stderr),
        }
        coregistration = {**coregistration, "execution": execution, "summary": summary}
        manifest["coregistration"] = coregistration
        if completed.returncode == 0 and summary.get("ready"):
            manifest["status"] = "COREGISTRATION_READY"
            manifest["next_stage"] = "rdc_dem"
        else:
            manifest["status"] = "COREGISTRATION_FAILED"
            manifest["next_stage"] = "fix_coregistration"

        self._write_json(run_dir / "coregistration_summary.json", summary)
        self._write_json(manifest_path, manifest)
        self._refresh_command_manifest_after_coregistration(run_dir, manifest)
        return self.get_run_detail(run_id)

    def list_trial_runs(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        if not self.trial_root.exists():
            return {"items": items, "count": 0, "trial_root": str(self.trial_root)}

        for summary_path in sorted(self.trial_root.glob("*/publish/trial_summary.json")):
            try:
                summary = self._read_json(summary_path)
                items.append(self._build_trial_card(summary_path.parent.parent, summary))
            except Exception as exc:
                items.append(
                    {
                        "trial_id": summary_path.parent.parent.name,
                        "status": "SUMMARY_UNREADABLE",
                        "summary_path": str(summary_path),
                        "error": str(exc),
                    }
                )

        items.sort(key=lambda item: str(item.get("generated_at") or ""), reverse=True)
        return {"items": items, "count": len(items), "trial_root": str(self.trial_root)}

    def get_trial_detail(self, trial_id: str) -> dict[str, Any]:
        trial_dir = self._resolve_trial_dir(trial_id)
        summary_path = trial_dir / "publish" / "trial_summary.json"
        if not summary_path.is_file():
            raise FileNotFoundError(f"trial summary not found: {summary_path}")

        summary = self._read_json(summary_path)
        artifacts = self._build_artifacts(trial_dir)
        return {
            "trial": self._build_trial_card(trial_dir, summary),
            "summary": summary,
            "artifacts": artifacts,
            "stage_contract": [
                "par_LT1_SLC",
                "LT1_precision_orbit.py",
                "multi_look",
                "base_calc",
                "SLC_coreg.py",
                "gc_map1/geocode/gc_map_fine",
                "phase_sim_orb",
                "SLC_diff_intf",
                "adf",
                "mcf",
                "mb",
                "ts_rate",
                "geocode_back",
                "data2geotiff",
                "LOS sign conversion",
                "monitoring point time series",
            ],
        }

    def resolve_artifact_path(self, trial_id: str, relative_path: str) -> Path:
        trial_dir = self._resolve_trial_dir(trial_id)
        normalized = str(relative_path or "").replace("\\", "/").strip("/")
        if not normalized or normalized.startswith("../") or "/../" in normalized:
            raise ValueError("invalid artifact path")
        if not normalized.startswith("publish/"):
            raise ValueError("only published artifacts can be served")

        candidate = (trial_dir / normalized).resolve()
        trial_resolved = trial_dir.resolve()
        try:
            candidate.relative_to(trial_resolved)
        except ValueError as exc:
            raise ValueError("artifact path escapes trial root") from exc
        if not candidate.is_file():
            raise FileNotFoundError(f"artifact not found: {normalized}")
        return candidate

    def resolve_run_artifact_path(self, run_id: str, relative_path: str) -> Path:
        run_dir = self._resolve_run_dir(run_id)
        normalized = str(relative_path or "").replace("\\", "/").strip("/")
        if not normalized or normalized.startswith("../") or "/../" in normalized:
            raise ValueError("invalid artifact path")
        allowed_paths = {item["relative_path"] for item in self._build_run_artifacts(run_dir)}
        if normalized not in allowed_paths:
            raise ValueError("run artifact is not published")

        candidate = (run_dir / normalized).resolve()
        run_resolved = run_dir.resolve()
        try:
            candidate.relative_to(run_resolved)
        except ValueError as exc:
            raise ValueError("artifact path escapes run root") from exc
        if not candidate.is_file():
            raise FileNotFoundError(f"artifact not found: {normalized}")
        return candidate

    def _resolve_source_roots(self, roots: list[str] | None) -> list[Path]:
        raw_values = roots or self._split_config_paths(
            settings.SOURCE_PRODUCT_DIRS,
            settings.MONITOR_RADAR_DIRS,
            settings.INSAR_STORAGE_DIRS,
        )
        if not raw_values:
            raw_values = [r"D:\LuTan1_Image_Pool"]
        return self._dedupe_existing_dirs(raw_values)

    def _resolve_orbit_roots(self, roots: list[str] | None) -> list[Path]:
        raw_values = roots or self._split_config_paths(
            settings.PYINT_ORBIT_POOL_TXT,
            settings.ORBIT_POOL_ENVI,
        )
        if not raw_values:
            raw_values = [r"D:\orbit_pools\envi"]
        return self._dedupe_existing_dirs(raw_values)

    @staticmethod
    def _split_config_paths(*values: str) -> list[str]:
        paths: list[str] = []
        for value in values:
            for item in str(value or "").replace(";", ",").split(","):
                text = item.strip().strip('"').strip("'")
                if text:
                    paths.append(text)
        return paths

    @staticmethod
    def _dedupe_existing_dirs(values: list[str]) -> list[Path]:
        roots: list[Path] = []
        seen: set[str] = set()
        for value in values:
            for path in SbasInsarProductionService._existing_path_variants(value):
                key = os.path.normcase(str(path.resolve()))
                if key in seen:
                    continue
                seen.add(key)
                roots.append(path)
        return roots

    @staticmethod
    def _existing_path_variants(value: str) -> list[Path]:
        text = str(value or "").strip().strip('"').strip("'")
        if not text:
            return []
        candidates = [Path(os.path.normpath(text))]
        wsl_path = SbasInsarProductionService._windows_path_to_wsl_mount(text)
        if wsl_path and wsl_path != text:
            candidates.append(Path(wsl_path))
        windows_path = SbasInsarProductionService._path_to_windows(text)
        if windows_path and windows_path != text:
            candidates.append(Path(os.path.normpath(windows_path)))

        existing: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = os.path.normcase(str(candidate))
            if key in seen:
                continue
            seen.add(key)
            if candidate.is_dir():
                existing.append(candidate)
        return existing

    def _iter_lt1_scene_dirs(self, root: Path):
        if root.name.upper().startswith("LT1") and self._looks_like_lt1_scene_dir(root):
            yield root
            return

        try:
            children = list(root.iterdir())
        except OSError:
            return

        for child in children:
            if child.is_dir() and child.name.upper().startswith("LT1") and self._looks_like_lt1_scene_dir(child):
                yield child

        # Some source roots may have one extra grouping level. Keep recursion shallow
        # to avoid walking runtime work directories by accident.
        for child in children:
            if not child.is_dir() or child.name.startswith((".", "_")):
                continue
            if child.name.upper().startswith(("LT1A", "LT1B")):
                continue
            try:
                for grandchild in child.iterdir():
                    if (
                        grandchild.is_dir()
                        and grandchild.name.upper().startswith("LT1")
                        and self._looks_like_lt1_scene_dir(grandchild)
                    ):
                        yield grandchild
            except OSError:
                continue

    @staticmethod
    def _looks_like_lt1_scene_dir(path: Path) -> bool:
        try:
            return any(path.glob("*.meta.xml")) and any(
                list(path.glob("*.tiff")) + list(path.glob("*.tif"))
            )
        except OSError:
            return False

    def _parse_lt1_scene(self, scene_dir: Path, orbit_roots: list[Path]) -> dict[str, Any]:
        scene_name = scene_dir.name
        filename_meta = self._parse_lt1_scene_name(scene_name)
        meta_path = self._select_meta_file(scene_dir)
        tiff_path = self._select_tiff_file(scene_dir)
        xml_meta = self._parse_lt1_product_info(meta_path)
        meta = {**filename_meta, **{key: value for key, value in xml_meta.items() if value not in (None, "")}}

        date = str(meta.get("date") or "")[:8]
        satellite = str(meta.get("satellite") or "").upper()
        orbit_path = self._find_lt1_orbit(orbit_roots, satellite, date)
        center_lon = self._as_float(meta.get("center_lon"))
        center_lat = self._as_float(meta.get("center_lat"))
        return {
            "scene_name": scene_name,
            "scene_dir_windows": self._path_to_windows(str(scene_dir)),
            "scene_dir_wsl": self._windows_path_to_wsl_mount(str(scene_dir)),
            "tiff_windows": self._path_to_windows(str(tiff_path)),
            "tiff_wsl": self._windows_path_to_wsl_mount(str(tiff_path)),
            "meta_windows": self._path_to_windows(str(meta_path)),
            "meta_wsl": self._windows_path_to_wsl_mount(str(meta_path)),
            "orbit_windows": self._path_to_windows(str(orbit_path)) if orbit_path else None,
            "orbit_wsl": self._windows_path_to_wsl_mount(str(orbit_path)) if orbit_path else None,
            "has_orbit": bool(orbit_path),
            "date": date,
            "satellite": satellite,
            "satellite_mode": str(meta.get("satellite_mode") or "").upper() or None,
            "receiving_station": str(meta.get("receiving_station") or "").upper() or None,
            "absolute_orbit": str(meta.get("absolute_orbit") or "") or None,
            "relative_orbit": str(meta.get("relative_orbit") or "") or None,
            "orbit_direction": str(meta.get("orbit_direction") or "").upper() or None,
            "imaging_mode": str(meta.get("imaging_mode") or "").upper() or None,
            "look_direction": str(meta.get("look_direction") or "").upper() or None,
            "polarization": str(meta.get("polarization") or "").upper() or None,
            "product_type": str(meta.get("product_type") or "").upper() or None,
            "center_lon": center_lon,
            "center_lat": center_lat,
            "center_bucket": self._center_bucket(center_lon, center_lat),
            "bbox": meta.get("bbox"),
            "start_time_utc": meta.get("start_time_utc"),
            "stop_time_utc": meta.get("stop_time_utc"),
        }

    @staticmethod
    def _parse_lt1_scene_name(scene_name: str) -> dict[str, Any]:
        match = LT1_SCENE_RE.match(scene_name)
        if not match:
            return {}
        data = match.groupdict()
        return {
            "satellite": data.get("satellite", "").upper(),
            "satellite_mode": data.get("satellite_mode", "").upper(),
            "receiving_station": data.get("receiving_station", "").upper(),
            "imaging_mode": data.get("imaging_mode", "").upper(),
            "absolute_orbit": data.get("absolute_orbit"),
            "center_lon": data.get("center_lon"),
            "center_lat": data.get("center_lat"),
            "date": data.get("date"),
            "product_type": data.get("product_type", "").upper(),
            "polarization": data.get("polarization", "").upper(),
        }

    @staticmethod
    def _select_meta_file(scene_dir: Path) -> Path:
        candidates = sorted(scene_dir.glob("*.meta.xml"))
        if not candidates:
            raise FileNotFoundError(f"No LT1 meta XML found in {scene_dir}")
        return candidates[0]

    @staticmethod
    def _select_tiff_file(scene_dir: Path) -> Path:
        candidates = sorted(list(scene_dir.glob("*.tiff")) + list(scene_dir.glob("*.tif")))
        if not candidates:
            raise FileNotFoundError(f"No LT1 TIFF found in {scene_dir}")
        slc_candidates = [path for path in candidates if "_SLC_" in path.name.upper()]
        return slc_candidates[0] if slc_candidates else candidates[0]

    def _parse_lt1_product_info(self, meta_path: Path) -> dict[str, Any]:
        text = meta_path.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r"<productInfo\b[^>]*>.*?</productInfo>", text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return {}
        root = ET.fromstring(match.group(0))
        corners: list[tuple[float, float]] = []
        for element in root.findall(".//sceneCornerCoord"):
            lat = self._as_float(self._child_text(element, "lat"))
            lon = self._as_float(self._child_text(element, "lon"))
            if lat is not None and lon is not None:
                corners.append((lon, lat))
        bbox = None
        if corners:
            lons = [item[0] for item in corners]
            lats = [item[1] for item in corners]
            bbox = {
                "min_lon": min(lons),
                "min_lat": min(lats),
                "max_lon": max(lons),
                "max_lat": max(lats),
            }
        center = root.find(".//sceneCenterCoord")
        return {
            "satellite": self._find_text(root, ".//missionInfo/mission"),
            "absolute_orbit": self._find_text(root, ".//missionInfo/absOrbit"),
            "relative_orbit": self._find_text(root, ".//missionInfo/relOrbit"),
            "orbit_direction": self._find_text(root, ".//missionInfo/orbitDirection"),
            "receiving_station": self._find_text(root, ".//generationInfo/receivingStation"),
            "imaging_mode": self._find_text(root, ".//acquisitionInfo/imagingMode"),
            "look_direction": self._find_text(root, ".//acquisitionInfo/lookDirection"),
            "polarization": (
                self._find_text(root, ".//acquisitionInfo/polarisationMode")
                or self._find_text(root, ".//acquisitionInfo/polarisationList/polLayer")
            ),
            "start_time_utc": self._find_text(root, ".//sceneInfo/start/timeUTC"),
            "stop_time_utc": self._find_text(root, ".//sceneInfo/stop/timeUTC"),
            "date": self._date_from_time(self._find_text(root, ".//sceneInfo/start/timeUTC")),
            "center_lon": self._child_text(center, "lon") if center is not None else None,
            "center_lat": self._child_text(center, "lat") if center is not None else None,
            "bbox": bbox,
        }

    @staticmethod
    def _find_text(root: ET.Element, path: str) -> str | None:
        element = root.find(path)
        if element is None or element.text is None:
            return None
        text = element.text.strip()
        return text or None

    @staticmethod
    def _child_text(root: ET.Element | None, name: str) -> str | None:
        if root is None:
            return None
        element = root.find(name)
        if element is None or element.text is None:
            return None
        text = element.text.strip()
        return text or None

    @staticmethod
    def _date_from_time(value: str | None) -> str | None:
        text = str(value or "").strip()
        if len(text) >= 10:
            return text[:10].replace("-", "")
        return None

    @staticmethod
    def _as_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _center_bucket(lon: float | None, lat: float | None) -> str:
        if lon is None or lat is None:
            return "UNKNOWN_CENTER"
        return f"E{lon:.1f}_N{lat:.1f}"

    @staticmethod
    def _windows_path_to_wsl_mount(path: str | None) -> str | None:
        text = str(path or "").strip()
        if not text:
            return None
        normalized_posix = text.replace("\\", "/")
        wsl_match = re.match(r"^/mnt/([a-zA-Z])/(.*)$", normalized_posix)
        if wsl_match:
            return f"/mnt/{wsl_match.group(1).lower()}/{wsl_match.group(2)}"
        drive_match = re.match(r"^([a-zA-Z]):/(.*)$", normalized_posix)
        if drive_match:
            return f"/mnt/{drive_match.group(1).lower()}/{drive_match.group(2).lstrip('/')}"
        drive, tail = os.path.splitdrive(os.path.normpath(text))
        if not drive:
            return text.replace("\\", "/")
        return f"/mnt/{drive.rstrip(':').lower()}/{tail.replace(os.sep, '/').lstrip('/')}"

    @staticmethod
    def _path_to_windows(path: str | None) -> str | None:
        text = str(path or "").strip()
        if not text:
            return None
        normalized_posix = text.replace("\\", "/")
        wsl_match = re.match(r"^/mnt/([a-zA-Z])/(.*)$", normalized_posix)
        if wsl_match:
            drive = wsl_match.group(1).upper()
            tail = wsl_match.group(2).replace("/", "\\")
            return f"{drive}:\\{tail}"
        return os.path.normpath(text)

    @staticmethod
    def _find_lt1_orbit(orbit_roots: list[Path], satellite: str, date: str) -> Path | None:
        if not satellite or not date:
            return None
        name = f"{satellite}_GpsData_GAS_C_{date}.txt"
        for root in orbit_roots:
            candidates = [
                root / satellite / name,
                root / name,
            ]
            for candidate in candidates:
                if candidate.is_file():
                    return candidate
        return None

    @staticmethod
    def _stack_group_key(scene: dict[str, Any]) -> str:
        parts = [
            scene.get("satellite"),
            scene.get("satellite_mode"),
            scene.get("receiving_station"),
            scene.get("relative_orbit"),
            scene.get("orbit_direction"),
            scene.get("imaging_mode"),
            scene.get("polarization"),
            scene.get("center_bucket"),
        ]
        return "|".join(str(part or "") for part in parts)

    def _build_stack_candidate(
        self,
        scenes: list[dict[str, Any]],
        *,
        min_scenes: int,
        require_orbits: bool,
    ) -> dict[str, Any]:
        scenes = sorted(scenes, key=lambda item: str(item.get("date") or ""))
        first = scenes[0]
        orbit_ready = [scene for scene in scenes if scene.get("has_orbit")]
        usable = orbit_ready if require_orbits else scenes
        dates = [scene.get("date") for scene in scenes if scene.get("date")]
        usable_dates = [scene.get("date") for scene in usable if scene.get("date")]
        group_key = self._stack_group_key(first)
        stack_id = self._stable_id(group_key)
        temporal_gaps = self._temporal_gaps(usable_dates)
        blockers: list[str] = []
        if len(usable) < min_scenes:
            blockers.append(f"usable_scene_count {len(usable)} < min_scenes {min_scenes}")
        if require_orbits and len(orbit_ready) < len(scenes):
            blockers.append("missing precise orbit for one or more scenes")
        return {
            "stack_id": stack_id,
            "status": "READY" if not blockers else "BLOCKED",
            "blockers": blockers,
            "group_key": group_key,
            "satellite": first.get("satellite"),
            "satellite_mode": first.get("satellite_mode"),
            "receiving_station": first.get("receiving_station"),
            "relative_orbit": first.get("relative_orbit"),
            "orbit_direction": first.get("orbit_direction"),
            "imaging_mode": first.get("imaging_mode"),
            "polarization": first.get("polarization"),
            "center_bucket": first.get("center_bucket"),
            "scene_count": len(scenes),
            "orbit_ready_scene_count": len(orbit_ready),
            "usable_scene_count": len(usable),
            "missing_orbit_count": len(scenes) - len(orbit_ready),
            "date_start": dates[0] if dates else None,
            "date_end": dates[-1] if dates else None,
            "dates": dates,
            "usable_dates": usable_dates,
            "reference_date": usable_dates[len(usable_dates) // 2] if usable_dates else None,
            "temporal_gaps_days": temporal_gaps,
            "max_temporal_gap_days": max(temporal_gaps) if temporal_gaps else 0,
            "bbox_intersection": self._bbox_intersection([scene.get("bbox") for scene in usable]),
            "scenes": scenes,
        }

    @staticmethod
    def _stable_id(value: str) -> str:
        digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"sbas_{digest}"

    @staticmethod
    def _temporal_gaps(dates: list[str]) -> list[int]:
        parsed: list[datetime] = []
        for date in sorted(set(dates)):
            try:
                parsed.append(datetime.strptime(date, "%Y%m%d"))
            except ValueError:
                continue
        return [
            int((parsed[index + 1] - parsed[index]).days)
            for index in range(len(parsed) - 1)
        ]

    @staticmethod
    def _bbox_intersection(items: list[dict[str, Any] | None]) -> dict[str, float] | None:
        boxes = [item for item in items if item]
        if not boxes:
            return None
        min_lon = max(float(item["min_lon"]) for item in boxes)
        min_lat = max(float(item["min_lat"]) for item in boxes)
        max_lon = min(float(item["max_lon"]) for item in boxes)
        max_lat = min(float(item["max_lat"]) for item in boxes)
        if min_lon >= max_lon or min_lat >= max_lat:
            return None
        return {
            "min_lon": min_lon,
            "min_lat": min_lat,
            "max_lon": max_lon,
            "max_lat": max_lat,
        }

    @staticmethod
    def _build_adjacent_pairs(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        pairs: list[dict[str, Any]] = []
        for index in range(len(scenes) - 1):
            master = scenes[index]
            slave = scenes[index + 1]
            delta_days = None
            try:
                delta_days = int(
                    (
                        datetime.strptime(str(slave.get("date")), "%Y%m%d")
                        - datetime.strptime(str(master.get("date")), "%Y%m%d")
                    ).days
                )
            except ValueError:
                pass
            pairs.append(
                {
                    "pair_index": index + 1,
                    "master_date": master.get("date"),
                    "slave_date": slave.get("date"),
                    "delta_days": delta_days,
                    "master_scene_name": master.get("scene_name"),
                    "slave_scene_name": slave.get("scene_name"),
                    "itab_row_initial": [index + 1, index + 2, index + 1, 1],
                    "gamma_baseline_status": "PENDING",
                }
            )
        return pairs

    def _write_runtime_json(self, relative_dir: str | Path, filename: str, payload: dict[str, Any]) -> Path:
        out_dir = self.production_root / relative_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return out_path

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def _resolve_trial_dir(self, trial_id: str) -> Path:
        clean_id = str(trial_id or "").strip()
        if not clean_id or Path(clean_id).name != clean_id:
            raise ValueError("invalid trial id")
        trial_dir = (self.trial_root / clean_id).resolve()
        root_resolved = self.trial_root.resolve()
        try:
            trial_dir.relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError("trial id escapes trial root") from exc
        if not trial_dir.is_dir():
            raise FileNotFoundError(f"trial not found: {clean_id}")
        return trial_dir

    def _resolve_run_dir(self, run_id: str) -> Path:
        clean_id = str(run_id or "").strip()
        if not clean_id or Path(clean_id).name != clean_id:
            raise ValueError("invalid run id")
        run_dir = (self.production_root / "runs" / clean_id).resolve()
        root_resolved = (self.production_root / "runs").resolve()
        try:
            run_dir.relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError("run id escapes production root") from exc
        if not run_dir.is_dir():
            raise FileNotFoundError(f"run not found: {clean_id}")
        return run_dir

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _read_optional_json(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        return self._read_json(path)

    def _build_monitor_point_config(
        self,
        *,
        monitor_points: list[dict[str, Any]] | None,
        strategy: str,
        stack_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_points = [self._normalize_monitor_point(item, index) for index, item in enumerate(monitor_points or [])]
        if normalized_points:
            mode = "manual_lonlat"
            note = "Manual monitoring points are stored for extraction after geocoded products are available."
        else:
            mode = strategy or "auto_low_sigma_high_rate"
            note = (
                "Automatic point is only a production placeholder until users provide a point layer "
                "or approve a quality-filtered sampler."
            )
        return {
            "schema": "insar.sbas-monitor-points/v1",
            "mode": mode,
            "points": normalized_points,
            "default_auto_strategy": {
                "key": "auto_low_sigma_high_rate",
                "selection": "low LOS sigma, high absolute LOS velocity, non-edge valid pixel",
                "usage": "debug/sample only; not a business monitoring network",
            },
            "reference_date": (stack_manifest.get("stack") or {}).get("reference_date"),
            "coordinate_system": "EPSG:4326 for manual lon/lat points; radar coordinates are derived during publishing",
            "note": note,
        }

    def _normalize_monitor_point(self, item: dict[str, Any], index: int) -> dict[str, Any]:
        lon = self._as_float(item.get("lon") if item.get("lon") is not None else item.get("longitude"))
        lat = self._as_float(item.get("lat") if item.get("lat") is not None else item.get("latitude"))
        if lon is None or lat is None:
            raise ValueError(f"monitor point {index + 1} requires lon/lat")
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError(f"monitor point {index + 1} lon/lat out of range")
        point_id = str(item.get("point_id") or item.get("id") or f"manual_{index + 1:03d}").strip()
        if not re.match(r"^[A-Za-z0-9_.-]{1,64}$", point_id):
            raise ValueError(f"monitor point {index + 1} has invalid point_id")
        return {
            "point_id": point_id,
            "lon": lon,
            "lat": lat,
            "label": str(item.get("label") or point_id).strip()[:120],
            "source": "manual_lonlat",
        }

    @staticmethod
    def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(maximum, number))

    def _write_baseline_audit_script(
        self,
        run_dir: Path,
        *,
        stack_manifest: dict[str, Any],
        rlks: int,
        azlks: int,
        max_delta_n: int,
    ) -> Path:
        scenes = sorted(stack_manifest.get("scenes") or [], key=lambda item: str(item.get("date") or ""))
        if len(scenes) < 2:
            raise ValueError("baseline audit requires at least two scenes")
        reference_date = str((stack_manifest.get("stack") or {}).get("reference_date") or "").strip()
        if reference_date not in {str(scene.get("date")) for scene in scenes}:
            reference_date = str(scenes[len(scenes) // 2].get("date"))

        scripts_dir = run_dir / "scripts"
        script_path = scripts_dir / "01_baseline_audit.sh"
        gamma_root = run_dir / "work" / "gamma"
        slc_dir = gamma_root / "slc"
        mli_dir = gamma_root / "mli"
        diff_dir = gamma_root / "diff"
        log_dir = run_dir / "logs"
        python_bin = settings.WSL_SHARED_PYTHON or settings.PYINT_WSL_PYTHON or "/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python"
        env_script = (
            self._windows_path_to_wsl_mount(settings.PYINT_GAMMA_ENV_SCRIPT)
            or f"{self._windows_path_to_wsl_mount(settings.PROJECT_ROOT)}/deploy/wsl/profiles/gamma_env.sh"
        )

        lines = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            f'RUN_ROOT="{self._windows_path_to_wsl_mount(str(run_dir))}"',
            f'SLC_DIR="{self._windows_path_to_wsl_mount(str(slc_dir))}"',
            f'MLI_DIR="{self._windows_path_to_wsl_mount(str(mli_dir))}"',
            f'DIFF_DIR="{self._windows_path_to_wsl_mount(str(diff_dir))}"',
            f'LOG_DIR="{self._windows_path_to_wsl_mount(str(log_dir))}"',
            f'PYTHON_BIN="{python_bin}"',
            f'ORBIT_SCRIPT="${{GAMMA_HOME:-/usr/local/GAMMA_SOFTWARE-20240627}}/ISP/scripts/LT1_precision_orbit.py"',
            f'RLKS="{rlks}"',
            f'AZLKS="{azlks}"',
            f'REF_DATE="{reference_date}"',
            f'MAX_DELTA_N="{max_delta_n}"',
            "",
            f'source "{env_script}" >/dev/null 2>&1',
            'ORBIT_SCRIPT="${GAMMA_HOME}/ISP/scripts/LT1_precision_orbit.py"',
            'mkdir -p "${SLC_DIR}" "${MLI_DIR}" "${DIFF_DIR}" "${LOG_DIR}"',
            "",
            "run_scene() {",
            '  local date="$1"',
            '  local tiff="$2"',
            '  local meta="$3"',
            '  local orbit="$4"',
            '  local slc="${SLC_DIR}/${date}.slc"',
            '  local par="${SLC_DIR}/${date}.slc.par"',
            '  local log="${LOG_DIR}/${date}_slc_prepare.log"',
            '  {',
            '    echo "== ${date} SLC prepare =="',
            '    echo "tiff=${tiff}"',
            '    echo "meta=${meta}"',
            '    echo "orbit=${orbit}"',
            '    test -r "${tiff}"',
            '    test -r "${meta}"',
            '    test -r "${orbit}"',
            '    if [ ! -s "${slc}" ] || [ ! -s "${par}" ]; then',
            '      rm -f "${slc}" "${par}"',
            '      par_LT1_SLC "${tiff}" "${meta}" "${par}" "${slc}"',
            "    else",
            '      echo "SLC already exists, skipping par_LT1_SLC"',
            "    fi",
            '    if [ ! -s "${par}.before_precision_orbit" ]; then',
            '      cp -f "${par}" "${par}.before_precision_orbit"',
            '      "${PYTHON_BIN}" "${ORBIT_SCRIPT}" "${par}" "${orbit}"',
            "    else",
            '      echo "Precision-orbit backup exists, assuming orbit correction is already applied"',
            "    fi",
            '    test -s "${slc}"',
            '    test -s "${par}"',
            '    ls -lh "${slc}" "${par}" "${par}.before_precision_orbit"',
            '  } >"${log}" 2>&1',
            "}",
            "",
            "run_multilook() {",
            '  local date="$1"',
            '  local slc="${SLC_DIR}/${date}.slc"',
            '  local slc_par="${SLC_DIR}/${date}.slc.par"',
            '  local mli="${MLI_DIR}/${date}.mli"',
            '  local mli_par="${MLI_DIR}/${date}.mli.par"',
            '  local log="${LOG_DIR}/${date}_multi_look.log"',
            '  {',
            '    echo "== ${date} multi_look rlks=${RLKS} azlks=${AZLKS} =="',
            '    test -s "${slc}"',
            '    test -s "${slc_par}"',
            '    if [ ! -s "${mli}" ] || [ ! -s "${mli_par}" ]; then',
            '      multi_look "${slc}" "${slc_par}" "${mli}" "${mli_par}" "${RLKS}" "${AZLKS}"',
            "    else",
            '      echo "MLI already exists, skipping multi_look"',
            "    fi",
            '    ls -lh "${mli}" "${mli_par}"',
            '  } >"${log}" 2>&1',
            "}",
            "",
        ]
        for scene in scenes:
            date = str(scene.get("date") or "")
            lines.append(
                "run_scene "
                f'"{date}" '
                f'"{scene.get("tiff_wsl")}" '
                f'"{scene.get("meta_wsl")}" '
                f'"{scene.get("orbit_wsl")}"'
            )
        lines.extend(
            [
                "",
                ': >"${SLC_DIR}/SLC_tab"',
            ]
        )
        for scene in scenes:
            date = str(scene.get("date") or "")
            lines.append(f'printf "%s %s\\n" "${{SLC_DIR}}/{date}.slc" "${{SLC_DIR}}/{date}.slc.par" >>"${{SLC_DIR}}/SLC_tab"')
        lines.append("")
        for scene in scenes:
            date = str(scene.get("date") or "")
            lines.append(f'run_multilook "{date}"')
        lines.extend(
            [
                "",
                ': >"${MLI_DIR}/RMLI_tab"',
            ]
        )
        for scene in scenes:
            date = str(scene.get("date") or "")
            lines.append(f'printf "%s %s\\n" "${{MLI_DIR}}/{date}.mli" "${{MLI_DIR}}/{date}.mli.par" >>"${{MLI_DIR}}/RMLI_tab"')
        lines.extend(
            [
                "",
                'base_calc "${SLC_DIR}/SLC_tab" "${SLC_DIR}/${REF_DATE}.slc.par" "${DIFF_DIR}/bperp_all_pairs.txt" "${DIFF_DIR}/itab_all_pairs" 1 0 - - 1 3650 - >"${LOG_DIR}/base_calc_all_pairs.log" 2>&1',
                'base_calc "${SLC_DIR}/SLC_tab" "${SLC_DIR}/${REF_DATE}.slc.par" "${DIFF_DIR}/bperp_adjacent.txt" "${DIFF_DIR}/itab_adjacent" 1 0 - - 1 3650 "${MAX_DELTA_N}" >"${LOG_DIR}/base_calc_adjacent.log" 2>&1',
                'du -h "${SLC_DIR}"/* "${MLI_DIR}"/* "${DIFF_DIR}"/* | sort -h >"${LOG_DIR}/baseline_audit_inventory.txt"',
                'echo "baseline audit complete: ${DIFF_DIR}/bperp_adjacent.txt"',
                "",
            ]
        )
        scripts_dir.mkdir(parents=True, exist_ok=True)
        script_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
        return script_path

    def _write_coregistration_script(
        self,
        run_dir: Path,
        *,
        scenes: list[dict[str, Any]],
        reference_date: str,
        rlks: int,
        azlks: int,
    ) -> Path:
        scripts_dir = run_dir / "scripts"
        script_path = scripts_dir / "02_coreg_common_ref.sh"
        gamma_root = run_dir / "work" / "gamma"
        slc_dir = gamma_root / "slc"
        mli_dir = gamma_root / "mli"
        diff_dir = gamma_root / "diff"
        common_dir = gamma_root / f"common_{reference_date}"
        common_rslc_dir = common_dir / "rslc"
        common_rmli_dir = common_dir / "rmli"
        log_dir = run_dir / "logs"
        python_bin = settings.WSL_SHARED_PYTHON or settings.PYINT_WSL_PYTHON or "/home/administrator/miniconda3/envs/insar_wsl_v1/bin/python"
        env_script = (
            self._windows_path_to_wsl_mount(settings.PYINT_GAMMA_ENV_SCRIPT)
            or f"{self._windows_path_to_wsl_mount(settings.PROJECT_ROOT)}/deploy/wsl/profiles/gamma_env.sh"
        )
        dates = [str(scene.get("date") or "") for scene in scenes if scene.get("date")]
        lines = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "",
            f'RUN_ROOT="{self._windows_path_to_wsl_mount(str(run_dir))}"',
            f'SLC_DIR="{self._windows_path_to_wsl_mount(str(slc_dir))}"',
            f'MLI_DIR="{self._windows_path_to_wsl_mount(str(mli_dir))}"',
            f'DIFF_DIR="{self._windows_path_to_wsl_mount(str(diff_dir))}"',
            f'COMMON_DIR="{self._windows_path_to_wsl_mount(str(common_dir))}"',
            f'COMMON_RSLC_DIR="{self._windows_path_to_wsl_mount(str(common_rslc_dir))}"',
            f'COMMON_RMLI_DIR="{self._windows_path_to_wsl_mount(str(common_rmli_dir))}"',
            f'LOG_DIR="{self._windows_path_to_wsl_mount(str(log_dir))}"',
            f'PYTHON_BIN="{python_bin}"',
            f'REF_DATE="{reference_date}"',
            f'RLKS="{rlks}"',
            f'AZLKS="{azlks}"',
            "",
            f'source "{env_script}" >/dev/null 2>&1',
            'SLC_COREG="${GAMMA_HOME}/DIFF/scripts/SLC_coreg.py"',
            'APPROVED_ITAB="${DIFF_DIR}/itab_approved"',
            'test -s "${APPROVED_ITAB}"',
            'mkdir -p "${COMMON_RSLC_DIR}" "${COMMON_RMLI_DIR}" "${LOG_DIR}"',
            "",
            "DATES=(",
        ]
        lines.extend(f'  "{date}"' for date in dates)
        lines.extend(
            [
                ")",
                "",
                'REF_SLC="${SLC_DIR}/${REF_DATE}.slc"',
                'REF_PAR="${SLC_DIR}/${REF_DATE}.slc.par"',
                "",
                "coreg_to_ref() {",
                '  local date="$1"',
                '  local slc="${SLC_DIR}/${date}.slc"',
                '  local par="${SLC_DIR}/${date}.slc.par"',
                '  local rslc="${COMMON_RSLC_DIR}/${date}.rslc"',
                '  local rslc_par="${COMMON_RSLC_DIR}/${date}.rslc.par"',
                '  local rmli="${COMMON_RMLI_DIR}/${date}.mli"',
                '  local rmli_par="${COMMON_RMLI_DIR}/${date}.mli.par"',
                '  local gamma_off="${SLC_DIR}/${date}.slc.off"',
                '  local off="${COMMON_RSLC_DIR}/${date}_to_${REF_DATE}.off"',
                '  {',
                '    echo "== common-reference coreg ${date} -> ${REF_DATE} =="',
                '    test -s "${slc}"',
                '    test -s "${par}"',
                '    test -s "${REF_SLC}"',
                '    test -s "${REF_PAR}"',
                '    if [ "${date}" = "${REF_DATE}" ]; then',
                '      echo "reference date, no resampling needed"',
                '      return',
                '    fi',
                '    if [ ! -s "${rslc}" ] || [ ! -s "${rslc_par}" ] || [ ! -s "${rmli}" ] || [ ! -s "${rmli_par}" ] || [ ! -s "${off}" ]; then',
                '      rm -f "${rslc}" "${rslc_par}" "${rmli}" "${rmli_par}" "${off}"',
                '      "${PYTHON_BIN}" "${SLC_COREG}" \\',
                '        "${slc}" "${par}" \\',
                '        "${rslc}" "${rslc_par}" \\',
                '        "${rmli}" "${rmli_par}" \\',
                '        "${REF_SLC}" "${REF_PAR}" \\',
                '        0.1 "${RLKS}" "${AZLKS}" \\',
                '        --init_offset',
                '      test -s "${gamma_off}"',
                '      cp -f "${gamma_off}" "${off}"',
                "    else",
                '      echo "common-reference RSLC/coreg outputs already exist, skipping"',
                "    fi",
                '    test -s "${rslc}"',
                '    test -s "${rslc_par}"',
                '    test -s "${rmli}"',
                '    test -s "${rmli_par}"',
                '    test -s "${off}"',
                '    ls -lh "${rslc}" "${rslc_par}" "${rmli}" "${rmli_par}" "${off}" "${rslc}.coreg_quality"',
                '  } >"${LOG_DIR}/${date}_to_${REF_DATE}_common_coreg.log" 2>&1',
                "}",
                "",
                "slc_path() {",
                '  local date="$1"',
                '  if [ "${date}" = "${REF_DATE}" ]; then',
                '    printf "%s %s\\n" "${SLC_DIR}/${date}.slc" "${SLC_DIR}/${date}.slc.par"',
                "  else",
                '    printf "%s %s\\n" "${COMMON_RSLC_DIR}/${date}.rslc" "${COMMON_RSLC_DIR}/${date}.rslc.par"',
                "  fi",
                "}",
                "",
                "rmli_path() {",
                '  local date="$1"',
                '  if [ "${date}" = "${REF_DATE}" ]; then',
                '    printf "%s %s\\n" "${MLI_DIR}/${date}.mli" "${MLI_DIR}/${date}.mli.par"',
                "  else",
                '    printf "%s %s\\n" "${COMMON_RMLI_DIR}/${date}.mli" "${COMMON_RMLI_DIR}/${date}.mli.par"',
                "  fi",
                "}",
                "",
                'for date in "${DATES[@]}"; do',
                '  coreg_to_ref "${date}"',
                "done",
                "",
                ': >"${COMMON_DIR}/SLC_tab"',
                ': >"${COMMON_DIR}/RMLI_tab"',
                'for date in "${DATES[@]}"; do',
                '  slc_path "${date}" >>"${COMMON_DIR}/SLC_tab"',
                '  rmli_path "${date}" >>"${COMMON_DIR}/RMLI_tab"',
                "done",
                "",
                'cp -f "${APPROVED_ITAB}" "${COMMON_DIR}/itab_approved"',
                'du -h "${COMMON_DIR}"/* "${COMMON_RSLC_DIR}"/* "${COMMON_RMLI_DIR}"/* 2>/dev/null | sort -h >"${LOG_DIR}/coregistration_inventory.txt"',
                'echo "coregistration script complete: ${COMMON_DIR}"',
                "",
            ]
        )
        scripts_dir.mkdir(parents=True, exist_ok=True)
        script_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
        return script_path

    def _build_baseline_summary(self, run_dir: Path) -> dict[str, Any]:
        diff_dir = run_dir / "work" / "gamma" / "diff"
        all_pairs = self._parse_bperp_table(diff_dir / "bperp_all_pairs.txt")
        adjacent_pairs = self._parse_bperp_table(diff_dir / "bperp_adjacent.txt")
        itab_rows = self._parse_itab(diff_dir / "itab_adjacent")
        pair_network = {
            "strategy": "gamma_base_calc_adjacent",
            "gamma_baseline_status": "READY" if adjacent_pairs else "EMPTY",
            "pairs": [],
        }
        for index, pair in enumerate(adjacent_pairs):
            itab = itab_rows[index] if index < len(itab_rows) else None
            pair_network["pairs"].append(
                {
                    "pair_index": pair.get("pair_index"),
                    "master_date": pair.get("master_date"),
                    "slave_date": pair.get("slave_date"),
                    "delta_days": pair.get("delta_days"),
                    "bperp_m": pair.get("bperp_m"),
                    "itab_row": itab,
                    "gamma_baseline_status": "READY",
                }
            )
        bperps = [abs(float(item["bperp_m"])) for item in adjacent_pairs if item.get("bperp_m") is not None]
        gaps = [float(item["delta_days"]) for item in adjacent_pairs if item.get("delta_days") is not None]
        return {
            "schema": "insar.gamma-baseline-audit/v1",
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "all_pair_count": len(all_pairs),
            "adjacent_pair_count": len(adjacent_pairs),
            "max_abs_bperp_m": max(bperps) if bperps else None,
            "mean_abs_bperp_m": sum(bperps) / len(bperps) if bperps else None,
            "max_delta_days": max(gaps) if gaps else None,
            "all_pairs": all_pairs,
            "adjacent_pairs": adjacent_pairs,
            "itab_adjacent": itab_rows,
            "pair_network": pair_network,
            "outputs": {
                "bperp_all_pairs": str(diff_dir / "bperp_all_pairs.txt"),
                "bperp_adjacent": str(diff_dir / "bperp_adjacent.txt"),
                "itab_all_pairs": str(diff_dir / "itab_all_pairs"),
                "itab_adjacent": str(diff_dir / "itab_adjacent"),
            },
        }

    def _build_coregistration_summary(
        self,
        run_dir: Path,
        *,
        reference_date: str | None,
    ) -> dict[str, Any]:
        stack_manifest = self._read_json(run_dir / "stack_manifest.json")
        scenes = sorted(stack_manifest.get("scenes") or [], key=lambda item: str(item.get("date") or ""))
        dates = [str(scene.get("date") or "") for scene in scenes if scene.get("date")]
        reference = str(reference_date or "").strip()
        if reference not in dates and dates:
            reference = str((stack_manifest.get("stack") or {}).get("reference_date") or "").strip()
        if reference not in dates and dates:
            reference = dates[len(dates) // 2]

        gamma_root = run_dir / "work" / "gamma"
        common_dir = gamma_root / f"common_{reference}"
        slc_dir = gamma_root / "slc"
        mli_dir = gamma_root / "mli"
        rslc_dir = common_dir / "rslc"
        rmli_dir = common_dir / "rmli"

        per_date: list[dict[str, Any]] = []
        missing_dates: list[str] = []
        for date in dates:
            if date == reference:
                required = {
                    "slc": slc_dir / f"{date}.slc",
                    "slc_par": slc_dir / f"{date}.slc.par",
                    "mli": mli_dir / f"{date}.mli",
                    "mli_par": mli_dir / f"{date}.mli.par",
                }
                role = "reference"
            else:
                required = {
                    "rslc": rslc_dir / f"{date}.rslc",
                    "rslc_par": rslc_dir / f"{date}.rslc.par",
                    "rmli": rmli_dir / f"{date}.mli",
                    "rmli_par": rmli_dir / f"{date}.mli.par",
                    "offset": rslc_dir / f"{date}_to_{reference}.off",
                }
                role = "secondary"
            missing = [name for name, path in required.items() if not path.is_file() or path.stat().st_size <= 0]
            if missing:
                missing_dates.append(date)
            per_date.append(
                {
                    "date": date,
                    "role": role,
                    "ready": not missing,
                    "missing": missing,
                    "quality_file": str(rslc_dir / f"{date}.rslc.coreg_quality") if date != reference else None,
                }
            )

        expected_secondary_count = max(0, len(dates) - (1 if reference in dates else 0))
        ready_secondary_count = len(
            [
                item for item in per_date
                if item.get("role") == "secondary" and item.get("ready")
            ]
        )
        slc_tab = common_dir / "SLC_tab"
        rmli_tab = common_dir / "RMLI_tab"
        itab_approved = common_dir / "itab_approved"
        required_tabs = {
            "slc_tab": slc_tab,
            "rmli_tab": rmli_tab,
            "itab_approved": itab_approved,
        }
        missing_tabs = [
            name for name, path in required_tabs.items()
            if not path.is_file() or path.stat().st_size <= 0
        ]
        ready = not missing_dates and not missing_tabs and bool(dates)
        return {
            "schema": "insar.gamma-coregistration-summary/v1",
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "reference_date": reference,
            "scene_count": len(dates),
            "expected_secondary_count": expected_secondary_count,
            "ready_secondary_count": ready_secondary_count,
            "missing_dates": missing_dates,
            "missing_tabs": missing_tabs,
            "ready": ready,
            "per_date": per_date,
            "outputs": {
                "common_dir": str(common_dir),
                "rslc_dir": str(rslc_dir),
                "rmli_dir": str(rmli_dir),
                "slc_tab": str(slc_tab),
                "rmli_tab": str(rmli_tab),
                "itab_approved": str(itab_approved),
            },
        }

    @staticmethod
    def _tail_text(value: Any, length: int = 4000) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            text = value.decode("utf-8", errors="replace")
        else:
            text = str(value)
        return text[-length:]

    @staticmethod
    def _parse_bperp_table(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split()
            if len(parts) < 8:
                continue
            try:
                rows.append(
                    {
                        "pair_index": int(parts[0]),
                        "master_date": parts[1],
                        "slave_date": parts[2],
                        "bperp_m": float(parts[3]),
                        "delta_days": float(parts[4]),
                        "mjd1": float(parts[5]),
                        "mjd2": float(parts[6]),
                        "bperp1_m": float(parts[7]),
                        "bperp2_m": float(parts[8]) if len(parts) > 8 else None,
                    }
                )
            except ValueError:
                continue
        return rows

    @staticmethod
    def _parse_itab(path: Path) -> list[list[int]]:
        if not path.is_file():
            return []
        rows: list[list[int]] = []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                rows.append([int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])])
            except ValueError:
                continue
        return rows

    def _refresh_command_manifest_after_baseline(
        self,
        run_dir: Path,
        run_manifest: dict[str, Any],
        baseline_summary: dict[str, Any] | None,
    ) -> None:
        path = run_dir / "gamma_command_manifest.json"
        command_manifest = self._read_optional_json(path) or {}
        stage_plan = command_manifest.get("stage_plan") or [dict(item) for item in GAMMA_STAGE_PLAN]
        for stage in stage_plan:
            if stage.get("stage_id") == "prepare_slc":
                stage["status"] = "COMPLETED" if baseline_summary else "SCRIPT_READY"
            if stage.get("stage_id") == "baseline_audit":
                if run_manifest.get("status") == "BASELINE_AUDIT_READY":
                    stage["status"] = "COMPLETED_PENDING_ITAB_APPROVAL"
                elif run_manifest.get("status") == "BASELINE_AUDIT_FAILED":
                    stage["status"] = "FAILED"
                else:
                    stage["status"] = "SCRIPT_READY"
        command_manifest["execution_enabled"] = True
        command_manifest["reason_execution_disabled"] = None
        command_manifest["stage_plan"] = stage_plan
        command_manifest["baseline_audit"] = run_manifest.get("baseline_audit")
        self._write_json(path, command_manifest)

    def _refresh_command_manifest_after_itab_decision(
        self,
        run_dir: Path,
        run_manifest: dict[str, Any],
    ) -> None:
        path = run_dir / "gamma_command_manifest.json"
        command_manifest = self._read_optional_json(path) or {}
        stage_plan = command_manifest.get("stage_plan") or [dict(item) for item in GAMMA_STAGE_PLAN]
        status = run_manifest.get("status")
        for stage in stage_plan:
            if stage.get("stage_id") == "baseline_audit":
                if status == "ITAB_APPROVED":
                    stage["status"] = "COMPLETED_ITAB_APPROVED"
                elif status == "ITAB_REJECTED":
                    stage["status"] = "COMPLETED_ITAB_REJECTED"
            if stage.get("stage_id") == "coregistration":
                if status == "ITAB_APPROVED":
                    stage["status"] = "READY"
                elif status == "ITAB_REJECTED":
                    stage["status"] = "BLOCKED_PAIR_NETWORK_REJECTED"
        command_manifest["stage_plan"] = stage_plan
        command_manifest["baseline_audit"] = run_manifest.get("baseline_audit")
        command_manifest["next_stage"] = run_manifest.get("next_stage")
        self._write_json(path, command_manifest)

    def _refresh_command_manifest_after_coregistration(
        self,
        run_dir: Path,
        run_manifest: dict[str, Any],
    ) -> None:
        path = run_dir / "gamma_command_manifest.json"
        command_manifest = self._read_optional_json(path) or {}
        stage_plan = command_manifest.get("stage_plan") or [dict(item) for item in GAMMA_STAGE_PLAN]
        for stage in stage_plan:
            if stage.get("stage_id") == "coregistration":
                if run_manifest.get("status") == "COREGISTRATION_SCRIPT_READY":
                    stage["status"] = "SCRIPT_READY"
                elif run_manifest.get("status") == "COREGISTRATION_RUNNING":
                    stage["status"] = "RUNNING"
                elif run_manifest.get("status") == "COREGISTRATION_READY":
                    stage["status"] = "COMPLETED"
                elif run_manifest.get("status") == "COREGISTRATION_FAILED":
                    stage["status"] = "FAILED"
            if stage.get("stage_id") == "rdc_dem" and run_manifest.get("status") == "COREGISTRATION_READY":
                stage["status"] = "READY"
        command_manifest["stage_plan"] = stage_plan
        command_manifest["coregistration"] = run_manifest.get("coregistration")
        command_manifest["next_stage"] = run_manifest.get("next_stage")
        self._write_json(path, command_manifest)

    def _build_command_manifest(self, run_manifest: dict[str, Any], stack_manifest: dict[str, Any]) -> dict[str, Any]:
        scenes = stack_manifest.get("scenes") or []
        pair_network = stack_manifest.get("pair_network") or {}
        return {
            "schema": "insar.gamma-command-manifest/v1",
            "run_id": run_manifest["run_id"],
            "engine": "gamma",
            "processor_code": "gamma_ipta_sbas",
            "execution_enabled": False,
            "reason_execution_disabled": "The managed Gamma runner is intentionally not attached in this planning slice.",
            "stage_plan": [dict(item) for item in GAMMA_STAGE_PLAN],
            "inputs": {
                "scene_count": len(scenes),
                "scenes": [
                    {
                        "date": scene.get("date"),
                        "scene_name": scene.get("scene_name"),
                        "tiff_wsl": scene.get("tiff_wsl"),
                        "meta_wsl": scene.get("meta_wsl"),
                        "orbit_wsl": scene.get("orbit_wsl"),
                    }
                    for scene in scenes
                ],
                "pair_count": len(pair_network.get("pairs") or []),
                "pair_network_strategy": pair_network.get("strategy"),
            },
            "expected_outputs": [item["relative_path"] for item in PRODUCT_DEFINITIONS],
            "next_manual_review": "Run Gamma base_calc, inspect perpendicular/temporal baselines, then replace initial adjacent itab if needed.",
        }

    def _build_run_card(self, run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
        stack = manifest.get("stack") or {}
        return {
            "run_id": manifest.get("run_id") or run_dir.name,
            "run_label": manifest.get("run_label"),
            "status": manifest.get("status") or "UNKNOWN",
            "created_at": manifest.get("created_at"),
            "workflow_code": manifest.get("workflow_code"),
            "processor_code": manifest.get("processor_code"),
            "engine_code": manifest.get("engine_code"),
            "stack_id": manifest.get("stack_id"),
            "scene_count": manifest.get("scene_count"),
            "pair_count": manifest.get("pair_count"),
            "next_stage": manifest.get("next_stage"),
            "platform": stack.get("satellite"),
            "relative_orbit": stack.get("relative_orbit"),
            "direction": stack.get("orbit_direction"),
            "polarization": stack.get("polarization"),
            "center_bucket": stack.get("center_bucket"),
            "reference_date": stack.get("reference_date"),
            "run_dir": str(run_dir),
        }

    def _build_run_artifacts(self, run_dir: Path) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        for relative_path, label, role in [
            ("run_manifest.json", "SBAS run manifest", "run_manifest"),
            ("stack_manifest.json", "Stack manifest", "stack_manifest"),
            ("pair_network.json", "Initial pair network", "pair_network"),
            ("pair_network_baseline_audit.json", "Gamma baseline-audited pair network", "pair_network_baseline_audit"),
            ("baseline_audit_summary.json", "Gamma baseline audit summary", "baseline_audit_summary"),
            ("itab_decision.json", "Approved/rejected itab decision", "itab_decision"),
            ("coregistration_plan.json", "Coregistration stage plan", "coregistration_plan"),
            ("coregistration_summary.json", "Coregistration execution summary", "coregistration_summary"),
            ("gamma_command_manifest.json", "Gamma command manifest", "command_manifest"),
            ("monitor_points.json", "Monitoring-point configuration", "monitor_points"),
            ("scripts/01_baseline_audit.sh", "Gamma baseline audit script", "baseline_audit_script"),
            ("scripts/02_coreg_common_ref.sh", "Gamma common-reference coregistration script", "coregistration_script"),
        ]:
            path = run_dir / relative_path
            if path.is_file():
                artifacts.append(
                    {
                        "key": Path(relative_path).stem,
                        "label": label,
                        "role": role,
                        "relative_path": relative_path,
                        "size_bytes": path.stat().st_size,
                    }
                )
        return artifacts

    def _build_trial_card(self, trial_dir: Path, summary: dict[str, Any]) -> dict[str, Any]:
        stack = summary.get("stack") or {}
        quality = summary.get("quality_stats") or {}
        exports = summary.get("exports") or {}
        monitor_points = summary.get("monitor_points") or []
        primary_rate_stats = quality.get("los_rate_toward_mm_per_year_rdc") or {}
        sigma_stats = quality.get("los_sigma_mm_per_year_rdc") or {}
        return {
            "trial_id": summary.get("trial_id") or trial_dir.name,
            "status": "TRIAL_READY",
            "generated_at": summary.get("generated_at"),
            "engine": summary.get("engine") or {},
            "stack": stack,
            "dates": stack.get("dates") or [],
            "reference_date": stack.get("reference_date"),
            "scene_count": len(stack.get("dates") or []),
            "platform": stack.get("platform"),
            "direction": stack.get("direction"),
            "relative_orbit": stack.get("relative_orbit"),
            "polarization": stack.get("polarization"),
            "mode": stack.get("mode"),
            "default_los_product": "los_rate_toward_mm_per_year",
            "los_sign_convention": (summary.get("radar") or {}).get("los_sign_convention"),
            "primary_rate_median_mm_year": primary_rate_stats.get("median"),
            "primary_rate_p01_mm_year": primary_rate_stats.get("p01"),
            "primary_rate_p99_mm_year": primary_rate_stats.get("p99"),
            "sigma_median_mm_year": sigma_stats.get("median"),
            "monitor_point_count": len(monitor_points),
            "export_count": len(exports),
            "trial_dir": str(trial_dir),
        }

    def _build_artifacts(self, trial_dir: Path) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        for item in PRODUCT_DEFINITIONS:
            path = trial_dir / item["relative_path"]
            if path.is_file():
                artifacts.append(
                    {
                        **item,
                        "size_bytes": path.stat().st_size,
                    }
                )

        monitor_dir = trial_dir / "publish" / "monitor_points"
        if monitor_dir.is_dir():
            for path in sorted(monitor_dir.iterdir()):
                if not path.is_file():
                    continue
                for suffix_key, label, ext in MONITOR_ARTIFACT_SUFFIXES:
                    if path.name.endswith(ext):
                        artifacts.append(
                            {
                                "key": f"monitor_{path.stem}_{suffix_key}",
                                "label": label,
                                "role": "monitor_point",
                                "relative_path": str(path.relative_to(trial_dir)).replace("\\", "/"),
                                "size_bytes": path.stat().st_size,
                            }
                        )
                        break
        return artifacts


sbas_insar_production_service = SbasInsarProductionService()
