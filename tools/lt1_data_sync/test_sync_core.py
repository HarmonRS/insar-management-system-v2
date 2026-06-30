from __future__ import annotations

import tempfile
from pathlib import Path

from sync_core import (
    copy_unc_missing_assets,
    import_disk_assets_to_server,
    parse_path_list,
    read_inventory,
    scan_assets,
    write_inventory,
)


def write_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_scan_server_writes_inventory_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        server = root / "server"
        output = root / "server_assets.json"
        write_file(server / "LT1A_EXIST.tar.gz", b"same")
        assets = scan_assets([server])
        write_inventory(output, assets, [str(server)])

        loaded = read_inventory(output)
        assert len(loaded) == 1
        assert loaded[0].name == "LT1A_EXIST.tar.gz"


def test_unc_copy_uses_server_json_to_skip_existing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        server = root / "server"
        unc = root / "unc"
        target = root / "disk_a"
        report_dir = root / "reports"
        inventory = root / "server_assets.json"

        write_file(server / "LT1A_EXIST.tar.gz", b"same")
        write_file(unc / "LT1A_EXIST.tar.gz", b"same")
        write_file(unc / "LT1A_MISSING.tar.gz", b"new")
        write_inventory(inventory, scan_assets([server]), [str(server)])

        report = copy_unc_missing_assets([unc], inventory, [target], report_dir, execute=True)

        assert (target / "LT1A_MISSING.tar.gz").read_bytes() == b"new"
        assert not (target / "LT1A_EXIST.tar.gz").exists()
        assert sum(1 for item in report if item.action == "skip") == 1
        assert sum(1 for item in report if item.action == "copied") == 1


def test_import_disk_copies_or_moves_to_server_and_skips_existing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        disk = root / "disk"
        server = root / "server"
        report_dir = root / "reports"

        write_file(server / "LT1A_EXIST.tar.gz", b"same")
        write_file(disk / "LT1A_EXIST.tar.gz", b"same")
        write_file(disk / "LT1A_NEW.tar.gz", b"new")

        report = import_disk_assets_to_server([disk], [server], report_dir, execute=True, move=True)

        assert (server / "LT1A_NEW.tar.gz").read_bytes() == b"new"
        assert not (disk / "LT1A_NEW.tar.gz").exists()
        assert sum(1 for item in report if item.action == "skip") == 1
        assert sum(1 for item in report if item.action == "moved") == 1


def test_parse_path_list_accepts_semicolon_and_newline() -> None:
    paths = parse_path_list(r"E:\;F:\Data" + "\n" + r"\\server\share")
    assert len(paths) == 3
    assert str(paths[1]) == r"F:\Data"
    assert "server" in str(paths[2])


if __name__ == "__main__":
    test_scan_server_writes_inventory_json()
    test_unc_copy_uses_server_json_to_skip_existing()
    test_import_disk_copies_or_moves_to_server_and_skips_existing()
    test_parse_path_list_accepts_semicolon_and_newline()
    print("ok")
