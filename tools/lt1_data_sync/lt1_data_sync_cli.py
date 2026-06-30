from __future__ import annotations

import argparse

from sync_core import (
    FileLogger,
    copy_unc_missing_assets,
    import_disk_assets_to_server,
    normalize_path,
    parse_path_list,
    scan_assets,
    stamp_text,
    write_inventory,
)


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--report-dir", required=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LT1 asset transfer tool")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan-server", help="Scan server asset paths and write a JSON inventory")
    add_common(scan)
    scan.add_argument("--server-roots", required=True, help="Server asset paths separated by semicolon/newline")
    scan.add_argument("--output-json", required=True)

    copy = sub.add_parser("copy-unc", help="Copy UNC assets that are missing from server inventory")
    add_common(copy)
    copy.add_argument("--server-json", required=True)
    copy.add_argument("--unc-roots", required=True, help="UNC source paths separated by semicolon/newline")
    copy.add_argument("--targets", required=True, help="Copy target paths separated by semicolon/newline")
    copy.add_argument("--execute", action="store_true")

    imp = sub.add_parser("import-disk", help="Copy or move disk assets into server asset paths")
    add_common(imp)
    imp.add_argument("--disk-roots", required=True, help="Disk asset paths separated by semicolon/newline")
    imp.add_argument("--server-roots", required=True, help="Server asset paths separated by semicolon/newline")
    imp.add_argument("--execute", action="store_true")
    imp.add_argument("--move", action="store_true")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_dir = normalize_path(args.report_dir)
    logger = FileLogger(report_dir / "logs" / f"cli_{stamp_text()}.log", print)

    if args.command == "scan-server":
        roots = parse_path_list(args.server_roots)
        assets = scan_assets(roots, log=logger)
        write_inventory(normalize_path(args.output_json), assets, [str(root) for root in roots])
        logger(f"服务器资产 JSON 已生成：{args.output_json}，资产 {len(assets)} 个")
        return 0

    if args.command == "copy-unc":
        report = copy_unc_missing_assets(
            parse_path_list(args.unc_roots),
            normalize_path(args.server_json),
            parse_path_list(args.targets),
            report_dir / "reports",
            execute=args.execute,
            log=logger,
        )
        failed = sum(1 for item in report if item.action == "failed")
        return 1 if failed else 0

    if args.command == "import-disk":
        report = import_disk_assets_to_server(
            parse_path_list(args.disk_roots),
            parse_path_list(args.server_roots),
            report_dir / "reports",
            execute=args.execute,
            move=args.move,
            log=logger,
        )
        failed = sum(1 for item in report if item.action == "failed")
        logger("导入后请重新执行 scan-server，生成新的随身 JSON。")
        return 1 if failed else 0

    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
