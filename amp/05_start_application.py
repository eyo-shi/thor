"""Workbench Application 起動エントリ (AMP Step 5)。

``start_application`` は run_session と同様に Jupyter kernel exec される
ことがある。optional env が空文字で渡されるケースを正規化してから
:mod:`thor.api.main` を import / 起動する。

* ``app`` — Workbench が FastAPI アプリを参照する場合に備える
* ``main()`` — スクリプトとして実行されたとき uvicorn を起動
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


_normalize_deploy_env()

from thor.api.main import app, serve  # noqa: E402

if __name__ == "__main__":
    serve()
