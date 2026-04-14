import base64
import hashlib
import json
import os
import tkinter as tk
from datetime import datetime, timedelta, timezone
from tkinter import filedialog, messagebox
from typing import Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


APP_TITLE = "InSAR 授权管理器"


def _derive_aes_key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")


def _b64decode(data: str) -> bytes:
    return base64.b64decode(data.encode("utf-8"))


def _load_env(path: str) -> dict:
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def _save_env(path: str, updates: dict) -> None:
    existing = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig") as f:
            existing = f.readlines()

    def _set_line(key: str, value: str) -> bool:
        for idx, line in enumerate(existing):
            if line.strip().startswith(key + "="):
                existing[idx] = f"{key}={value}\n"
                return True
        return False

    for key, value in updates.items():
        if not _set_line(key, value):
            existing.append(f"{key}={value}\n")

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(existing)


def _gen_keys() -> Tuple[str, str]:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_bytes = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _b64encode(priv_bytes), _b64encode(pub_bytes)


def _load_private_key(path: str) -> Ed25519PrivateKey:
    raw = _b64decode(open(path, "r", encoding="utf-8").read().strip())
    return Ed25519PrivateKey.from_private_bytes(raw)


def _public_from_private(priv: Ed25519PrivateKey) -> str:
    pub = priv.public_key()
    pub_bytes = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _b64encode(pub_bytes)


def _get_fingerprint() -> str:
    try:
        from get_fingerprint import get_fingerprint
    except Exception:
        return ""
    return get_fingerprint()


def _resolve_paths(project_root: str) -> Tuple[str, str]:
    lic_dir = os.path.join(project_root, "backend", "license")
    lic_path = os.path.join(lic_dir, "license.lic")
    return lic_dir, lic_path


def _default_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")


