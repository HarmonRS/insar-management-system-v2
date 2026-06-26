"""
Cluster data-transport helpers for LandSAR distributed processing.

Used by _handle_landsar_cluster_item in job_handlers.py to pull input
data from the main server and push results back via HTTP.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import queue
import shutil
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Any, Iterable, Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.orm import DinsarProductionRunItemORM, DinsarProductionRunORM


def _read_cluster_env(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def _cluster_transfer_timeout() -> int:
    try:
        from ..config import read_int_env

        return read_int_env(
            "CLUSTER_TRANSFER_TIMEOUT_SECONDS",
            3600,
            minimum=60,
            maximum=86400,
        )
    except Exception:
        return 3600


def _cluster_request_headers() -> dict[str, str]:
    token = _read_cluster_env("CLUSTER_SHARED_TOKEN")
    if not token:
        raise RuntimeError("CLUSTER_SHARED_TOKEN is not configured.")
    return {"X-Cluster-Token": token}


def normalize_cluster_relative_path(relative_path: Any) -> str:
    text = str(relative_path or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or os.path.splitdrive(text)[0]:
        raise ValueError(f"Unsafe cluster relative path: {relative_path}")
    parts = []
    for part in text.split("/"):
        if not part or part == ".":
            continue
        if part == ".." or os.path.splitdrive(part)[0]:
            raise ValueError(f"Unsafe cluster relative path: {relative_path}")
        parts.append(part)
    if not parts:
        raise ValueError(f"Unsafe cluster relative path: {relative_path}")
    return "/".join(parts)


def safe_extract_zip(zf: zipfile.ZipFile, target_dir: str) -> None:
    """Extract a zip after verifying all members stay inside target_dir."""
    target_root = os.path.abspath(target_dir)
    for member in zf.infolist():
        member_name = member.filename.replace("\\", "/")
        if (
            not member_name
            or member_name.startswith("/")
            or os.path.splitdrive(member_name)[0]
            or member_name.startswith("../")
            or "/../" in f"/{member_name}/"
        ):
            raise ValueError(f"Unsafe zip member path: {member.filename}")
        destination = os.path.abspath(os.path.join(target_root, member_name))
        if destination != target_root and not destination.startswith(
            target_root + os.sep
        ):
            raise ValueError(f"Unsafe zip member path: {member.filename}")
    zf.extractall(target_root)


def _zip_directory_contents(source_dir: str, zip_path: str) -> None:
    source_root = os.path.abspath(source_dir)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for current, _, files in os.walk(source_root):
            for name in files:
                path = os.path.join(current, name)
                arcname = os.path.relpath(path, source_root)
                zf.write(path, arcname)


def _build_multipart_form_data(
    *,
    fields: dict[str, str],
    files: list[tuple[str, str, str, bytes]],
    boundary: str,
) -> bytes:
    body = io.BytesIO()

    def _write_field(
        name: str,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> None:
        body.write(f"--{boundary}\r\n".encode("utf-8"))
        if filename:
            body.write(
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\n'.encode("utf-8")
            )
            body.write(f"Content-Type: {content_type}\r\n".encode("utf-8"))
        else:
            body.write(
                f'Content-Disposition: form-data; name="{name}"\r\n'.encode(
                    "utf-8"
                )
            )
        body.write(b"\r\n")
        body.write(data)
        body.write(b"\r\n")

    for name, value in fields.items():
        _write_field(name, "", "text/plain", str(value or "").encode("utf-8"))
    for name, filename, content_type, data in files:
        _write_field(name, filename, content_type, data)
    body.write(f"--{boundary}--\r\n".encode("utf-8"))
    return body.getvalue()


def _is_lt1_source_file(name: str) -> bool:
    lower_name = str(name or "").lower()
    return lower_name.startswith("lt1") and lower_name.endswith(
        (".xml", ".tif", ".tiff", ".jpg", ".jpeg", ".rpc")
    )


def _extract_date_from_name(name: str) -> str:
    import re

    match = re.search(r"((?:19|20)\d{6})", str(name or ""))
    return match.group(1) if match else ""


def _has_landsar_input_pair(input_data_dir: str) -> bool:
    if not os.path.isdir(input_data_dir):
        return False

    by_date: dict[str, set[str]] = {}
    for entry in os.scandir(input_data_dir):
        if not entry.is_file():
            continue
        lower_name = entry.name.lower()
        if not lower_name.endswith((".xml", ".tif", ".tiff")):
            continue
        if lower_name.endswith(".meta.xml") or lower_name.endswith("_check.xml"):
            continue
        if lower_name.endswith(".xml") and "_slc" not in lower_name:
            continue
        date_text = _extract_date_from_name(entry.name)
        if not date_text:
            continue
        kinds = by_date.setdefault(date_text, set())
        if lower_name.endswith(".xml"):
            kinds.add("xml")
        elif lower_name.endswith((".tif", ".tiff")):
            kinds.add("tif")

    return sum(1 for kinds in by_date.values() if {"xml", "tif"} <= kinds) >= 2


def _has_direct_landsar_source_file(directory: str) -> bool:
    if not os.path.isdir(directory):
        return False
    try:
        for entry in os.scandir(directory):
            if entry.is_file() and _is_lt1_source_file(entry.name):
                return True
    except OSError:
        return False
    return False


def _iter_tree_files(root_dir: str, relative_root: str = "") -> Iterator[tuple[str, str]]:
    if not os.path.isdir(root_dir):
        return
    root_abs = os.path.abspath(root_dir)
    for current, _, files in os.walk(root_abs):
        for name in sorted(files):
            path = os.path.join(current, name)
            rel = os.path.relpath(path, root_abs)
            if relative_root:
                rel = os.path.join(relative_root, rel)
            yield path, rel


def _iter_direct_files(root_dir: str, relative_root: str = "") -> Iterator[tuple[str, str]]:
    if not os.path.isdir(root_dir):
        return
    for entry in sorted(os.scandir(root_dir), key=lambda item: item.name.lower()):
        if not entry.is_file():
            continue
        rel = os.path.join(relative_root, entry.name) if relative_root else entry.name
        yield entry.path, rel


def iter_cluster_input_package_files(source_task_dir: str) -> Iterator[tuple[str, str]]:
    """Yield the minimal LandSAR input package file set.

    A Task_Pool item can contain both raw LT-1 ``master/slave`` files and a
    derived ``Input_Data`` directory. Shipping both copies is wasteful for
    remote workers. Prefer a valid LandSAR ``Input_Data`` pair; otherwise ship
    only direct raw files under ``master`` and ``slave``. This keeps the
    extracted layout compatible with LandSAR validation while avoiding nested
    duplicate scene copies.
    """
    source_dir = os.path.abspath(source_task_dir)
    if not os.path.isdir(source_dir):
        return

    pair_meta = os.path.join(source_dir, ".dinsar_pair.json")
    if os.path.isfile(pair_meta):
        yield pair_meta, ".dinsar_pair.json"

    input_data_dir = os.path.join(source_dir, "Input_Data")
    if _has_landsar_input_pair(input_data_dir):
        yield from _iter_tree_files(input_data_dir, "Input_Data")
    else:
        master_dir = os.path.join(source_dir, "master")
        slave_dir = os.path.join(source_dir, "slave")
        if _has_direct_landsar_source_file(master_dir) and _has_direct_landsar_source_file(slave_dir):
            yield from _iter_direct_files(master_dir, "master")
            yield from _iter_direct_files(slave_dir, "slave")
        else:
            yield from _iter_tree_files(source_dir)
            return

    orbit_dir = os.path.join(source_dir, "orbit")
    if os.path.isdir(orbit_dir):
        yield from _iter_tree_files(orbit_dir, "orbit")


def build_cluster_input_manifest(source_task_dir: str) -> dict[str, Any]:
    files = []
    total_bytes = 0
    for abs_path, rel_path in iter_cluster_input_package_files(source_task_dir):
        if not os.path.isfile(abs_path):
            continue
        normalized_rel = normalize_cluster_relative_path(rel_path)
        stat = os.stat(abs_path)
        size = int(stat.st_size)
        total_bytes += size
        files.append(
            {
                "relative_path": normalized_rel,
                "size": size,
                "mtime": float(stat.st_mtime),
            }
        )
    return {
        "task_name": os.path.basename(os.path.normpath(source_task_dir)),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
    }


class _QueueZipWriter:
    def __init__(self, output_queue: "queue.Queue[object]") -> None:
        self._queue = output_queue
        self._closed = False

    def write(self, data: bytes) -> int:
        if self._closed:
            raise BrokenPipeError("zip stream is closed")
        chunk = bytes(data)
        if not chunk:
            return 0
        while not self._closed:
            try:
                self._queue.put(chunk, timeout=1)
                return len(chunk)
            except queue.Full:
                continue
        raise BrokenPipeError("zip stream is closed")

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self._closed = True


def stream_zip_files(
    files: Iterable[tuple[str, str]],
    *,
    top_level_dir: str,
) -> Iterator[bytes]:
    """Stream a zip archive for *files* without materializing it first."""
    output_queue: "queue.Queue[object]" = queue.Queue(maxsize=8)
    sentinel = object()
    writer = _QueueZipWriter(output_queue)
    safe_top = str(top_level_dir or "Task").strip().strip("/\\") or "Task"

    def _put_control(item: object) -> None:
        while True:
            try:
                output_queue.put(item, timeout=1)
                return
            except queue.Full:
                if writer._closed:
                    return

    def _producer() -> None:
        try:
            with zipfile.ZipFile(
                writer,
                mode="w",
                compression=zipfile.ZIP_STORED,
                allowZip64=True,
            ) as zf:
                for abs_path, rel_path in files:
                    if writer._closed:
                        raise BrokenPipeError("zip stream is closed")
                    if not os.path.isfile(abs_path):
                        continue
                    arcname = os.path.join(safe_top, rel_path).replace("\\", "/")
                    zf.write(abs_path, arcname)
        except Exception as exc:
            if not writer._closed:
                _put_control(exc)
        finally:
            _put_control(sentinel)

    producer = threading.Thread(
        target=_producer,
        name="cluster-input-zip-stream",
        daemon=True,
    )
    producer.start()

    try:
        while True:
            item = output_queue.get()
            if item is sentinel:
                break
            if isinstance(item, Exception):
                raise item
            yield item  # type: ignore[misc]
    finally:
        writer.close()


def _resolve_cluster_server_url() -> str:
    """Return the main-server HTTP base URL for cluster data transport.

    Prefers CLUSTER_MAIN_SERVER_URL; falls back to the DATABASE_URL host.
    Returns ``http://127.0.0.1`` when nothing is configured (main-server /
    local worker).
    """
    from ..config import settings

    explicit = _read_cluster_env("CLUSTER_MAIN_SERVER_URL") or str(
        getattr(settings, "CLUSTER_MAIN_SERVER_URL", "") or ""
    ).strip()
    if explicit:
        return explicit.rstrip("/")

    db_url = str(getattr(settings, "DATABASE_URL", "") or "")
    if "@" in db_url:
        host_part = db_url.split("@")[1].split("/")[0].split(":")[0]
        if host_part and host_part not in {"localhost", "127.0.0.1"}:
            return f"http://{host_part}"

    return "http://127.0.0.1"


def _is_remote_worker() -> bool:
    """True when this process is configured to talk to a remote main server."""
    url = _resolve_cluster_server_url()
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    return host not in {"", "127.0.0.1", "localhost"}


def resolve_cluster_local_task_dir(item: DinsarProductionRunItemORM) -> str:
    """Return the worker-local Task_* directory for a cluster item."""
    source_task_dir = os.path.normpath(str(item.source_task_dir or ""))
    worker_root = _read_cluster_env("CLUSTER_WORKER_TASK_ROOT")
    if not worker_root:
        return source_task_dir
    task_name = os.path.basename(source_task_dir) or f"Task_item_{item.id}"
    return os.path.normpath(
        os.path.join(worker_root, f"item_{item.id}", task_name)
    )


def resolve_cluster_local_run_dir(
    item: DinsarProductionRunItemORM,
    run_key: str,
) -> str:
    """Return the worker-local managed result run directory."""
    worker_root = _read_cluster_env("CLUSTER_WORKER_RESULT_ROOT")
    if not worker_root:
        return os.path.join(str(item.results_root_dir or ""), "runs", run_key)
    pair_fragment = str(item.pair_key or f"item_{item.id}").strip() or f"item_{item.id}"
    safe_pair = "".join(
        ch if ch.isalnum() or ch in "._-" else "_"
        for ch in pair_fragment
    ).strip("._") or f"item_{item.id}"
    return os.path.normpath(os.path.join(worker_root, safe_pair, "runs", run_key))


async def materialize_cluster_input(
    item: DinsarProductionRunItemORM,
    local_task_dir: str,
    task_id: str,
) -> None:
    """Download the input data for a cluster item file by file."""
    from .task_service import task_service

    server_url = _resolve_cluster_server_url()
    manifest_url = f"{server_url}/api/cluster/input-manifest/{item.id}"
    parent_dir = os.path.dirname(local_task_dir)
    task_name = os.path.basename(local_task_dir)
    staging_dir = os.path.join(parent_dir, f".{task_name}.download")

    await task_service.add_log(
        task_id,
        "INFO",
        f"[cluster] Fetching input manifest from {manifest_url} ...",
    )

    def _request_json(url: str) -> dict[str, Any]:
        req = urllib.request.Request(
            url,
            headers=_cluster_request_headers(),
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=_cluster_transfer_timeout()) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _download_file(relative_path: str, expected_size: int) -> None:
        normalized_rel = normalize_cluster_relative_path(relative_path)
        file_url = (
            f"{server_url}/api/cluster/input-file/{item.id}"
            f"?relative_path={urllib.parse.quote(normalized_rel, safe='')}"
        )
        target_path = os.path.join(staging_dir, *normalized_rel.split("/"))
        tmp_path = f"{target_path}.part"
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        req = urllib.request.Request(
            file_url,
            headers=_cluster_request_headers(),
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=_cluster_transfer_timeout()) as resp:
            with open(tmp_path, "wb") as fh:
                shutil.copyfileobj(resp, fh, 8 * 1024 * 1024)
        actual_size = os.path.getsize(tmp_path)
        if expected_size >= 0 and actual_size != expected_size:
            raise RuntimeError(
                f"Downloaded file size mismatch for {normalized_rel}: "
                f"expected={expected_size} actual={actual_size}"
            )
        os.replace(tmp_path, target_path)

    try:
        manifest = await asyncio.to_thread(_request_json, manifest_url)
        files = list(manifest.get("files") or [])
        if not files:
            raise RuntimeError(f"Input manifest contains no files: {manifest_url}")

        total_bytes = int(manifest.get("total_bytes") or 0)
        await task_service.add_log(
            task_id,
            "INFO",
            f"[cluster] Downloading input files: count={len(files)} bytes={total_bytes}",
        )

        if os.path.isdir(staging_dir):
            await asyncio.to_thread(shutil.rmtree, staging_dir)
        os.makedirs(staging_dir, exist_ok=True)

        for index, file_info in enumerate(files, start=1):
            rel_path = normalize_cluster_relative_path(file_info.get("relative_path"))
            expected_size = int(file_info.get("size") or -1)
            await task_service.add_log(
                task_id,
                "INFO",
                f"[cluster] Downloading input file {index}/{len(files)}: "
                f"{rel_path} ({expected_size} bytes)",
            )
            await asyncio.to_thread(_download_file, rel_path, expected_size)
            await task_service.add_log(
                task_id,
                "INFO",
                f"[cluster] Downloaded input file {index}/{len(files)}: {rel_path}",
            )

        if os.path.isdir(local_task_dir):
            await asyncio.to_thread(shutil.rmtree, local_task_dir)
        os.makedirs(parent_dir, exist_ok=True)
        os.replace(staging_dir, local_task_dir)

        await task_service.add_log(
            task_id,
            "INFO",
            f"[cluster] Input data ready: {local_task_dir}",
        )
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        await task_service.add_log(
            task_id,
            "WARNING",
            f"[cluster] Input download HTTP {exc.code}: {body_text[:300]}",
        )
        raise RuntimeError(
            f"Input download failed HTTP {exc.code}: {body_text[:200]}"
        ) from exc
    finally:
        try:
            if os.path.isdir(staging_dir):
                shutil.rmtree(staging_dir)
        except Exception:
            pass


async def upload_cluster_result(
    item: DinsarProductionRunItemORM,
    run: DinsarProductionRunORM,
    managed_run_dir: str,
    run_key: str,
    task_id: str,
) -> bool:
    """Package the managed run directory and upload it to the main server.

    Returns ``True`` when the main server accepted the upload and
    registered the result in the D-InSAR catalog.
    """
    from .task_service import task_service

    server_url = _resolve_cluster_server_url()

    await task_service.add_log(
        task_id,
        "INFO",
        "[cluster] Packaging results for upload ...",
    )

    tmp_root = tempfile.mkdtemp(prefix=f"cluster_result_{item.id}_")
    tmp_zip = os.path.join(tmp_root, "result.zip")

    def _package_and_upload() -> dict:
        _zip_directory_contents(managed_run_dir, tmp_zip)

        boundary = "----ClusterUploadBoundary"
        with open(tmp_zip, "rb") as fh:
            body = _build_multipart_form_data(
                fields={
                    "run_id": str(run.run_id or ""),
                    "run_key": str(run_key or ""),
                },
                files=[
                    (
                        "result_zip",
                        os.path.basename(tmp_zip),
                        "application/zip",
                        fh.read(),
                    ),
                ],
                boundary=boundary,
            )

        upload_url = f"{server_url}/api/cluster/upload-result/{item.id}"
        req = urllib.request.Request(
            upload_url,
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                **_cluster_request_headers(),
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=_cluster_transfer_timeout()) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        await task_service.add_log(
            task_id,
            "INFO",
            "[cluster] Uploading results to main server ...",
        )

        result = await asyncio.to_thread(_package_and_upload)
        await task_service.add_log(
            task_id,
            "INFO",
            f"[cluster] Results uploaded: "
            f"registered={result.get('registered', False)} "
            f"processed={result.get('processed', 0)}",
        )
        return bool(result.get("registered", False))

    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        await task_service.add_log(
            task_id,
            "WARNING",
            f"[cluster] Upload HTTP {exc.code}: {body_text[:300]}",
        )
        raise RuntimeError(
            f"Upload failed HTTP {exc.code}: {body_text[:200]}"
        ) from exc
    finally:
        try:
            if os.path.isdir(tmp_root):
                shutil.rmtree(tmp_root)
        except Exception:
            pass
