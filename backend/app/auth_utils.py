import base64
import hashlib
import hmac
import re
import secrets
from typing import Tuple

from .config import settings


_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,64}$")
_PBKDF2_ITERATIONS = int(settings.AUTH_PBKDF2_ITERATIONS)


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def validate_username(username: str) -> Tuple[bool, str]:
    normalized = normalize_username(username)
    if not normalized:
        return False, "用户名不能为空。"
    if not _USERNAME_RE.match(normalized):
        return False, "用户名仅支持 3-64 位字母、数字、下划线、点和短横线。"
    return True, ""


def validate_password(password: str) -> Tuple[bool, str]:
    password = password or ""
    if len(password) < 8:
        return False, "密码长度至少 8 位。"
    if not re.search(r"[A-Z]", password):
        return False, "密码需包含至少一个大写字母。"
    if not re.search(r"[a-z]", password):
        return False, "密码需包含至少一个小写字母。"
    if not re.search(r"[0-9]", password):
        return False, "密码需包含至少一个数字。"
    return True, ""


def hash_password(password: str, iterations: int = _PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    salt_b64 = base64.b64encode(salt).decode("utf-8")
    hash_b64 = base64.b64encode(dk).decode("utf-8")
    return f"pbkdf2_sha256${iterations}${salt_b64}${hash_b64}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iteration_str, salt_b64, hash_b64 = (encoded or "").split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iteration_str)
        salt = base64.b64decode(salt_b64.encode("utf-8"))
        expected = base64.b64decode(hash_b64.encode("utf-8"))
    except Exception:
        return False

    actual = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def generate_session_token() -> str:
    return secrets.token_urlsafe(48)


def hash_session_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()