def _parse_expiry(value: str) -> str:
    value = value.strip()
    if not value:
        value = _default_expiry()
    dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class LicenseManagerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("640x420")
        self.root.resizable(False, False)

        self.env_path = tk.StringVar()
        self.expiry = tk.StringVar(value=_default_expiry())
        self.status = tk.StringVar(value="请选择 .env 文件路径")

        self._build_ui()

    def _build_ui(self):
        frame = tk.Frame(self.root, padx=16, pady=16)
        frame.pack(fill=tk.BOTH, expand=True)

        title = tk.Label(frame, text="离线授权文件生成器", font=("Microsoft YaHei", 14, "bold"))
        title.pack(anchor="w")

        env_row = tk.Frame(frame)
        env_row.pack(fill=tk.X, pady=(18, 8))
        tk.Label(env_row, text=".env 路径:", width=12, anchor="w").pack(side=tk.LEFT)
        env_entry = tk.Entry(env_row, textvariable=self.env_path, width=60)
        env_entry.pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(env_row, text="选择", command=self._pick_env).pack(side=tk.LEFT)

        exp_row = tk.Frame(frame)
        exp_row.pack(fill=tk.X, pady=8)
        tk.Label(exp_row, text="到期时间:", width=12, anchor="w").pack(side=tk.LEFT)
        exp_entry = tk.Entry(exp_row, textvariable=self.expiry, width=30)
        exp_entry.pack(side=tk.LEFT)
        tk.Label(exp_row, text="格式：YYYY-MM-DD HH:MM:SS (UTC)", fg="#666").pack(side=tk.LEFT, padx=8)

        btn_row = tk.Frame(frame)
        btn_row.pack(fill=tk.X, pady=12)
        tk.Button(btn_row, text="生成/续期授权", width=20, command=self._run).pack(side=tk.LEFT)

        note = tk.Label(
            frame,
            text="说明：若检测到公私钥不匹配，将自动重建密钥对（旧授权全部失效）。",
            fg="#b91c1c",
        )
        note.pack(anchor="w", pady=(6, 10))

        status_label = tk.Label(frame, textvariable=self.status, fg="#0f172a", wraplength=580, justify="left")
        status_label.pack(anchor="w", pady=(12, 0))

    def _pick_env(self):
        path = filedialog.askopenfilename(title="选择 .env 文件", filetypes=[("Env file", ".env"), ("All files", "*.*")])
        if path:
            self.env_path.set(path)
            self.status.set("准备就绪，可生成授权文件。")

    def _run(self):
        env_path = self.env_path.get().strip()
        if not env_path or not os.path.exists(env_path):
            messagebox.showerror("错误", "请先选择有效的 .env 文件路径。")
            return

        try:
            exp_iso = _parse_expiry(self.expiry.get())
        except Exception:
            messagebox.showerror("错误", "到期时间格式错误，请使用 YYYY-MM-DD HH:MM:SS (UTC)。")
            return

        env = _load_env(env_path)
        secret = env.get("LICENSE_SECRET")
        if not secret:
            secret = _b64encode(os.urandom(32))

        project_root = os.path.dirname(env_path)
        lic_dir, lic_path = _resolve_paths(project_root)
        private_key_path = os.path.join(project_root, "license_private_key.txt")

        if os.path.exists(private_key_path):
            try:
                priv = _load_private_key(private_key_path)
                pub_from_priv = _public_from_private(priv)
                pub_env = env.get("LICENSE_PUBLIC_KEY", "")
                if pub_env and pub_env != pub_from_priv:
                    priv_b64, pub_b64 = _gen_keys()
                    priv = Ed25519PrivateKey.from_private_bytes(_b64decode(priv_b64))
                    pub_from_priv = pub_b64
                    with open(private_key_path, "w", encoding="utf-8") as f:
                        f.write(priv_b64)
                elif not pub_env:
                    pub_env = pub_from_priv
            except Exception:
                priv_b64, pub_b64 = _gen_keys()
                priv = Ed25519PrivateKey.from_private_bytes(_b64decode(priv_b64))
                pub_from_priv = pub_b64
                with open(private_key_path, "w", encoding="utf-8") as f:
                    f.write(priv_b64)
        else:
            priv_b64, pub_b64 = _gen_keys()
            priv = Ed25519PrivateKey.from_private_bytes(_b64decode(priv_b64))
            pub_from_priv = pub_b64
            with open(private_key_path, "w", encoding="utf-8") as f:
                f.write(priv_b64)

        _save_env(env_path, {
            "LICENSE_SECRET": secret,
            "LICENSE_PUBLIC_KEY": pub_from_priv,
        })

        fingerprint = _get_fingerprint()
        if not fingerprint:
            messagebox.showerror("错误", "无法获取机器指纹，请检查 get_fingerprint.py 是否可用。")
            return

        payload = {
            "product": "insar_management_system_v2",
            "fingerprint": fingerprint,
            "expires_at": exp_iso,
            "issued_at": datetime.now(timezone.utc).isoformat(),
        }
        plaintext = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        aes_key = _derive_aes_key(secret)
        aesgcm = AESGCM(aes_key)
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        header = b"LIC1"
        nonce_b64 = _b64encode(nonce).encode("utf-8")
        ct_b64 = _b64encode(ciphertext).encode("utf-8")
        data_to_sign = b"|".join([header, nonce_b64, ct_b64])

        signature = priv.sign(data_to_sign)
        sig_b64 = _b64encode(signature).encode("utf-8")
        blob = b"|".join([header, sig_b64, nonce_b64, ct_b64])

        os.makedirs(lic_dir, exist_ok=True)
        with open(lic_path, "wb") as f:
            f.write(blob)

        self.status.set(
            "授权文件已生成：\n"
            f"- license.lic: {lic_path}\n"
            f"- private key: {private_key_path}\n"
            f"- expires_at: {exp_iso}\n"
            "已自动写入 LICENSE_SECRET / LICENSE_PUBLIC_KEY 到 .env"
        )
        messagebox.showinfo("完成", "授权文件已生成并写入配置。")


if __name__ == "__main__":
    root = tk.Tk()
    app = LicenseManagerApp(root)
    root.mainloop()
