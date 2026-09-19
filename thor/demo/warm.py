"""``THOR_DEMO_MODE=warm`` 用フォールバック / ウォームアップ実装。

**目的**: デモ本番中、LLM / CDV / Trino の初回応答遅延やネットワーク瞬断で
シナリオが崩れないよう、事前ウォーミング済みのシャドウ結果を返せるようにする。

**設計方針**:

* 本番コード (Tools / Crews / API) は ``thor.demo`` に依存しない。
  代わりに、必要な箇所で :func:`is_warm_mode` を **オプトインで** 参照し、
  ``True`` のときだけ :mod:`thor.demo.warm` の canned data で短絡する。
* デモ用テーブル / ダッシュボード / サマリーは *不変* の Python 定数として
  ここに置く。実データを S3 / Iceberg / CDV に流し込む必要はない
  (シャドウ環境なので、UI と Router から見える形式が合っていれば十分)。
* 実 API を叩かないので、ネットワークが落ちてもデモは進行する。

**環境変数**:

* ``THOR_DEMO_MODE=warm`` を有効化する唯一のスイッチ。
* ``THOR_DEMO_MODE=off`` (既定) では :func:`is_warm_mode` は False を返す。

**責務外**:

* Tool の内部書き換えはしない (invasive すぎる)。デモパスは各 Crew / Task が
  「先に warm を尋ねてから通常フォールバックに落ちる」形で組み立てる想定。
* Ossie YAML の生成は既に heuristic + LLM で動くので、デモ用テーブルの
  Ossie は事前 commit しておくこと (``semantic/datasets/iceberg/demo/*.yaml``)。
"""
from __future__ import annotations

import os
from typing import Any, Optional

# ------------------------------------------------------------------ #
# switch
# ------------------------------------------------------------------ #
_WARM_ENV_VAR = "THOR_DEMO_MODE"
_WARM_VALUE = "warm"


def is_warm_mode() -> bool:
    """``THOR_DEMO_MODE=warm`` なら True。それ以外 (未設定 / off) は False。"""
    return (os.environ.get(_WARM_ENV_VAR, "") or "").strip().lower() == _WARM_VALUE


# ------------------------------------------------------------------ #
# デモ用テーブル (Ingestion Crew の出力を模す)
# ------------------------------------------------------------------ #
# key: 完全修飾テーブル名 (catalog.schema.table)。UI ツリーはこれで解決する。
_WARM_TABLES: dict[str, dict[str, Any]] = {
    "iceberg.demo.sales_2024": {
        "fq": "iceberg.demo.sales_2024",
        "s3_source": "s3://demo-bucket/2024/sales_2024.xlsx",
        "row_count": 12480,
        "columns": [
            {"name": "region", "trino_type": "VARCHAR"},
            {"name": "order_date", "trino_type": "DATE"},
            {"name": "product", "trino_type": "VARCHAR"},
            {"name": "revenue", "trino_type": "DECIMAL(18,2)"},
            {"name": "customer_id", "trino_type": "BIGINT"},
        ],
        "similar_tables": ["iceberg.demo.sales_2023"],
        "ossie_path": "semantic/datasets/iceberg/demo/sales_2024.yaml",
    },
    "iceberg.demo.customers": {
        "fq": "iceberg.demo.customers",
        "s3_source": "s3://demo-bucket/2024/customers.csv",
        "row_count": 4210,
        "columns": [
            {"name": "customer_id", "trino_type": "BIGINT"},
            {"name": "name", "trino_type": "VARCHAR"},
            {"name": "segment", "trino_type": "VARCHAR"},
            {"name": "signup_date", "trino_type": "DATE"},
        ],
        "similar_tables": [],
        "ossie_path": "semantic/datasets/iceberg/demo/customers.yaml",
    },
}


def warm_table(fq: str) -> Optional[dict[str, Any]]:
    """warm mode の canned テーブル情報を返す。無ければ None。"""
    if not is_warm_mode():
        return None
    return _WARM_TABLES.get(fq)


