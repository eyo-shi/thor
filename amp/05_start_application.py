"""Workbench Application 起動エントリ (AMP Step 5)。

CML は ``CDSW_APP_PORT`` で listen するプロセスを Application 本体とみなす。
BrowserSvcs が外部 URL を localhost:CDSW_APP_PORT へ proxy する。

* ``CDSW_APP_PORT`` が注入されるまで短時間待つ (port 0 / Duplicate port 0 回避)
* Jupyter kernel 内では :func:`thor.api.main.serve` が uvicorn を別スレッド起動
* ``app`` は import 時点で公開 (Workbench の FastAPI 検出用)
"""
from __future__ import annotations

import os
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


def _wait_for_app_port(timeout_sec: float = 60.0) -> int:
    """``CDSW_APP_PORT`` が正の整数になるまで待つ。"""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        raw = (os.environ.get("CDSW_APP_PORT") or "").strip()
        if raw:
            try:
                port = int(raw)
            except ValueError:
                port = 0
            if port > 0:
                print(f"[amp:05] CDSW_APP_PORT={port}", flush=True)
                return port
        time.sleep(0.5)
    raise RuntimeError(
        "CDSW_APP_PORT was not assigned within "
        f"{timeout_sec:.0f}s (BrowserSvcs may log Duplicate port 0)"
    )


_normalize_deploy_env()
_wait_for_app_port()

from thor.api.main import app, serve  # noqa: E402

if __name__ == "__main__":
    print("[amp:05] starting uvicorn on CDSW_APP_PORT", flush=True)
    serve()
