# -*- coding: utf-8 -*-
"""托管 backend/forward/mock_llm_server.py（正向自带的本地 mock LLM 服务）。

进程内单例：首个 MOCK 任务在守护线程里拉起 mock 服务（127.0.0.1:8731），后续任务
复用同一监听；纯本机线程，不影响反向，也不改任何 .env。
"""
from __future__ import annotations

import importlib.util
import socket
import threading
import time

from app.forward.root import FORWARD_ROOT

_lock = threading.Lock()
_module = None


def _load_module():
    """按绝对路径加载正向 mock 服务模块（文件内 __main__ 保护 → 不会自动起服务）。"""
    global _module
    if _module is None:
        path = FORWARD_ROOT / "mock_llm_server.py"
        if not path.is_file():
            raise RuntimeError(f"正向 mock 服务缺失：{path}")
        spec = importlib.util.spec_from_file_location("forward_mock_llm_server", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"正向 mock 服务无法加载：{path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _module = module
    return _module


def mock_port() -> int:
    """端口取正向模块自己的常量（源目录是唯一事实源）。"""
    return int(getattr(_load_module(), "PORT", 8731))


def is_running(timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", mock_port()), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_mock_server() -> bool:
    """确保 mock 服务在监听；返回 True = 本次调用新启动，False = 复用已有监听。"""
    with _lock:
        if is_running():
            return False
        module = _load_module()
        threading.Thread(target=module.main, name="forward-mock-llm", daemon=True).start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if is_running():
                return True
            time.sleep(0.1)
        raise RuntimeError(f"正向 mock LLM 服务启动失败（127.0.0.1:{mock_port()} 未监听）")
