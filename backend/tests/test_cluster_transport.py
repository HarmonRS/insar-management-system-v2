import json
import os
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest import mock

from backend.app.services.cluster_transport import (
    _zip_directory_contents,
    resolve_cluster_local_run_dir,
    resolve_cluster_local_task_dir,
    safe_extract_zip,
)
from backend.app.services.dinsar_completion_files import repair_managed_completion_files
from backend.app.services.dinsar_naming import RUN_META_FILENAME


class ClusterTransportTests(unittest.TestCase):
    def test_zip_directory_contents_excludes_top_level_run_dir(self):
        with tempfile.TemporaryDirectory() as root:
            run_dir = os.path.join(root, "run_abc")
            os.makedirs(os.path.join(run_dir, "assets", "disp"))
            os.makedirs(os.path.join(run_dir, "native"))
            with open(os.path.join(run_dir, RUN_META_FILENAME), "w", encoding="utf-8") as fp:
                json.dump({"run_key": "run_abc"}, fp)
            with open(os.path.join(run_dir, "assets", "disp", "disp.tif"), "wb") as fp:
                fp.write(b"disp")
            with open(os.path.join(run_dir, "native", "raw.txt"), "w", encoding="utf-8") as fp:
                fp.write("raw")

            zip_path = os.path.join(root, "result.zip")
            _zip_directory_contents(run_dir, zip_path)

            with zipfile.ZipFile(zip_path, "r") as zf:
                names = set(zf.namelist())

            self.assertIn(RUN_META_FILENAME, names)
            self.assertIn("assets/disp/disp.tif", names)
            self.assertIn("native/raw.txt", names)
            self.assertFalse(any(name.startswith("run_abc/") for name in names))

    def test_safe_extract_zip_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as root:
            zip_path = os.path.join(root, "unsafe.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("../escape.txt", "bad")

            with zipfile.ZipFile(zip_path, "r") as zf:
                with self.assertRaises(ValueError):
                    safe_extract_zip(zf, os.path.join(root, "extract"))

    def test_worker_local_paths_are_optional_overrides(self):
        item = SimpleNamespace(
            id=7,
            source_task_dir=r"D:\Task_Pool\DInSAR\Task_20260101_20260113",
            results_root_dir=r"D:\production_results\dinsar\pair_a",
            pair_key="lt1/pair:a",
        )

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                os.path.normpath(resolve_cluster_local_task_dir(item)),
                os.path.normpath(item.source_task_dir),
            )
            self.assertEqual(
                os.path.normpath(resolve_cluster_local_run_dir(item, "run_1")),
                os.path.normpath(r"D:\production_results\dinsar\pair_a\runs\run_1"),
            )

        with mock.patch.dict(
            os.environ,
            {
                "CLUSTER_WORKER_TASK_ROOT": r"E:\cluster_tasks",
                "CLUSTER_WORKER_RESULT_ROOT": r"E:\cluster_results",
            },
            clear=True,
        ):
            self.assertEqual(
                os.path.normpath(resolve_cluster_local_task_dir(item)),
                os.path.normpath(r"E:\cluster_tasks\item_7\Task_20260101_20260113"),
            )
            self.assertEqual(
                os.path.normpath(resolve_cluster_local_run_dir(item, "run_1")),
                os.path.normpath(r"E:\cluster_results\lt1_pair_a\runs\run_1"),
            )

    def test_repair_completion_files_reanchors_uploaded_landsar_run(self):
        with tempfile.TemporaryDirectory() as root:
            run_dir = os.path.join(root, "pair_a", "runs", "run_abc")
            disp_dir = os.path.join(run_dir, "assets", "disp")
            native_dir = os.path.join(run_dir, "native")
            os.makedirs(disp_dir)
            os.makedirs(native_dir)
            primary_file = os.path.join(disp_dir, "disp.tif")
            with open(primary_file, "wb") as fp:
                fp.write(b"disp")
            with open(os.path.join(run_dir, RUN_META_FILENAME), "w", encoding="utf-8") as fp:
                json.dump(
                    {
                        "run_key": "run_abc",
                        "pair_key": "pair_a",
                        "engine_code": "landsar",
                        "profile_code": "lt1_dinsar",
                        "task_name": "Task_20260101_20260113",
                        "task_alias": "Task_20260101_20260113",
                        "output_dir": r"E:\worker_results\pair_a\runs\run_abc",
                        "native_output_dir": r"E:\worker_results\pair_a\runs\run_abc\native",
                        "primary_file": primary_file,
                        "source_files": [primary_file],
                    },
                    fp,
                )

            result = repair_managed_completion_files(
                run_dir,
                primary_file=primary_file,
                source_files=[primary_file],
            )

            self.assertTrue(os.path.isfile(result["execution_manifest_path"]))
            self.assertTrue(os.path.isfile(result["current_pointer_path"]))
            with open(result["execution_manifest_path"], "r", encoding="utf-8") as fp:
                manifest = json.load(fp)
            self.assertEqual(os.path.normpath(manifest["output_dir"]), os.path.normpath(run_dir))
            self.assertEqual(os.path.normpath(manifest["native_output_dir"]), os.path.normpath(native_dir))


if __name__ == "__main__":
    unittest.main()
