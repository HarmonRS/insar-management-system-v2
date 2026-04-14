import subprocess
import os
import threading
from typing import Optional

from .config import settings
from .process_utils import is_any_process_running

# IDL 路径配置
IDL_CONFIG = {
    "idl_path": settings.IDL_EXECUTABLE,
    "workbench_path": settings.IDL_WORKBENCH_PATH,
}

def launch_idl_workbench():
    """
    启动 IDL Workbench (GUI 界面)
    """
    try:
        path = IDL_CONFIG["workbench_path"]
        if not os.path.exists(path):
            # 尝试一些常见的默认路径
            return False, f"未找到 IDL 路径: {path}。请检查配置。"
        
        # 使用 Popen 启动，不阻塞后端进程
        subprocess.Popen([path], creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        return True, "IDL Workbench 已启动"
    except Exception as e:
        return False, f"启动失败: {str(e)}"

def get_idl_status():
    """
    检查 IDL 是否在运行 (简单检查进程名)
    """
    return {
        "is_installed": os.path.exists(IDL_CONFIG["idl_path"]),
        "is_running": is_any_process_running(["idl.exe", "idlde.exe"]),
        "config": IDL_CONFIG
    }
