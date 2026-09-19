"""Cloudera Data Visualization (CDV) 連携 Tool。

CDV は Cloudera が提供する BI/ダッシュボード SaaS/OnPrem 製品。REST API を
持つがバージョン差が大きい (v7 系と v8 系で微妙にパスが違う) ため、本モジュ
ールは薄いアダプタ層に留め、実運用時は :class:`_CDVEndpoints` を差し替える
だけで版差を吸収できるようにしておく。

**セキュリティ**:
* すべての Tool は :class:`BaseThorTool` を継承し、Knox JWT を Bearer で送る
  (:class:`ThorHttpClient` が自動でヘッダに付ける)。
* Knox JWT / 資格情報は LLM プロンプトにも Task inputs にも決して載らない。
* CDV base URL は環境変数 ``THOR_CDV_BASE_URL`` から取得する。未設定なら
  ``CDV_NOT_RUNNING`` エラーを返し、Dashboard Crew の guardrail が Crew を停止する。

**Tool 群**:
  * :class:`CDVStartupCheckTool` — CDV が起動しているかの疎通確認
  * :class:`CDVDatasetTool`      — Iceberg テーブルを CDV Dataset として登録
  * :class:`CDVVisualTool`       — 個別 Visual (line/bar/kpi/etc.) を作成
  * :class:`CDVDashboardTool`    — Visual を並べたダッシュボードを作成
"""
from __future__ import annotations

import os
from typing import Any, Optional

from pydantic import BaseModel, Field

from thor.transport.errors import ErrorCode, err, ok
from thor.transport.http import ThorHttpClient
from thor.transport.logging import get_logger
from thor.transport.tool_base import BaseThorTool
from thor.transport.user_context import UserContext

_logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# CDV endpoints (adapter, 版差吸収レイヤ)
# ------------------------------------------------------------------ #
class _CDVEndpoints:
    """CDV バージョンごとの API パスを 1 箇所にまとめる。

    現在は Cloudera Data Visualization v7 系を想定した典型パス。実運用では
    ``THOR_CDV_API_VERSION`` を見て切替える予定 (未実装 - v7 固定)。
    """

    #: CDV 疎通確認用の軽量エンドポイント (認証は必要だが GET のみ)
    STATUS: str = "/arc/apps/status"
    #: Dataset 一覧 / 検索
    DATASETS: str = "/arc/adminapi/v1/datasets"
    #: Visual (単一チャート)
    VISUALS: str = "/arc/adminapi/v1/visuals"
    #: Dashboard (Visual の集約)
    DASHBOARDS: str = "/arc/adminapi/v1/dashboards"


def _cdv_base_url() -> Optional[str]:
    """環境変数から CDV base URL を取得。無ければ None。"""
    url = os.environ.get("THOR_CDV_BASE_URL")
    return url.rstrip("/") if url else None


def _default_connection_id() -> Optional[str]:
    """CDV 側の Trino 接続の ID (事前登録済み)。

    デモ運用時、CDV には「Trino 用 Data Connection」を事前に 1 つ作っておく
    ことを前提とする (毎回 Ingestion のたびに接続を作るのは非現実的)。
    """
    return os.environ.get("THOR_CDV_TRINO_CONNECTION_ID")


# ------------------------------------------------------------------ #
# CDVStartupCheckTool
# ------------------------------------------------------------------ #
class CDVStartupCheckArgs(BaseModel):
    """引数無し (シグネチャ互換のため空スキーマ)。"""

    model_config = {"extra": "ignore"}


