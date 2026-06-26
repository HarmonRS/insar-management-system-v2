import json
import os
import tempfile
import unittest
import zipfile
from io import BytesIO
from types import SimpleNamespace
from unittest import mock

from multipart import parse_form

from backend.app.services.cluster_transport import (
    _build_multipart_form_data,
    _zip_directory_contents,
    build_cluster_input_manifest,
    iter_cluster_input_package_files,
    normalize_cluster_relative_path,
    resolve_cluster_local_run_dir,
    resolve_cluster_local_task_dir,
    safe_extract_zip,
    stream_zip_files,
)
from backend.app.services.dinsar_completion_files import repair_managed_completion_files
from backend.app.services.dinsar_naming import RUN_META_FILENAME


class ClusterTransportTests(unittest.TestCase):
    def test_build_multipart_form_data_is_parseable(self):
        boundary = "----ClusterUploadBoundary"
        body = _build_multipart_form_data(
            fields={"run_id": "run-1", "run_key": "key-1"},
            files=[("result_zip", "result.zip", "application/zip", b"zip-bytes")],
            boundary=boundary,
        )
        fields = {}
        files = {}

        def on_field(field):
            fields[field.field_name.decode("utf-8")] = field.value.decode("utf-8")

        def on_file(file):
            files[file.field_name.decode("utf-8")] = {
                "file_name": file.file_name.decode("utf-8"),
                "size": file.size,
                "content": file.file_object.getvalue(),
            }

        parse_form(
            {
                "Content-Type": f"multipart/form-data; boundary={boundary}".encode("utf-8"),
                "Content-Length": str(len(body)).encode("utf-8"),
            },
            BytesIO(body),
            on_field,
            on_file,
        )

        self.assertEqual(fields, {"run_id": "run-1", "run_key": "key-1"})
        self.assertEqual(files["result_zip"]["file_name"], "result.zip")
        self.assertEqual(files["result_zip"]["size"], len(b"zip-bytes"))
        self.assertEqual(files["result_zip"]["content"], b"zip-bytes")

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

    def test_cluster_input_package_prefers_input_data(self):
        with tempfile.TemporaryDirectory() as root:
            task_dir = os.path.join(root, "Task_20260101_20260113")
            input_dir = os.path.join(task_dir, "Input_Data")
            master_dir = os.path.join(task_dir, "master")
            slave_dir = os.path.join(task_dir, "slave")
            orbit_dir = os.path.join(task_dir, "orbit")
            os.makedirs(input_dir)
            os.makedirs(master_dir)
            os.makedirs(slave_dir)
            os.makedirs(orbit_dir)
            with open(os.path.join(task_dir, ".dinsar_pair.json"), "w", encoding="utf-8") as fp:
                json.dump({"pair_key": "pair"}, fp)
            for date_text in ("20260101", "20260113"):
                base = f"LT1A_MONO_TEST_{date_text}_SLC"
                with open(os.path.join(input_dir, f"{base}.xml"), "wb") as fp:
                    fp.write(b"xml")
                with open(os.path.join(input_dir, f"{base}.tiff"), "wb") as fp:
                    fp.write(b"tif")
            with open(os.path.join(master_dir, "LT1A_raw_20260101.tiff"), "wb") as fp:
                fp.write(b"raw-master")
            with open(os.path.join(slave_dir, "LT1A_raw_20260113.tiff"), "wb") as fp:
                fp.write(b"raw-slave")
            with open(os.path.join(orbit_dir, "orbit.txt"), "wb") as fp:
                fp.write(b"orbit")

            names = {rel.replace("\\", "/") for _, rel in iter_cluster_input_package_files(task_dir)}

            self.assertIn(".dinsar_pair.json", names)
            self.assertIn("Input_Data/LT1A_MONO_TEST_20260101_SLC.xml", names)
            self.assertIn("Input_Data/LT1A_MONO_TEST_20260113_SLC.tiff", names)
            self.assertIn("orbit/orbit.txt", names)
            self.assertFalse(any(name.startswith("master/") for name in names))
            self.assertFalse(any(name.startswith("slave/") for name in names))

    def test_cluster_input_manifest_reports_files_and_size(self):
        with tempfile.TemporaryDirectory() as root:
            task_dir = os.path.join(root, "Task_20260101_20260113")
            input_dir = os.path.join(task_dir, "Input_Data")
            os.makedirs(input_dir)
            total = 0
            for date_text, payload in (("20260101", b"xml1"), ("20260113", b"xml2")):
                base = f"LT1A_MONO_TEST_{date_text}_SLC"
                for suffix, content in ((".xml", payload), (".tiff", payload * 2)):
                    path = os.path.join(input_dir, f"{base}{suffix}")
                    with open(path, "wb") as fp:
                        fp.write(content)
                    total += len(content)

            manifest = build_cluster_input_manifest(task_dir)

            self.assertEqual(manifest["task_name"], "Task_20260101_20260113")
            self.assertEqual(manifest["file_count"], 4)
            self.assertEqual(manifest["total_bytes"], total)
            self.assertTrue(
                all("\\" not in item["relative_path"] for item in manifest["files"])
            )

    def test_normalize_cluster_relative_path_rejects_escape(self):
        for value in ("../a.txt", "a/../../b.txt", "/abs.txt", r"C:\abs.txt"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_cluster_relative_path(value)

        self.assertEqual(
            normalize_cluster_relative_path(r"Input_Data\scene.tif"),
            "Input_Data/scene.tif",
        )

    def test_stream_zip_files_preserves_task_top_level(self):
        with tempfile.TemporaryDirectory() as root:
            payload_path = os.path.join(root, "payload.txt")
            with open(payload_path, "wb") as fp:
                fp.write(b"payload")

            zip_bytes = b"".join(
                stream_zip_files([(payload_path, "Input_Data/payload.txt")], top_level_dir="Task_A")
            )
            zip_path = os.path.join(root, "streamed.zip")
            with open(zip_path, "wb") as fp:
                fp.write(zip_bytes)

            with zipfile.ZipFile(zip_path, "r") as zf:
                self.assertEqual(zf.read("Task_A/Input_Data/payload.txt"), b"payload")

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
