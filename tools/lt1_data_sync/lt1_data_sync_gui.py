from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

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


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"

DEFAULT_CONFIG = {
    "server_asset_roots": r"D:\LuTan1_Image_Pool_Zip" + "\n" + r"D:\LT1_data_lsarorbit",
    "inventory_output": "",
    "server_inventory_json": "",
    "unc_roots": "",
    "unc_copy_targets": "",
    "disk_roots": "",
    "import_server_roots": r"D:\LuTan1_Image_Pool_Zip" + "\n" + r"D:\LT1_data_lsarorbit",
    "report_dir": "",
    "move_on_import": False,
}


class LT1AssetTool:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("LT1 资产搬运工具")
        self.root.geometry("1120x820")
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.text_widgets: dict[str, ScrolledText] = {}

        config = self.load_config()
        self.vars = {
            "server_asset_roots": StringVar(value=config["server_asset_roots"]),
            "inventory_output": StringVar(value=config["inventory_output"]),
            "server_inventory_json": StringVar(value=config["server_inventory_json"]),
            "unc_roots": StringVar(value=config["unc_roots"]),
            "unc_copy_targets": StringVar(value=config["unc_copy_targets"]),
            "disk_roots": StringVar(value=config["disk_roots"]),
            "import_server_roots": StringVar(value=config["import_server_roots"]),
            "report_dir": StringVar(value=config["report_dir"]),
        }
        self.move_on_import = BooleanVar(value=bool(config["move_on_import"]))
        self.summary_text = StringVar(value="准备就绪")
        self.build_ui()
        self.root.after(100, self.drain_logs)

    def load_config(self) -> dict:
        if not CONFIG_PATH.exists():
            return dict(DEFAULT_CONFIG)
        try:
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return {**DEFAULT_CONFIG, **payload}
        except Exception:
            return dict(DEFAULT_CONFIG)

    def sync_text_vars(self) -> None:
        for key, widget in self.text_widgets.items():
            self.vars[key].set(widget.get("1.0", "end").strip())

    def save_config(self) -> None:
        self.sync_text_vars()
        payload = {key: var.get() for key, var in self.vars.items()}
        payload["move_on_import"] = self.move_on_import.get()
        CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        common = ttk.LabelFrame(outer, text="公共输出", padding=10)
        common.pack(fill="x")
        self.add_path_row(common, 0, "报告/日志目录", "report_dir")

        scan_box = ttk.LabelFrame(outer, text="1. 扫描服务器资产路径，生成随身 JSON", padding=10)
        scan_box.pack(fill="x", pady=(10, 0))
        self.add_multi_path_row(scan_box, 0, "服务器资产路径（多个用分号或换行）", "server_asset_roots")
        self.add_file_save_row(scan_box, 1, "服务器资产 JSON 保存为", "inventory_output")
        ttk.Button(scan_box, text="执行 1：扫描服务器并生成 JSON", command=lambda: self.start_worker(self.run_scan_server)).grid(
            row=2,
            column=1,
            sticky="w",
            pady=(8, 0),
        )

        unc_box = ttk.LabelFrame(outer, text="2. 扫描 UNC 多路径，按服务器 JSON 跳过已有，剩余复制到指定路径", padding=10)
        unc_box.pack(fill="x", pady=(10, 0))
        self.add_file_row(unc_box, 0, "读取服务器资产 JSON", "server_inventory_json")
        self.add_multi_path_row(unc_box, 1, "UNC 源路径（多个用分号或换行）", "unc_roots")
        self.add_multi_path_row(unc_box, 2, "复制目标路径（多个用分号或换行）", "unc_copy_targets")
        ttk.Button(unc_box, text="预览 2：只生成复制报告", command=lambda: self.start_worker(lambda logger: self.run_unc_copy(logger, execute=False))).grid(
            row=3,
            column=1,
            sticky="w",
            pady=(8, 0),
        )
        ttk.Button(unc_box, text="执行 2：复制缺失资产", command=lambda: self.start_worker(lambda logger: self.run_unc_copy(logger, execute=True))).grid(
            row=3,
            column=1,
            sticky="w",
            padx=(180, 0),
            pady=(8, 0),
        )

        import_box = ttk.LabelFrame(outer, text="3. 将指定磁盘路径资产复制或剪切到服务器资产路径", padding=10)
        import_box.pack(fill="x", pady=(10, 0))
        self.add_multi_path_row(import_box, 0, "磁盘资产路径（多个用分号或换行）", "disk_roots")
        self.add_multi_path_row(import_box, 1, "服务器资产路径（多个用分号或换行）", "import_server_roots")
        ttk.Checkbutton(import_box, text="剪切到服务器（不勾选则复制）", variable=self.move_on_import).grid(row=2, column=1, sticky="w")
        ttk.Button(import_box, text="预览 3：只生成导入报告", command=lambda: self.start_worker(lambda logger: self.run_import(logger, execute=False))).grid(
            row=3,
            column=1,
            sticky="w",
            pady=(8, 0),
        )
        ttk.Button(import_box, text="执行 3：导入到服务器", command=lambda: self.start_worker(lambda logger: self.run_import(logger, execute=True))).grid(
            row=3,
            column=1,
            sticky="w",
            padx=(180, 0),
            pady=(8, 0),
        )
        ttk.Label(import_box, text="执行 3 后，请回到服务器重新执行 1，生成新的随身 JSON。", foreground="#92400e").grid(
            row=4,
            column=1,
            sticky="w",
            pady=(8, 0),
        )

        control = ttk.Frame(outer)
        control.pack(fill="x", pady=10)
        ttk.Button(control, text="保存配置", command=self.handle_save_config).pack(side="left")
        ttk.Label(control, textvariable=self.summary_text, font=("Microsoft YaHei UI", 10, "bold")).pack(side="left", padx=16)

        log_box = ttk.LabelFrame(outer, text="日志", padding=10)
        log_box.pack(fill="both", expand=True)
        self.log_view = ScrolledText(log_box, height=14, wrap="word")
        self.log_view.pack(fill="both", expand=True)

    def add_path_row(self, frame: ttk.Frame, row: int, label: str, key: str) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(frame, textvariable=self.vars[key], width=105).grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Button(frame, text="选择", command=lambda: self.choose_dir(key)).grid(row=row, column=2, padx=(8, 0), pady=4)
        frame.columnconfigure(1, weight=1)

    def add_file_row(self, frame: ttk.Frame, row: int, label: str, key: str) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(frame, textvariable=self.vars[key], width=105).grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Button(frame, text="选择", command=lambda: self.choose_file(key)).grid(row=row, column=2, padx=(8, 0), pady=4)
        frame.columnconfigure(1, weight=1)

    def add_file_save_row(self, frame: ttk.Frame, row: int, label: str, key: str) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(frame, textvariable=self.vars[key], width=105).grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Button(frame, text="选择", command=lambda: self.choose_save_file(key)).grid(row=row, column=2, padx=(8, 0), pady=4)
        frame.columnconfigure(1, weight=1)

    def add_multi_path_row(self, frame: ttk.Frame, row: int, label: str, key: str) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=4)
        text = ScrolledText(frame, width=105, height=3, wrap="none")
        text.insert("1.0", self.vars[key].get())
        text.grid(row=row, column=1, sticky="ew", pady=4)
        self.text_widgets[key] = text
        ttk.Button(frame, text="追加目录", command=lambda: self.append_dir(key)).grid(row=row, column=2, padx=(8, 0), pady=4, sticky="n")
        frame.columnconfigure(1, weight=1)

    def choose_dir(self, key: str) -> None:
        selected = filedialog.askdirectory()
        if selected:
            self.vars[key].set(selected)

    def append_dir(self, key: str) -> None:
        selected = filedialog.askdirectory()
        if not selected:
            return
        widget = self.text_widgets.get(key)
        if not widget:
            self.vars[key].set(selected)
            return
        current = widget.get("1.0", "end").strip()
        widget.delete("1.0", "end")
        widget.insert("1.0", f"{current}\n{selected}" if current else selected)

    def choose_file(self, key: str) -> None:
        selected = filedialog.askopenfilename(filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
        if selected:
            self.vars[key].set(selected)

    def choose_save_file(self, key: str) -> None:
        selected = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            initialfile=f"server_assets_{stamp_text()}.json",
        )
        if selected:
            self.vars[key].set(selected)

    def report_dir(self) -> Path:
        text = self.vars["report_dir"].get().strip()
        if not text:
            raise ValueError("请指定报告/日志目录")
        return normalize_path(text)

    def logger(self) -> FileLogger:
        return FileLogger(self.report_dir() / "logs" / f"run_{stamp_text()}.log", self.log)

    def handle_save_config(self) -> None:
        self.save_config()
        messagebox.showinfo("已保存", f"配置已保存到：{CONFIG_PATH}")

    def log(self, message: str) -> None:
        self.log_queue.put(message)

    def drain_logs(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_view.insert("end", message + "\n")
            self.log_view.see("end")
        self.root.after(100, self.drain_logs)

    def start_worker(self, action) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("正在运行", "已有任务正在运行。")
            return
        self.save_config()
        self.log_view.delete("1.0", "end")
        self.worker = threading.Thread(target=self.run_action, args=(action,), daemon=True)
        self.worker.start()

    def run_action(self, action) -> None:
        try:
            action(self.logger())
        except Exception as exc:
            self.log(f"任务失败：{exc}")
            self.root.after(0, lambda: messagebox.showerror("任务失败", str(exc)))

    def run_scan_server(self, logger: FileLogger) -> None:
        roots = parse_path_list(self.vars["server_asset_roots"].get())
        if not roots:
            raise ValueError("请填写服务器资产路径")
        output = self.vars["inventory_output"].get().strip()
        if not output:
            raise ValueError("请指定服务器资产 JSON 保存路径")
        logger("开始扫描服务器资产路径")
        assets = scan_assets(roots, log=logger)
        write_inventory(normalize_path(output), assets, [str(root) for root in roots])
        self.root.after(0, lambda: self.summary_text.set(f"服务器资产 {len(assets)} 个，JSON 已生成"))
        logger(f"完成：{output}")

    def run_unc_copy(self, logger: FileLogger, *, execute: bool) -> None:
        inventory = self.vars["server_inventory_json"].get().strip()
        unc_roots = parse_path_list(self.vars["unc_roots"].get())
        targets = parse_path_list(self.vars["unc_copy_targets"].get())
        if not inventory:
            raise ValueError("请指定服务器资产 JSON")
        if not unc_roots:
            raise ValueError("请填写 UNC 源路径")
        if not targets:
            raise ValueError("请填写复制目标路径")
        logger("开始扫描 UNC 并按服务器 JSON 跳过已有资产")
        report = copy_unc_missing_assets(
            unc_roots,
            normalize_path(inventory),
            targets,
            self.report_dir() / "reports",
            execute=execute,
            log=logger,
        )
        copied = sum(1 for item in report if item.action == "copied")
        planned = sum(1 for item in report if item.action == "planned")
        skipped = sum(1 for item in report if item.action == "skip")
        failed = sum(1 for item in report if item.action == "failed")
        text = f"UNC 处理完成：copied={copied}, planned={planned}, skipped={skipped}, failed={failed}"
        self.root.after(0, lambda: self.summary_text.set(text))
        logger(text)

    def run_import(self, logger: FileLogger, *, execute: bool) -> None:
        disk_roots = parse_path_list(self.vars["disk_roots"].get())
        server_roots = parse_path_list(self.vars["import_server_roots"].get())
        if not disk_roots:
            raise ValueError("请填写磁盘资产路径")
        if not server_roots:
            raise ValueError("请填写服务器资产路径")
        logger("开始从磁盘导入资产到服务器")
        report = import_disk_assets_to_server(
            disk_roots,
            server_roots,
            self.report_dir() / "reports",
            execute=execute,
            move=self.move_on_import.get(),
            log=logger,
        )
        imported = sum(1 for item in report if item.action in {"copied", "moved"})
        planned = sum(1 for item in report if item.action == "planned")
        skipped = sum(1 for item in report if item.action == "skip")
        failed = sum(1 for item in report if item.action == "failed")
        text = f"磁盘导入完成：imported={imported}, planned={planned}, skipped={skipped}, failed={failed}。请重新执行 1。"
        self.root.after(0, lambda: self.summary_text.set(text))
        logger(text)


def main() -> None:
    root = Tk()
    LT1AssetTool(root)
    root.mainloop()


if __name__ == "__main__":
    main()