class CDVStartupCheckTool(BaseThorTool):
    """CDV が起動しているか確認する。

    * 環境変数 ``THOR_CDV_BASE_URL`` が未設定なら ``CDV_NOT_RUNNING`` で即返却
      (Crew の guardrail が後段を停止する)。
    * URL は設定されているが GET /status が 5xx / timeout なら ``CDV_NOT_RUNNING``。
    * 401/403 は起動している証拠なので ``running=true`` として返す
      (権限は個別 API 側で判定させる)。
    """

    name: str = "cdv_startup_check"
    description: str = (
        "Ping the Cloudera Data Visualization endpoint to verify it is running. "
        "Returns {running: bool, endpoint: str, version: str}. On failure returns "
        "error_code=CDV_NOT_RUNNING with a hint that the user should start CDV "
        "from the Workbench Data menu once."
    )
    args_schema: type[BaseModel] = CDVStartupCheckArgs

    def run(
        self,
        user_ctx: Optional[UserContext],
        **_: Any,
    ) -> dict[str, Any]:
        base = _cdv_base_url()
        if not base:
            return err(
                ErrorCode.CDV_NOT_RUNNING,
                "CDV base URL is not configured (set THOR_CDV_BASE_URL). "
                "Ask the user to start Cloudera Data Visualization from the "
                "Workbench Data menu once.",
            )

        with ThorHttpClient(base_url=base, timeout=5.0, max_attempts=1) as http:
            resp = http.get(_CDVEndpoints.STATUS, expect_json=True)

        # 401/403 は "起動しているが権限で弾かれた" と解釈 → running=True
        if resp.get("status") == "error":
            body = (resp.get("detail") or {}).get("status_code")
            if body in (401, 403):
                return ok(
                    {
                        "running": True,
                        "endpoint": base,
                        "version": None,
                        "message": "CDV is reachable (auth challenge received).",
                    }
                )
            return err(
                ErrorCode.CDV_NOT_RUNNING,
                f"CDV status check failed at {base}: {resp.get('message', 'unknown')}. "
                "Start CDV from the Workbench Data menu once.",
            )

        data = resp.get("data") or {}
        return ok(
            {
                "running": True,
                "endpoint": base,
                "version": data.get("version"),
                "message": data.get("message", "ok"),
            }
        )


# ------------------------------------------------------------------ #
# CDVDatasetTool
# ------------------------------------------------------------------ #
class CDVDatasetArgs(BaseModel):
    fq_table_name: str = Field(..., description="catalog.schema.table")
    #: Dataset に付ける表示名 (省略時は fq_table_name)
    display_name: Optional[str] = Field(None, max_length=256)
    #: CDV 側の Trino 接続 ID (省略時は THOR_CDV_TRINO_CONNECTION_ID)
    connection_id: Optional[str] = None
    #: 既存 Dataset があれば作らずに再利用するか (デフォルト True)
    reuse_if_exists: bool = True

    model_config = {"extra": "ignore"}


class CDVDatasetTool(BaseThorTool):
    """Iceberg テーブルを CDV Dataset として登録 (既存なら再利用)。

    Dataset は「Connection ID + テーブル名」で一意なので、まず既存を検索して
    無ければ作成する。作成に成功したら ``dataset_id`` を返し、以降の Visual
    作成でこれを参照する。
    """

    name: str = "cdv_dataset_create_or_get"
    description: str = (
        "Create or reuse a CDV Dataset for a given Iceberg fq_table_name. "
        "Returns {dataset_id, display_name, connection_id, created}. If a "
        "dataset already exists for this table on the connection, it is "
        "returned without creating a new one."
    )
    args_schema: type[BaseModel] = CDVDatasetArgs

    def run(
        self,
        user_ctx: Optional[UserContext],
        fq_table_name: str,
        display_name: Optional[str] = None,
        connection_id: Optional[str] = None,
        reuse_if_exists: bool = True,
        **_: Any,
    ) -> dict[str, Any]:
        base = _cdv_base_url()
        if not base:
            return err(ErrorCode.CDV_NOT_RUNNING, "CDV base URL not configured.")

        conn_id = connection_id or _default_connection_id()
        if not conn_id:
            return err(
                ErrorCode.CDV_API_FAILED,
                "CDV connection_id is not configured "
                "(set THOR_CDV_TRINO_CONNECTION_ID or pass connection_id).",
            )

        if fq_table_name.count(".") != 2:
            return err(
                ErrorCode.CDV_API_FAILED,
                f"fq_table_name must be 'catalog.schema.table' (got: {fq_table_name!r}).",
            )
        catalog, schema, table = fq_table_name.split(".")
        name = display_name or fq_table_name

        with ThorHttpClient(base_url=base, timeout=15.0, max_attempts=2) as http:
            # 1) 既存 Dataset 検索 (name + connection)
            if reuse_if_exists:
                existing = http.get(
                    _CDVEndpoints.DATASETS,
                    params={"name": name, "connection_id": conn_id},
                )
                if existing.get("status") == "ok":
                    items = _extract_items(existing.get("data"))
                    for item in items:
                        if item.get("name") == name:
                            return ok(
                                {
                                    "dataset_id": str(item.get("id")),
                                    "display_name": name,
                                    "connection_id": conn_id,
                                    "created": False,
                                }
                            )

            # 2) 新規作成
            payload = {
                "name": name,
                "connection_id": conn_id,
                "table_name": table,
                "schema_name": schema,
                "catalog_name": catalog,
                "table_type": "table",
            }
            created = http.post(_CDVEndpoints.DATASETS, json=payload)
            if created.get("status") == "error":
                # 409 相当は Conflict として素直に返す (デモ時に判別できるように)
                code = ErrorCode.CDV_DATASET_CONFLICT
                if (created.get("detail") or {}).get("status_code") not in (409,):
                    code = ErrorCode.CDV_API_FAILED
                return err(
                    code,
                    f"CDV dataset creation failed: {created.get('message')}",
                )
            data = created.get("data") or {}
            dataset_id = data.get("id") or data.get("dataset_id")
            if dataset_id is None:
                return err(
                    ErrorCode.CDV_API_FAILED,
                    f"CDV dataset creation returned unexpected payload: {data!r}",
                )
            return ok(
                {
                    "dataset_id": str(dataset_id),
                    "display_name": name,
                    "connection_id": conn_id,
                    "created": True,
                }
            )


