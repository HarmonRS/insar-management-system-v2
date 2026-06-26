"""
Cluster data-transport helpers for LandSAR distributed processing.

Used by _handle_landsar_cluster_item in job_handlers.py to pull input
data from the main server and push results back via HTTP.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import TYPE_CHECKING

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


async def materialize_cluster_input(
    item: DinsarProductionRunItemORM,
    source_task_dir: str,
    task_id: str,
) -> None:
    """Download and extract the input data for a cluster item.

    Calls ``GET /api/cluster/input-package/{item_id}`` on the main
    server, retrieves a zip containing the Task_Pool directory tree,
    and extracts it so that *source_task_dir* exists locally.
    """
    from .task_service import task_service

    server_url = _resolve_cluster_server_url()
    download_url = f"{server_url}/api/cluster/input-package/{item.id}"
    parent_dir = os.path.dirname(source_task_dir)
    task_name = os.path.basename(source_task_dir)

    await task_service.add_log(
        task_id,
        "INFO",
        f"[cluster] Downloading input data from {download_url} ...",
    )

    tmp_zip = os.path.join(
        tempfile.gettempdir(),
        f"cluster_input_{item.id}_{task_name}.zip",
    )
    try:
        req = urllib.request.Request(
            download_url,
            headers=_cluster_request_headers(),
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=_cluster_transfer_timeout()) as resp:
            with open(tmp_zip, "wb") as fh:
                shutil.copyfileobj(resp, fh, 8 * 1024 * 1024)

        os.makedirs(parent_dir, exist_ok=True)
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            safe_extract_zip(zf, parent_dir)

        if not os.path.isdir(source_task_dir):
            raise RuntimeError(
                f"Extraction did not create expected directory: {source_task_dir}"
            )

        await task_service.add_log(
            task_id,
            "INFO",
            f"[cluster] Input data ready: {source_task_dir}",
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
            os.unlink(tmp_zip)
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

    run_dir_name = os.path.basename(os.path.normpath(managed_run_dir))
    tmp_zip = os.path.join(
        tempfile.gettempdir(),
        f"cluster_result_{item.id}.zip",
    )
    try:
        parent = os.path.dirname(managed_run_dir)
        shutil.make_archive(
            tmp_zip.replace(".zip", ""),
            "zip",
            root_dir=parent,
            base_dir=run_dir_name,
        )

        await task_service.add_log(
            task_id,
            "INFO",
            "[cluster] Uploading results to main server ...",
        )

        boundary = "----ClusterUploadBoundary"
        body = io.BytesIO()

        def _write_field(name, filename, content_type, data):
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

        _write_field("run_id", "", "text/plain", (run.run_id or "").encode("utf-8"))
        _write_field("run_key", "", "text/plain", str(run_key or "").encode("utf-8"))
        with open(tmp_zip, "rb") as fh:
            _write_field(
                "result_zip",
                os.path.basename(tmp_zip),
                "application/zip",
                fh.read(),
            )
        body.write(f"--{boundary}--\r\n".encode("utf-8"))

        upload_url = f"{server_url}/api/cluster/upload-result/{item.id}"
        req = urllib.request.Request(
            upload_url,
            data=body.getvalue(),
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                **_cluster_request_headers(),
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=_cluster_transfer_timeout()) as resp:
            result = json.loads(resp.read().decode("utf-8"))
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
            if os.path.isfile(tmp_zip):
                os.unlink(tmp_zip)
        except Exception:
            pass
