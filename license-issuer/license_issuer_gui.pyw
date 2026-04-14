from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from issue_license import (
    BACKEND_LICENSE_SERVICE_FILE,
    PUBLIC_KEY_FILE,
    get_key_status,
    get_machine_fingerprint,
    issue_license_file,
    rotate_key_pair,
    sync_backend_public_key,
    verify_license_file,
)


class LicenseIssuerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("InSAR 授权签发工具")
        self.geometry("920x720")
        self.minsize(860, 620)

        self.fingerprint_var = tk.StringVar()
        self.issue_to_var = tk.StringVar()
        self.issue_fingerprint_var = tk.StringVar()
        self.issue_days_var = tk.StringVar(value="365")
        self.issue_output_var = tk.StringVar()
        self.verify_path_var = tk.StringVar()
        self.backend_target_var = tk.StringVar(value=str(BACKEND_LICENSE_SERVICE_FILE))
        self.status_var = tk.StringVar(value="就绪")
        self.key_summary_var = tk.StringVar(value="")

        self._build_ui()
        self.refresh_fingerprint()
        self.refresh_keys()

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=12)
        container.pack(fill="both", expand=True)

        header = ttk.Frame(container)
        header.pack(fill="x")

        ttk.Label(
            header,
            text="InSAR 授权签发工具",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            header,
            text="基于现有 LIC2 协议，通过桌面窗体完成指纹获取、签发、验签和密钥管理。",
        ).pack(anchor="w", pady=(4, 10))

        notebook = ttk.Notebook(container)
        notebook.pack(fill="both", expand=True)

        self._build_fingerprint_tab(notebook)
        self._build_issue_tab(notebook)
        self._build_verify_tab(notebook)
        self._build_keys_tab(notebook)

        status_bar = ttk.Label(
            container,
            textvariable=self.status_var,
            relief="sunken",
            anchor="w",
            padding=(8, 4),
        )
        status_bar.pack(fill="x", pady=(10, 0))

    def _build_fingerprint_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=12)
        notebook.add(frame, text="机器指纹")

        ttk.Label(
            frame,
            text="获取当前机器指纹。该值可以发给签发端，用于生成绑定本机的授权文件。",
            wraplength=760,
        ).pack(anchor="w")

        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(16, 8))
        ttk.Entry(row, textvariable=self.fingerprint_var, state="readonly").pack(
            side="left",
            fill="x",
            expand=True,
        )

        button_row = ttk.Frame(frame)
        button_row.pack(fill="x", pady=(4, 8))
        ttk.Button(button_row, text="刷新", command=self.refresh_fingerprint).pack(side="left")
        ttk.Button(button_row, text="复制", command=self.copy_fingerprint).pack(side="left", padx=(8, 0))
        ttk.Button(
            button_row,
            text="填入签发表单",
            command=self.use_current_fingerprint_for_issue,
        ).pack(side="left", padx=(8, 0))

        self.fingerprint_details = ScrolledText(frame, height=18, wrap="word")
        self.fingerprint_details.pack(fill="both", expand=True, pady=(12, 0))
        self._set_text(
            self.fingerprint_details,
            "当前指纹由硬件标识计算得到，算法与 backend/app/license_service.py 保持一致。\n",
        )

    def _build_issue_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=12)
        notebook.add(frame, text="签发授权")

        form = ttk.Frame(frame)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="授权对象").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(form, textvariable=self.issue_to_var).grid(row=0, column=1, sticky="ew", pady=6)

        ttk.Label(form, text="机器指纹").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(form, textvariable=self.issue_fingerprint_var).grid(row=1, column=1, sticky="ew", pady=6)

        ttk.Label(form, text="有效天数").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(form, textvariable=self.issue_days_var, width=12).grid(row=2, column=1, sticky="w", pady=6)

        ttk.Label(form, text="输出文件").grid(row=3, column=0, sticky="w", pady=6)
        output_row = ttk.Frame(form)
        output_row.grid(row=3, column=1, sticky="ew", pady=6)
        output_row.columnconfigure(0, weight=1)
        ttk.Entry(output_row, textvariable=self.issue_output_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(output_row, text="浏览", command=self.browse_issue_output).grid(row=0, column=1, padx=(8, 0))

        button_row = ttk.Frame(frame)
        button_row.pack(fill="x", pady=(10, 8))
        ttk.Button(button_row, text="使用本机指纹", command=self.use_current_fingerprint_for_issue).pack(side="left")
        ttk.Button(button_row, text="生成授权文件", command=self.issue_license_action).pack(side="left", padx=(8, 0))

        self.issue_output_box = ScrolledText(frame, height=20, wrap="word")
        self.issue_output_box.pack(fill="both", expand=True, pady=(8, 0))
        self._set_text(
            self.issue_output_box,
            "填写表单后，点击“生成授权文件”。\n",
        )

    def _build_verify_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=12)
        notebook.add(frame, text="验证授权")

        ttk.Label(
            frame,
            text="使用当前目录中的 public_key.b64 验证已有的 .lic 授权文件。",
            wraplength=760,
        ).pack(anchor="w")

        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(14, 8))
        row.columnconfigure(0, weight=1)
        ttk.Entry(row, textvariable=self.verify_path_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(row, text="浏览", command=self.browse_verify_file).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(row, text="开始验证", command=self.verify_license_action).grid(row=0, column=2, padx=(8, 0))

        self.verify_output_box = ScrolledText(frame, height=24, wrap="word")
        self.verify_output_box.pack(fill="both", expand=True, pady=(8, 0))
        self._set_text(
            self.verify_output_box,
            "选择一个授权文件，然后点击“开始验证”。\n",
        )

    def _build_keys_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=12)
        notebook.add(frame, text="密钥管理")

        ttk.Label(
            frame,
            text="管理签发密钥，并可选地将 public_key.b64 同步到 backend/app/license_service.py。",
            wraplength=760,
        ).pack(anchor="w")

        target_row = ttk.Frame(frame)
        target_row.pack(fill="x", pady=(14, 8))
        target_row.columnconfigure(1, weight=1)
        ttk.Label(target_row, text="后端目标文件").grid(row=0, column=0, sticky="w")
        ttk.Entry(target_row, textvariable=self.backend_target_var).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Button(target_row, text="浏览", command=self.browse_backend_target).grid(row=0, column=2, padx=(8, 0))

        ttk.Label(frame, textvariable=self.key_summary_var, wraplength=780).pack(anchor="w", pady=(6, 8))

        button_row = ttk.Frame(frame)
        button_row.pack(fill="x", pady=(0, 8))
        ttk.Button(button_row, text="刷新状态", command=self.refresh_keys).pack(side="left")
        ttk.Button(button_row, text="轮换密钥", command=self.rotate_key_action).pack(side="left", padx=(8, 0))
        ttk.Button(button_row, text="强制轮换", command=lambda: self.rotate_key_action(force=True)).pack(side="left", padx=(8, 0))
        ttk.Button(button_row, text="同步公钥到后端", command=self.sync_backend_action).pack(side="left", padx=(8, 0))

        self.key_output_box = ScrolledText(frame, height=24, wrap="word")
        self.key_output_box.pack(fill="both", expand=True, pady=(8, 0))
        self._set_text(
            self.key_output_box,
            f"后端文件：{BACKEND_LICENSE_SERVICE_FILE}\n公钥文件：{PUBLIC_KEY_FILE}\n",
        )

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)

    def _set_text(self, widget: ScrolledText, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _copy_text(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()

    def refresh_fingerprint(self) -> None:
        fingerprint = get_machine_fingerprint()
        self.fingerprint_var.set(fingerprint)
        self._set_text(
            self.fingerprint_details,
            "当前机器指纹：\n\n"
            f"{fingerprint}\n\n"
            "可以点击“复制”发给签发端，也可以点击“填入签发表单”做本机测试。\n",
        )
        self._set_status("机器指纹已刷新")

    def copy_fingerprint(self) -> None:
        if not self.fingerprint_var.get().strip():
            self.refresh_fingerprint()
        self._copy_text(self.fingerprint_var.get().strip())
        self._set_status("机器指纹已复制")

    def use_current_fingerprint_for_issue(self) -> None:
        if not self.fingerprint_var.get().strip():
            self.refresh_fingerprint()
        self.issue_fingerprint_var.set(self.fingerprint_var.get().strip())
        self._set_status("签发表单已填入当前指纹")

    def browse_issue_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="选择输出 .lic 文件",
            defaultextension=".lic",
            filetypes=[("授权文件", "*.lic"), ("所有文件", "*.*")],
            initialfile="license.lic",
        )
        if path:
            self.issue_output_var.set(path)

    def browse_verify_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 .lic 文件",
            filetypes=[("授权文件", "*.lic"), ("所有文件", "*.*")],
        )
        if path:
            self.verify_path_var.set(path)

    def browse_backend_target(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 backend/app/license_service.py",
            filetypes=[("Python 文件", "*.py"), ("所有文件", "*.*")],
            initialfile="license_service.py",
        )
        if path:
            self.backend_target_var.set(path)

    def issue_license_action(self) -> None:
        try:
            days = int(self.issue_days_var.get().strip() or "365")
            result = issue_license_file(
                issued_to=self.issue_to_var.get().strip(),
                fingerprint=self.issue_fingerprint_var.get().strip(),
                days=days,
                output=self.issue_output_var.get().strip() or None,
            )
        except Exception as exc:
            messagebox.showerror("签发授权", str(exc))
            self._set_status("生成授权失败")
            return

        payload = result["payload"]
        output_path = result["output_path"]
        self.issue_output_var.set(output_path)
        text = (
            "授权文件生成成功。\n\n"
            f"输出文件：{output_path}\n"
            f"授权对象：{payload['issued_to']}\n"
            f"机器指纹：{payload['fingerprint']}\n"
            f"签发时间：{payload['issued_at']}\n"
            f"到期时间：{payload['expires_at']}\n"
        )
        self._set_text(self.issue_output_box, text)
        self._set_status("授权文件已生成")
        messagebox.showinfo("签发授权", f"授权文件已生成：\n{output_path}")

    def verify_license_action(self) -> None:
        path = self.verify_path_var.get().strip()
        if not path:
            messagebox.showwarning("验证授权", "请先选择一个 .lic 授权文件。")
            return

        try:
            result = verify_license_file(path)
        except Exception as exc:
            messagebox.showerror("验证授权", str(exc))
            self._set_status("授权验证失败")
            return

        if not result["ok"]:
            self._set_text(
                self.verify_output_box,
                json.dumps(result, ensure_ascii=False, indent=2),
            )
            self._set_status("授权文件无效")
            messagebox.showerror("验证授权", result["reason"])
            return

        text = (
            "授权验证通过。\n\n"
            f"授权文件：{result['license_file']}\n"
            f"授权对象：{result.get('issued_to')}\n"
            f"机器指纹：{result.get('fingerprint')}\n"
            f"签发时间：{result.get('issued_at')}\n"
            f"到期时间：{result.get('expires_at')}\n"
            f"是否过期：{'是' if result.get('expired') else '否'}\n"
        )
        self._set_text(self.verify_output_box, text)
        self._set_status("授权验证通过")
        messagebox.showinfo("验证授权", "授权验证通过。")

    def refresh_keys(self) -> None:
        status = get_key_status(self.backend_target_var.get().strip() or None)
        lines = [
            f"私钥存在：{'是' if status['private_key_exists'] else '否'}",
            f"公钥存在：{'是' if status['public_key_exists'] else '否'}",
            f"公钥已同步：{'是' if status['backend_synced'] else '否'}",
            f"私钥路径：{status['private_key_path']}",
            f"公钥路径：{status['public_key_path']}",
            f"后端文件：{status['backend_license_service_path']}",
        ]
        self.key_summary_var.set("\n".join(lines))

        key_details = {
            "public_key_b64": status.get("public_key_b64"),
            "backend_public_key_b64": status.get("backend_public_key_b64"),
            "backend_synced": status.get("backend_synced"),
        }
        self._set_text(self.key_output_box, json.dumps(key_details, ensure_ascii=False, indent=2))
        self._set_status("密钥状态已刷新")

    def rotate_key_action(self, force: bool = False) -> None:
        if force:
            confirmed = messagebox.askyesno(
                "强制轮换密钥",
                "这会覆盖现有密钥对，并使旧授权文件失效。确认继续吗？",
            )
            if not confirmed:
                return

        try:
            result = rotate_key_pair(force=force)
        except Exception as exc:
            messagebox.showerror("轮换密钥", str(exc))
            self._set_status("密钥轮换失败")
            return

        self.refresh_keys()
        self._copy_text(result["public_key_b64"])
        self._set_status("密钥轮换完成，公钥已复制")

        sync_now = messagebox.askyesno(
            "轮换密钥",
            "新密钥对已生成，公钥也已复制到剪贴板。\n\n现在要同步到 backend/app/license_service.py 吗？",
        )
        if sync_now:
            self.sync_backend_action()

    def sync_backend_action(self) -> None:
        try:
            result = sync_backend_public_key(
                target_path=self.backend_target_var.get().strip() or None,
            )
        except Exception as exc:
            messagebox.showerror("同步公钥", str(exc))
            self._set_status("后端公钥同步失败")
            return

        self.refresh_keys()
        changed_text = "已更新" if result["changed"] else "已是最新"
        self._set_status(f"后端公钥{changed_text}")
        messagebox.showinfo(
            "同步公钥",
            f"后端公钥{changed_text}：\n{result['target_path']}",
        )


def main() -> None:
    app = LicenseIssuerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
