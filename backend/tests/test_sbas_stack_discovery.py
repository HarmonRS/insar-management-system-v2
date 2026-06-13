from backend.app.services.sbas_insar_production_service import SbasInsarProductionService


def _scene(date, lon, lat, name=None, has_orbit=True):
    width = 1.0
    height = 1.0
    return {
        "scene_name": name or f"LT1B_MONO_SYC_STRIP1_000000_E{lon:.1f}_N{lat:.1f}_{date}_SLC_HH",
        "date": date,
        "satellite": "LT1B",
        "satellite_mode": "MONO",
        "receiving_station": "SYC",
        "relative_orbit": "114",
        "orbit_direction": "DESCENDING",
        "imaging_mode": "STRIP1",
        "polarization": "HH",
        "center_lon": lon,
        "center_lat": lat,
        "center_bucket": f"E{lon:.1f}_N{lat:.1f}",
        "has_orbit": has_orbit,
        "bbox": {
            "min_lon": lon - width / 2,
            "min_lat": lat - height / 2,
            "max_lon": lon + width / 2,
            "max_lat": lat + height / 2,
        },
    }


def test_common_overlap_subgroups_preserve_viable_date_keyed_stack():
    service = SbasInsarProductionService()
    viable = [
        _scene("20240101", 129.20, 44.10),
        _scene("20240201", 129.22, 44.08),
        _scene("20240301", 129.18, 44.11),
        _scene("20240401", 129.21, 44.09),
    ]
    distractors = [
        _scene("20240501", 129.80, 44.80),
        _scene("20240601", 129.85, 44.78),
        _scene("20240701", 129.82, 44.82),
    ]

    groups = service._build_discovery_scene_groups(
        observation_key="LT1B|MONO|SYC|114|DESCENDING|STRIP1|HH|E129.2_N44.1",
        group_scenes=viable + distractors,
        discovery_mode="strict",
        require_orbits=True,
        min_scenes=3,
        min_common_overlap_ratio=0.30,
        cluster_source="footprint_common_overlap",
    )
    candidates = [
        service._build_stack_candidate(
            group["scenes"],
            min_scenes=3,
            require_orbits=True,
            discovery_mode="strict",
            min_common_overlap_ratio=0.30,
        )
        for group in groups
    ]
    ready = [candidate for candidate in candidates if candidate["status"] == "READY"]

    assert ready
    assert any(
        set(candidate["dates"]) == {scene["date"] for scene in viable}
        and candidate["common_overlap_ratio"] >= 0.30
        for candidate in ready
    )


def test_common_overlap_subgroups_do_not_drop_same_date_viable_branch():
    service = SbasInsarProductionService()
    viable = [
        _scene("20240101", 129.20, 44.10, name="viable_a"),
        _scene("20240201", 129.22, 44.08, name="viable_b"),
        _scene("20240301", 129.18, 44.11, name="viable_c"),
    ]
    same_date_distractors = [
        _scene("20240101", 130.00, 44.90, name="distractor_a"),
        _scene("20240201", 130.02, 44.88, name="distractor_b"),
        _scene("20240301", 129.98, 44.91, name="distractor_c"),
    ]

    groups = service._build_discovery_scene_groups(
        observation_key="LT1B|MONO|SYC|114|DESCENDING|STRIP1|HH|E129.2_N44.1",
        group_scenes=viable + same_date_distractors,
        discovery_mode="strict",
        require_orbits=True,
        min_scenes=3,
        min_common_overlap_ratio=0.30,
        cluster_source="footprint_common_overlap",
    )

    assert any(
        {scene["scene_name"] for scene in group["scenes"]} == {"viable_a", "viable_b", "viable_c"}
        for group in groups
    )


def test_candidate_identity_marks_same_dates_with_different_scene_names():
    service = SbasInsarProductionService()
    first = [
        _scene("20240101", 129.20, 44.10, name="frame_a_20240101"),
        _scene("20240201", 129.22, 44.08, name="frame_a_20240201"),
        _scene("20240301", 129.18, 44.11, name="frame_a_20240301"),
    ]
    second = [
        _scene("20240101", 129.70, 44.60, name="frame_b_20240101"),
        _scene("20240201", 129.72, 44.58, name="frame_b_20240201"),
        _scene("20240301", 129.68, 44.61, name="frame_b_20240301"),
    ]
    candidates = [
        service._build_stack_candidate(
            scenes,
            min_scenes=3,
            require_orbits=True,
            discovery_mode="strict",
            min_common_overlap_ratio=0.30,
        )
        for scenes in (first, second)
    ]

    service._annotate_stack_candidate_identity(candidates, existing_run_index={})

    assert candidates[0]["date_sequence_hash"] == candidates[1]["date_sequence_hash"]
    assert candidates[0]["scene_identity_hash"] != candidates[1]["scene_identity_hash"]
    assert all(candidate["same_date_sequence_candidate_count"] == 2 for candidate in candidates)
    assert all(candidate["same_date_sequence_distinct_scene_group_count"] == 2 for candidate in candidates)
    assert all(candidate["same_date_sequence_has_different_scene_groups"] is True for candidate in candidates)


def test_candidate_identity_matches_existing_same_scene_run():
    service = SbasInsarProductionService()
    scenes = [
        _scene("20240101", 129.20, 44.10, name="same_a"),
        _scene("20240201", 129.22, 44.08, name="same_b"),
        _scene("20240301", 129.18, 44.11, name="same_c"),
    ]
    candidate = service._build_stack_candidate(
        scenes,
        min_scenes=3,
        require_orbits=True,
        discovery_mode="strict",
        min_common_overlap_ratio=0.30,
    )
    scene_hash = service._scene_identity_summary(scenes)["scene_identity_hash"]

    service._annotate_stack_candidate_identity(
        [candidate],
        existing_run_index={
            scene_hash: [
                {
                    "run_id": "sbas_existing",
                    "status": "WORKFLOW_COMPLETED",
                    "stack_id": candidate["stack_id"],
                }
            ]
        },
    )

    assert candidate["scene_identity_hash"] == scene_hash
    assert candidate["existing_same_scene_runs"][0]["run_id"] == "sbas_existing"
