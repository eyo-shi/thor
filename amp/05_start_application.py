"""Workbench Application 起動エントリ (AMP Step 5)。

``start_application`` は Jupyter kernel 内で本スクリプトを exec する。

* ``app`` を import した時点で Workbench が CDSW_APP_PORT を bind する場合がある
  → uvicorn 二重起動を避け、**プロセスを終了させず keep-alive** する
* ポートが空いていれば自前で uvicorn を起動 (kernel 内は別スレッド)
* Application の Status が ``Starting`` のままになるのを防ぐため、
  いずれの経路でもメインプロセスは blocking する
"""
from __future__ import annotations

import os
import socket
import time


def _normalize_deploy_env() -> None:
    """空の optional env を Workbench 向けデフォルトに置き換える。"""
    defaults = {
        "THOR_LOG_LEVEL": "INFO",
        "THOR_DEMO_MODE": "off",
    }
    for key, default in defaults.items():
        if not (os.environ.get(key) or "").strip():
            os.environ[key] = default


def _resolve_app_port() -> int:
    port_env = os.environ.get("CDSW_APP_PORT") or os.environ.get(
        "THOR_API_PORT", "8080"
    )
    try:
        return int(port_env)
    except ValueError:
        return 8080


def _port_is_listening(port: int, host: str = "127.0.0.1") -> bool:
    """APP ポートが既に listen 中か (Workbench 側配信の検出用)。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _keep_alive() -> None:
    """Workbench Application プロセスを Running 状態に保つ。"""
    print(
        "[amp:05] keeping application process alive (Workbench serves the port)",
        flush=True,
    )
    while True:
        time.sleep(3600)


_normalize_deploy_env()

from thor.api.main import app, serve  # noqa: E402

if __name__ == "__main__":
    port = _resolve_app_port()
    if _port_is_listening(port):
        print(
            f"[amp:05] CDSW_APP_PORT={port} already listening "
            "— skip uvicorn, keep process alive",
            flush=True,
        )
        _keep_alive()
    else:
        print(f"[amp:05] starting uvicorn on port {port}", flush=True)
        serve()