# ------------------------------------------------------------------ #
# CDVVisualTool
# ------------------------------------------------------------------ #
class CDVVisualArgs(BaseModel):
    dataset_id: str = Field(..., description="CDV Dataset ID")
    name: str = Field(..., description="Visual の表示名", max_length=256)
    viz_type: str = Field(
        ..., description="line | bar | pie | kpi | table"
    )
    x: Optional[str] = Field(None, description="X 軸カラム (line/bar 系で必須)")
    y: Optional[str] = Field(None, description="Y 軸カラム (measure)")
    aggregation: str = Field(
        "sum", description="sum | avg | count | min | max"
    )
    group_by: Optional[str] = Field(None, description="第 2 分岐カラム (色分けなど)")
    limit: int = Field(1000, ge=1, le=100000)

    model_config = {"extra": "ignore"}


class CDVVisualTool(BaseThorTool):
    """1 個の Visual (チャート) を CDV Dataset の上に作る。

    返り値は ``visual_id``。作成した Visual は後段の :class:`CDVDashboardTool`
    でダッシュボードに組み込む。
    """

    name: str = "cdv_visual_create"
    description: str = (
        "Create a single CDV visual (line/bar/pie/kpi/table) on top of a "
        "dataset. Returns {visual_id, name, viz_type}. Use cdv_dashboard_create "
        "afterwards to combine visuals into a dashboard."
    )
    args_schema: type[BaseModel] = CDVVisualArgs

    def run(
        self,
        user_ctx: Optional[UserContext],
        dataset_id: str,
        name: str,
        viz_type: str,
        x: Optional[str] = None,
        y: Optional[str] = None,
        aggregation: str = "sum",
        group_by: Optional[str] = None,
        limit: int = 1000,
        **_: Any,
    ) -> dict[str, Any]:
        base = _cdv_base_url()
        if not base:
            return err(ErrorCode.CDV_NOT_RUNNING, "CDV base URL not configured.")

        if viz_type not in {"line", "bar", "pie", "kpi", "table"}:
            return err(
                ErrorCode.CDV_API_FAILED,
                f"Unsupported viz_type: {viz_type!r}",
            )

        # KPI 以外は X 軸必須
        if viz_type in {"line", "bar", "pie"} and not x:
            return err(
                ErrorCode.CDV_API_FAILED,
                f"viz_type={viz_type!r} requires 'x' column.",
            )
        # line / bar / kpi は measure (y) 必須
        if viz_type in {"line", "bar", "kpi"} and not y:
            return err(
                ErrorCode.CDV_API_FAILED,
                f"viz_type={viz_type!r} requires 'y' measure.",
            )

        payload = {
            "dataset_id": dataset_id,
            "name": name,
            "viz_type": viz_type,
            "x": x,
            "y": y,
            "aggregation": aggregation,
            "group_by": group_by,
            "limit": limit,
        }
        with ThorHttpClient(base_url=base, timeout=20.0, max_attempts=2) as http:
            created = http.post(_CDVEndpoints.VISUALS, json=payload)

        if created.get("status") == "error":
            return err(
                ErrorCode.CDV_API_FAILED,
                f"CDV visual creation failed: {created.get('message')}",
            )
        data = created.get("data") or {}
        visual_id = data.get("id") or data.get("visual_id")
        if visual_id is None:
            return err(
                ErrorCode.CDV_API_FAILED,
                f"CDV visual creation returned unexpected payload: {data!r}",
            )
        return ok(
            {
                "visual_id": str(visual_id),
                "name": name,
                "viz_type": viz_type,
            }
        )


