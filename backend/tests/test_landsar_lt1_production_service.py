from pathlib import Path
import json
import re
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace

import backend.app.services.landsar_lt1_production_service as landsar_lt1_module
from backend.app.services.landsar_lt1_production_service import (
    IMPORT_PROID,
    ORBIT_PROID,
    generate_lt1_import_param_file,
    generate_lt1_orbit_param_file,
    landsar_lt1_production_service,
)


CN_PROCESS_ID = "\u5904\u7406\u7f16\u53f7"
CN_FOLDER_COUNT = "\u6587\u4ef6\u5939\u5bfc\u5165\u4e2a\u6570"
CN_FOLDER_PATH = "\u6587\u4ef6\u5939{index}\u8def\u5f84"
CN_OUTPUT_DIR = "\u8bbe\u7f6e\u8f93\u51fa\u6587\u4ef6\u76ee\u5f55"
CN_SAT_MODE = "\u8f93\u5165\u536b\u661f\u6570\u636e\u683c\u5f0f"
CN_DATA_COUNT = "\u8f93\u5165\u6570\u636e\u4e2a\u6570"
CN_DATA_XML = "\u8f93\u5165\u6570\u636e{index}\u7684xml"
CN_ORBIT_DIR = "\u8f93\u5165\u7cbe\u5bc6\u8f68\u9053\u6570\u636e\u6587\u4ef6\u5939"


def test_generate_lt1_import_param_file_single_scene(tmp_path: Path) -> None:
    scene_dir = tmp_path / "LT1A_SCENE"
    scene_dir.mkdir()
    export_dir = tmp_path / "Input_Data"
    param_file = tmp_path / "params" / "100016.txt"

    generate_lt1_import_param_file(
        str(param_file),
        [str(scene_dir)],
        str(export_dir),
        sat_mode="MONO",
    )

    content = param_file.read_text(encoding="utf-8")
    assert f"{CN_PROCESS_ID}       {IMPORT_PROID}" in content
    assert f"{CN_FOLDER_COUNT}  1" in content
    assert f"{CN_FOLDER_PATH.format(index=1)}  <{scene_dir}>" in content
    assert f"{CN_OUTPUT_DIR}  <{export_dir}>" in content
    assert f"{CN_SAT_MODE}  MONO" in content


def test_generate_lt1_import_param_file_stack(tmp_path: Path) -> None:
    scene_dirs = []
    for index in range(3):
        scene_dir = tmp_path / f"LT1A_SCENE_{index}"
        scene_dir.mkdir()
        scene_dirs.append(str(scene_dir))
    param_file = tmp_path / "params" / "100016_stack.txt"

    generate_lt1_import_param_file(
        str(param_file),
        scene_dirs,
        str(tmp_path / "Input_Data"),
        sat_mode="BIST",
    )

    content = param_file.read_text(encoding="utf-8")
    assert f"{CN_FOLDER_COUNT}  3" in content
    assert f"{CN_SAT_MODE}  BIST" in content
    for index, scene_dir in enumerate(scene_dirs, 1):
        assert f"{CN_FOLDER_PATH.format(index=index)}  <{scene_dir}>" in content


def test_generate_lt1_orbit_param_file(tmp_path: Path) -> None:
    xml_paths = []
    for index in range(2):
        xml_path = tmp_path / f"LT1A_{index}_SLC.xml"
        xml_path.write_text("<root />", encoding="utf-8")
        xml_paths.append(str(xml_path))
    orbit_dir = tmp_path / "orbit"
    orbit_dir.mkdir()
    param_file = tmp_path / "params" / "100206.txt"

    generate_lt1_orbit_param_file(
        str(param_file),
        xml_paths,
        str(orbit_dir),
        str(tmp_path / "Input_Data"),
    )

    content = param_file.read_text(encoding="utf-8")
    assert f"{CN_PROCESS_ID}       {ORBIT_PROID}" in content
    assert f"{CN_DATA_COUNT}  2" in content
    assert re.search(rf"{CN_ORBIT_DIR}\s+<{re.escape(str(orbit_dir))}>", content)
    for index, xml_path in enumerate(xml_paths, 1):
        assert f"{CN_DATA_XML.format(index=index)}  <{xml_path}>" in content


def test_preview_blocks_scene_without_lt1_files(tmp_path: Path) -> None:
    scene_dir = tmp_path / "EMPTY_SCENE"
    scene_dir.mkdir()

    preview = landsar_lt1_production_service.preview_import(
        {
            "scene_dirs": [str(scene_dir)],
            "mode": "scene",
            "sat_mode": "MONO",
            "import_orbit": False,
        }
    )

    assert preview["allow_submit"] is False
    assert any("Missing LT-1 XML" in item for item in preview["blockers"])
    assert any("Missing LT-1 SLC TIFF" in item for item in preview["blockers"])


def test_preview_accepts_source_asset_only_scene() -> None:
    preview = landsar_lt1_production_service.preview_import(
        {
            "source_asset_ids": [101],
            "mode": "scene",
            "sat_mode": "MONO",
            "import_orbit": False,
        }
    )

    assert preview["allow_submit"] is True
    assert preview["scene_count"] == 1
    assert preview["directory_scene_count"] == 0
    assert preview["source_asset_count"] == 1


def test_stack_preview_counts_source_assets() -> None:
    preview = landsar_lt1_production_service.preview_import(
        {
            "source_asset_ids": [101, 102],
            "mode": "stack",
            "sat_mode": "MONO",
            "import_orbit": False,
        }
    )

    assert preview["allow_submit"] is True
    assert preview["scene_count"] == 2
    assert not preview["warnings"]


