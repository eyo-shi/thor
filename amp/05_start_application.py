"""Workbench Application 起動エントリ (AMP Step 5)。

``start_application`` は Jupyter kernel 内で本スクリプトを exec し、
定義された ``app`` (FastAPI) を **Workbench 側が CDSW_APP_PORT で配信**する。
ここで uvicorn を二重起動すると ``address already in use`` になるため、
``TASK_TYPE=START_APPLICATION`` のときは ``serve()`` を呼ばない。

ローカル開発で ``python amp/05_start_application.py`` するときだけ
自前で uvicorn を起動する。
"""
from __future__ import annotations

import os


def _normalize_deploy_env() -> None:
    """空の optional env を Workbench 向けデフォルトに置き換える。"""
    defaults = {
        "THOR_LOG_LEVEL": "INFO",
        "THOR_DEMO_MODE": "off",
    }
    for key, default in defaults.items():
        if not (os.environ.get(key) or "").strip():
            os.environ[key] = default


def _workbench_serves_app() -> bool:
    """Workbench Application ランタイムが app を配信するか。"""
    if os.environ.get("TASK_TYPE") == "START_APPLICATION":
        return True
    # kernel exec + APP ポート割当済み → プラットフォーム側が bind 済み
    if os.environ.get("CDSW_APP_PORT"):
        try:
            import asyncio

            asyncio.get_running_loop()
            return True
        except RuntimeError:
            pass
    return False


_normalize_deploy_env()

from thor.api.main import app, serve  # noqa: E402

if __name__ == "__main__":
    if _workbench_serves_app():
        port = os.environ.get("CDSW_APP_PORT", "?")
        print(
            f"[amp:05] Workbench serves `app` on CDSW_APP_PORT={port} "
            "— skipping self-hosted uvicorn",
            flush=True,
        )
    else:
        serve()
