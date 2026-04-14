import argparse
import os
import sys
from typing import Iterable, Set

import httpx


def _check_status(
    response: httpx.Response,
    expected: Iterable[int],
    success_message: str,
    fail_message: str,
    failures: list[str],
) -> bool:
    expected_set: Set[int] = set(expected)
    if response.status_code in expected_set:
        print(f"[PASS] {success_message} (status={response.status_code})")
        return True
    print(f"[FAIL] {fail_message} (status={response.status_code}, body={response.text})")
    failures.append(fail_message)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Auth and permission smoke checks for InSAR backend.")
    parser.add_argument("--base-url", default=os.getenv("AUTH_SMOKE_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--admin-user", default=os.getenv("INIT_ADMIN_USERNAME", "admin"))
    parser.add_argument("--admin-password", default=os.getenv("INIT_ADMIN_PASSWORD", "ChangeMe123!"))
    parser.add_argument("--viewer-user", default=os.getenv("AUTH_SMOKE_VIEWER_USERNAME", ""))
    parser.add_argument("--viewer-password", default=os.getenv("AUTH_SMOKE_VIEWER_PASSWORD", ""))
    args = parser.parse_args()

    failures: list[str] = []
    timeout = httpx.Timeout(10.0)

    try:
        with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=timeout) as client:
            print(f"[*] Target: {args.base_url.rstrip('/')}")

            unauth_me = client.get("/api/auth/me")
            _check_status(
                unauth_me,
                {401},
                "未登录访问 /api/auth/me 返回 401",
                "未登录访问 /api/auth/me 未返回 401",
                failures,
            )

            login_admin = client.post(
                "/api/auth/login",
                json={"username": args.admin_user, "password": args.admin_password},
            )
            if not _check_status(
                login_admin,
                {200},
                "管理员登录成功",
                "管理员登录失败",
                failures,
            ):
                print("[INFO] 管理员登录失败，后续检查跳过。")
                return 1

            me_admin = client.get("/api/auth/me")
            if _check_status(
                me_admin,
                {200},
                "管理员会话可访问 /api/auth/me",
                "管理员会话无法访问 /api/auth/me",
                failures,
            ):
                role = (me_admin.json() or {}).get("role")
                if role != "admin":
                    print(f"[FAIL] 管理员角色异常，实际 role={role}")
                    failures.append("管理员角色异常")
                else:
                    print("[PASS] 管理员角色校验通过")

            audit_logs = client.get("/api/auth/audit-logs", params={"limit": 20})
            if _check_status(
                audit_logs,
                {200},
                "管理员可查询审计日志 /api/auth/audit-logs",
                "管理员无法查询审计日志 /api/auth/audit-logs",
                failures,
            ):
                payload = audit_logs.json()
                if isinstance(payload, list):
                    print(f"[PASS] 审计日志接口返回列表（条数={len(payload)}）")
                else:
                    print("[FAIL] 审计日志接口返回格式不是列表")
                    failures.append("审计日志接口返回格式异常")

            run_now_admin = client.post("/api/monitor/run-now")
            _check_status(
                run_now_admin,
                {200, 202, 400, 409},
                "管理员可触发写操作接口（未被鉴权拒绝）",
                "管理员触发写操作接口异常（可能被鉴权拒绝）",
                failures,
            )

            client.post("/api/auth/logout")

            if args.viewer_user and args.viewer_password:
                login_viewer = client.post(
                    "/api/auth/login",
                    json={"username": args.viewer_user, "password": args.viewer_password},
                )
                if _check_status(
                    login_viewer,
                    {200},
                    "只读用户登录成功",
                    "只读用户登录失败",
                    failures,
                ):
                    me_viewer = client.get("/api/auth/me")
                    if _check_status(
                        me_viewer,
                        {200},
                        "只读用户会话可访问 /api/auth/me",
                        "只读用户会话无法访问 /api/auth/me",
                        failures,
                    ):
                        role = (me_viewer.json() or {}).get("role")
                        if role != "viewer":
                            print(f"[FAIL] 只读用户角色异常，实际 role={role}")
                            failures.append("只读用户角色异常")
                        else:
                            print("[PASS] 只读用户角色校验通过")

                    run_now_viewer = client.post("/api/monitor/run-now")
                    _check_status(
                        run_now_viewer,
                        {403},
                        "只读用户写操作被拒绝（403）",
                        "只读用户写操作未被拒绝",
                        failures,
                    )
                    client.post("/api/auth/logout")
            else:
                print("[INFO] 未提供只读用户凭据，跳过 viewer 鉴权回归。")

    except httpx.RequestError as exc:
        print(f"[FAIL] 无法连接后端: {exc}")
        return 1

    if failures:
        print("\n[RESULT] 鉴权冒烟检查失败。")
        for item in failures:
            print(f"- {item}")
        return 1

    print("\n[RESULT] 鉴权冒烟检查通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