def test_scene_mode_rejects_multiple_directories(tmp_path: Path) -> None:
    left = tmp_path / "LEFT"
    right = tmp_path / "RIGHT"
    left.mkdir()
    right.mkdir()

    try:
        landsar_lt1_production_service.preview_import(
            {
                "scene_dirs": [str(left), str(right)],
                "mode": "scene",
            }
        )
    except ValueError as exc:
        assert "exactly one" in str(exc)
    else:
        raise AssertionError("scene mode accepted multiple directories")


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _ExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _ScalarRows(self._rows)

    def all(self):
        return self._rows


class _FakeAsyncDb:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _stmt):
        return _ExecuteResult(self.rows)


def test_find_produced_source_asset_map_matches_summary_source_ids() -> None:
    scene = SimpleNamespace(
        id=7,
        radar_data_id=77,
        analysis_engine="lt_gamma",
        analysis_profile="lt1_gamma_geocoded_mli",
        analysis_tif_path="D:/ready/analysis_ready.tif",
        analysis_dir="D:/ready",
        analysis_preview_path="D:/ready/preview.png",
        updated_at=datetime(2026, 6, 27, 1, 2, 3),
    )

    async def _run():
        return await landsar_lt1_production_service.find_produced_source_asset_map(
            _FakeAsyncDb([(12, scene)]),
            [12, 13],
        )

    import asyncio

    produced = asyncio.run(_run())
    assert 12 in produced
    assert produced[12]["product_id"] == "sar_scene_geo:7"
    assert produced[12]["analysis_tif_path"] == "D:/ready/analysis_ready.tif"
    assert 13 not in produced


def test_run_import_materialized_asset_does_not_double_count_scene(tmp_path: Path) -> None:
    scene_dir = tmp_path / "LT1A_SCENE"
    scene_dir.mkdir()

    service = landsar_lt1_production_service
    original_ensure = service._ensure_runtime_ready
    original_console = service._console_path
    original_home = service._landsar_home

    original_publish_root = landsar_lt1_module.settings.RESULT_PUBLISH_ROOT
    original_run_console = landsar_lt1_module._run_console
    original_find_imported_xmls = service._find_imported_xmls

    def fake_run_console(_console_path, param_file, log_path, *, cwd, timeout_seconds):
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("console success\n", encoding="utf-8")
        return {
            "command": [_console_path, param_file],
            "returncode": 0,
            "started_at": "2026-06-27T00:00:00Z",
            "finished_at": "2026-06-27T00:00:01Z",
            "log_path": log_path,
            "stdout_tail": "console success",
        }

    try:
        landsar_lt1_module.settings.RESULT_PUBLISH_ROOT = str(tmp_path / "publish")
        service._ensure_runtime_ready = lambda: None
        service._console_path = lambda: "LandSARConsole.exe"
        service._landsar_home = lambda: str(tmp_path)
        service._find_imported_xmls = lambda input_data_dir: [str(Path(input_data_dir) / "LT1A_SCENE_SLC.xml")]
        landsar_lt1_module._run_console = fake_run_console

        result = service.run_import(
            {
                "source_asset_ids": [101],
                "__prepared_scene_dirs": [str(scene_dir)],
                "__materialized": [{"source_asset_id": 101, "scene_dir": str(scene_dir)}],
                "__materialize_task_root": str(tmp_path / "tasks"),
                "mode": "scene",
                "sat_mode": "MONO",
                "import_orbit": False,
            }
        )

        manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
        assert manifest["summary"]["scene_count"] == 1
        assert manifest["summary"]["source_asset_ids"] == [101]
        assert manifest["source"]["scene_dirs"] == [str(scene_dir)]
    finally:
        landsar_lt1_module.settings.RESULT_PUBLISH_ROOT = original_publish_root
        service._ensure_runtime_ready = original_ensure
        service._console_path = original_console
        service._landsar_home = original_home
        service._find_imported_xmls = original_find_imported_xmls
        landsar_lt1_module._run_console = original_run_console


class LandsarLt1ProductionServiceTests(unittest.TestCase):
    def _with_tmp_path(self, fn) -> None:
        with tempfile.TemporaryDirectory() as root:
            fn(Path(root))

    def test_generate_lt1_import_param_file_single_scene_unittest(self) -> None:
        self._with_tmp_path(test_generate_lt1_import_param_file_single_scene)

    def test_generate_lt1_import_param_file_stack_unittest(self) -> None:
        self._with_tmp_path(test_generate_lt1_import_param_file_stack)

    def test_generate_lt1_orbit_param_file_unittest(self) -> None:
        self._with_tmp_path(test_generate_lt1_orbit_param_file)

    def test_preview_blocks_scene_without_lt1_files_unittest(self) -> None:
        self._with_tmp_path(test_preview_blocks_scene_without_lt1_files)

    def test_preview_accepts_source_asset_only_scene_unittest(self) -> None:
        test_preview_accepts_source_asset_only_scene()

    def test_stack_preview_counts_source_assets_unittest(self) -> None:
        test_stack_preview_counts_source_assets()

    def test_scene_mode_rejects_multiple_directories_unittest(self) -> None:
        self._with_tmp_path(test_scene_mode_rejects_multiple_directories)

    def test_find_produced_source_asset_map_matches_summary_source_ids_unittest(self) -> None:
        test_find_produced_source_asset_map_matches_summary_source_ids()

    def test_run_import_materialized_asset_does_not_double_count_scene_unittest(self) -> None:
        self._with_tmp_path(test_run_import_materialized_asset_does_not_double_count_scene)