# ------------------------------------------------------------------ #
# CDVDashboardTool
# ------------------------------------------------------------------ #
class CDVDashboardArgs(BaseModel):
    title: str = Field(..., description="Dashboard タイトル", max_length=256)
    visual_ids: list[str] = Field(
        ..., min_length=1, description="ダッシュボードに載せる Visual ID"
    )
    #: Grid layout: [{visual_id, row, col, width, height}] — 省略時は自動 2 列
    layout: Optional[list[dict[str, Any]]] = None
    description: Optional[str] = Field(None, max_length=1024)

    model_config = {"extra": "ignore"}


class CDVDashboardTool(BaseThorTool):
    """複数 Visual をまとめたダッシュボードを 1 個作成する。

    返り値は ``dashboard_id`` と ``dashboard_url``。UI (thor.ui.ResultPane) は
    ``dashboard_url`` を ``<iframe>`` で埋め込む。
    """

    name: str = "cdv_dashboard_create"
    description: str = (
        "Create a CDV dashboard that contains one or more visuals. "
        "Returns {dashboard_id, dashboard_url, title}. The dashboard_url is "
        "iframe-embeddable and inherits the caller's Knox session."
    )
    args_schema: type[BaseModel] = CDVDashboardArgs

    def run(
        self,
        user_ctx: Optional[UserContext],
        title: str,
        visual_ids: list[str],
        layout: Optional[list[dict[str, Any]]] = None,
        description: Optional[str] = None,
        **_: Any,
    ) -> dict[str, Any]:
        base = _cdv_base_url()
        if not base:
            return err(ErrorCode.CDV_NOT_RUNNING, "CDV base URL not configured.")

        if not visual_ids:
            return err(
                ErrorCode.CDV_API_FAILED,
                "visual_ids must contain at least one visual.",
            )

        # デフォルト layout: 2 列グリッド (width=6/12, height=4 行)
        if layout is None:
            layout = []
            for i, vid in enumerate(visual_ids):
                layout.append(
                    {
                        "visual_id": vid,
                        "row": (i // 2) * 4,
                        "col": (i % 2) * 6,
                        "width": 6,
                        "height": 4,
                    }
                )

        payload = {
            "title": title,
            "description": description or "",
            "visual_ids": visual_ids,
            "layout": layout,
        }
        with ThorHttpClient(base_url=base, timeout=20.0, max_attempts=2) as http:
            created = http.post(_CDVEndpoints.DASHBOARDS, json=payload)

        if created.get("status") == "error":
            return err(
                ErrorCode.CDV_API_FAILED,
                f"CDV dashboard creation failed: {created.get('message')}",
            )
        data = created.get("data") or {}
        dashboard_id = data.get("id") or data.get("dashboard_id")
        if dashboard_id is None:
            return err(
                ErrorCode.CDV_API_FAILED,
                f"CDV dashboard creation returned unexpected payload: {data!r}",
            )
        url = data.get("url") or f"{base}/arc/apps/dashboard/{dashboard_id}"
        return ok(
            {
                "dashboard_id": str(dashboard_id),
                "dashboard_url": url,
                "title": title,
            }
        )


# ------------------------------------------------------------------ #
# 内部ヘルパ
# ------------------------------------------------------------------ #
def _extract_items(data: Any) -> list[dict[str, Any]]:
    """CDV の list レスポンス形の揺れを吸収する。

    * ``{"items": [...]}`` 形式
    * ``{"results": [...]}`` 形式
    * 素の list
    """
    if data is None:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("items", "results", "data", "datasets"):
            v = data.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


__all__ = [
    "CDVStartupCheckTool",
    "CDVDatasetTool",
    "CDVVisualTool",
    "CDVDashboardTool",
]