# ------------------------------------------------------------------ #
# デモ用サマリー (AnalyticsSummaryCrew の出力を模す)
# ------------------------------------------------------------------ #
_WARM_SUMMARIES: dict[str, str] = {
    "iceberg.demo.sales_2024": """\
# sales_2024 サマリー

- **総売上**: ¥1,842,300,000 (12,480 件のオーダー)
- **最大地域**: 東京 (シェア 34.2%)、次いで大阪 (18.7%)
- **月次トレンド**: 3〜5 月に集中 (全体の 41%)。年末 12 月は例年通り閑散期
- **主要製品**: Product A (¥612M), Product C (¥398M), Product B (¥281M)
- **注意**: 4 月に revenue の外れ値 (>¥30M/order) が 3 件検出された
  → 与信管理チームに要確認 (customer_id: 108422, 108911, 109034)
""",
    "iceberg.demo.customers": """\
# customers サマリー

- **アクティブ顧客**: 4,210 名 (segment 別: Enterprise 812 / Mid 1,904 / SMB 1,494)
- **新規登録**: 2024 年 Q1 に 1,204 名 (前年比 +18%)
- **地域偏り**: 首都圏 62%、関西 21%、その他 17%
""",
}


def warm_summary(fq: str) -> Optional[str]:
    """warm mode の canned Markdown サマリーを返す。無ければ None。"""
    if not is_warm_mode():
        return None
    return _WARM_SUMMARIES.get(fq)


# ------------------------------------------------------------------ #
# デモ用ダッシュボード (AnalyticsDashboardCrew の出力を模す)
# ------------------------------------------------------------------ #
# CDV に事前作成済みのダッシュボードを直リンクで参照する。
# 実 URL は環境変数 THOR_DEMO_CDV_BASE_URL があればそちらを優先。
_WARM_DASHBOARDS: dict[str, dict[str, Any]] = {
    "iceberg.demo.sales_2024": {
        "dashboard_id": 42,
        "dashboard_path": "/arc/apps/dashboard/42",
        "title": "Sales 2024 — Monthly / Regional",
        "visuals": [
            {"kind": "line", "title": "月次売上", "measure": "revenue", "time": "order_date"},
            {"kind": "bar", "title": "地域別売上", "dimension": "region", "measure": "revenue"},
            {"kind": "kpi", "title": "総売上", "measure": "revenue"},
        ],
    },
    "iceberg.demo.customers": {
        "dashboard_id": 43,
        "dashboard_path": "/arc/apps/dashboard/43",
        "title": "Customers — Segments & Signups",
        "visuals": [
            {"kind": "bar", "title": "segment 別顧客数", "dimension": "segment"},
            {"kind": "line", "title": "月次新規登録", "measure": "signups", "time": "signup_date"},
        ],
    },
}


def warm_dashboard(fq: str) -> Optional[dict[str, Any]]:
    """warm mode の canned ダッシュボード情報を返す。無ければ None。

    ``dashboard_url`` は ``THOR_DEMO_CDV_BASE_URL`` があればそれを prefix にする
    (デモ環境の URL に差し替え可能)。無ければ ``dashboard_path`` のみ返す。
    """
    if not is_warm_mode():
        return None
    entry = _WARM_DASHBOARDS.get(fq)
    if entry is None:
        return None
    base = os.environ.get("THOR_DEMO_CDV_BASE_URL", "").rstrip("/")
    if base:
        return {**entry, "dashboard_url": f"{base}{entry['dashboard_path']}"}
    return dict(entry)


# ------------------------------------------------------------------ #
# 一覧 API (UI TreePane の 'warm' シャドウでも使える)
# ------------------------------------------------------------------ #
def list_warm_tables() -> list[str]:
    """warm mode で有効な (canned) テーブル FQ の一覧を返す。off なら空。"""
    if not is_warm_mode():
        return []
    return sorted(_WARM_TABLES.keys())


def has_warm_asset(fq: str) -> bool:
    """warm mode が有効で、かつ fq が canned セットに含まれているか。"""
    if not is_warm_mode():
        return False
    return (
        fq in _WARM_TABLES
        or fq in _WARM_SUMMARIES
        or fq in _WARM_DASHBOARDS
    )


__all__ = [
    "is_warm_mode",
    "warm_table",
    "warm_summary",
    "warm_dashboard",
    "list_warm_tables",
    "has_warm_asset",
]
