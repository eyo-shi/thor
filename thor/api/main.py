"""Thor API のエントリポイント。

Cloudera AI Workbench Application として起動される。Knox 越しに配信され、
FastAPI が ``/api/*`` を提供し、``/`` は ``thor/api/static/`` の SPA
(React + Vite ビルド成果物) を静的配信する。

起動方法:

* ``python -m thor.api.main`` (Workbench Application の entry)
* ``thor-api`` (pyproject の script)

ポート:

* 環境変数 ``CDSW_APP_PORT`` があればそれを使う (Workbench が割り当てる)
* 無ければ ``THOR_API_PORT`` (既定 8080) をローカル開発用に使う
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from thor.api.routes import (
    artifacts as artifacts_route,
    catalog as catalog_route,
    files as files_route,
    health as health_route,
    ossie as ossie_route,
    query as query_route,
    sessions as sessions_route,
    wish as wish_route,
)
from thor.transport.logging import configure_logging, get_logger


def _resolve_static_dir() -> Path:
    """``thor/api/static`` のパスを解決する。

    Workbench Application (Jupyter kernel exec) では ``__file__`` が無いことが
    あるため、env / パッケージ位置 / cwd でフォールバックする。
    """
    try:
        return Path(__file__).resolve().parent / "static"
    except NameError:
        pass

    for key in ("CDSW_PROJECT_DIR", "CML_PROJECT_DIR"):
        root = os.environ.get(key)
        if root:
            return Path(root).resolve() / "thor" / "api" / "static"

    try:
        import inspect

        import thor.api as api_pkg

        return Path(inspect.getfile(api_pkg)).resolve().parent / "static"
    except Exception:
        pass

    return Path.cwd().resolve() / "thor" / "api" / "static"


_UVICORN_LOG_LEVELS = frozenset(
    {"critical", "error", "warning", "info", "debug", "trace"}
)


def _env_non_empty(key: str, default: str) -> str:
    """環境変数が未設定または空文字のとき default を返す。

    Workbench の AMP フォームで optional env を空のまま送ると、
    ``os.environ.get(key, default)`` では default にならない (キーは存在する)。
    """
    val = os.environ.get(key)
    if val is None or not str(val).strip():
        return default
    return str(val).strip()


def _uvicorn_log_level() -> str:
    level = _env_non_empty("THOR_LOG_LEVEL", "info").lower()
    return level if level in _UVICORN_LOG_LEVELS else "info"


def create_app() -> FastAPI:
    """FastAPI アプリを組み立てる。テストからも呼ぶ想定。"""
    configure_logging()
    logger = get_logger("thor.api")

    app = FastAPI(
        title="Thor API",
        version="0.0.1",
        description=(
            "Cloudera AI Agent Studio 上の Genie 相当マルチエージェントの "
            "FastAPI バックエンド。/api/* で Crew と各種メタデータを提供する。"
        ),
    )

    # CORS: Workbench の同一ドメインで配信されるので基本 same-origin。
    # 開発時 (Vite の 5173) からの叩き込みを許すため、明示リストで許可。
    dev_origins = os.environ.get(
        "THOR_API_DEV_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in dev_origins if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # /api/* と /healthz のルート登録
    app.include_router(health_route.router)
    app.include_router(catalog_route.router)
    app.include_router(files_route.router)
    app.include_router(query_route.router)
    app.include_router(ossie_route.router)
    app.include_router(artifacts_route.router)
    app.include_router(sessions_route.router)
    app.include_router(wish_route.router)

    # SPA (React + Vite ビルド成果物) を / から配信する。
    # ビルド前 / 開発モード用に static/ が無くても起動できるようにする。
    static_dir = _resolve_static_dir()
    if static_dir.is_dir() and any(static_dir.iterdir()):
        _mount_spa(app, static_dir, logger)
    else:
        _mount_placeholder(app, static_dir, logger)

    logger.info(
        "thor.api.ready",
        static_dir=str(static_dir),
        static_present=static_dir.is_dir(),
    )
    return app


def _mount_spa(app: FastAPI, static_dir: Path, logger: Any) -> None:
    """Vite ビルド成果物を / で SPA として配信する。

    * ``/assets/*`` などは通常の StaticFiles ハンドラで返す
    * それ以外の GET (SPA ルート) は ``index.html`` にフォールバックする
      (React Router のクライアントサイドルーティング用)
    """
    index_html = static_dir / "index.html"
    if not index_html.exists():
        logger.warning("thor.api.spa_missing_index", static_dir=str(static_dir))
        return

    # /assets/*, /favicon.ico など Vite が吐く実ファイルを配信
    app.mount(
        "/assets",
        StaticFiles(directory=str(static_dir / "assets")),
        name="assets",
    )

    @app.get("/", include_in_schema=False)
    async def _root() -> FileResponse:
        return FileResponse(index_html)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str) -> Response:
        # API パスや /healthz は上で捕捉済み。ここは SPA ルート専用。
        if full_path.startswith("api/") or full_path == "healthz":
            return JSONResponse(
                status_code=404,
                content={"error_code": "NOT_FOUND", "message": "Unknown API path"},
            )
        candidate = static_dir / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_html)

    logger.info("thor.api.spa_mounted", static_dir=str(static_dir))


def _mount_placeholder(app: FastAPI, static_dir: Path, logger: Any) -> None:
    """静的ビルドが無いときのプレースホルダ画面。"""
    logger.warning("thor.api.spa_absent", static_dir=str(static_dir))

    @app.get("/", include_in_schema=False)
    async def _placeholder() -> JSONResponse:
        return JSONResponse(
            {
                "service": "thor.api",
                "status": "ok",
                "ui": "not-built",
                "hint": (
                    "UI (thor_ui) の本番ビルドが未実行です。"
                    "`cd thor_ui && npm ci && npm run build` を走らせてから "
                    "再起動してください (もしくは AMP セットアップの step 2)。"
                ),
                "docs": "/docs",
            }
        )


# module-level app (uvicorn --factory を使わない場合に import される)
app = create_app()


def main() -> None:
    """uvicorn 起動関数 (pyproject の [project.scripts] からも呼ばれる)。"""
    import uvicorn

    # Workbench Application は CDSW_APP_PORT を割り当てる
    port_env = os.environ.get("CDSW_APP_PORT") or os.environ.get(
        "THOR_API_PORT", "8080"
    )
    try:
        port = int(port_env)
    except ValueError:
        port = 8080

    host = _env_non_empty("THOR_API_HOST", "0.0.0.0")
    log_level = _uvicorn_log_level()

    # import 文字列ではなく app オブジェクトを渡す (Workbench kernel exec 向け)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
