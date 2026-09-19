"""可視化ヒューリスティック Tool (Pure Python)。

Ossie / TableInspectionResult のカラム情報から「どんな Visual を作ると
分かりやすいか」を規則ベースで提案する。LLM を呼ばず HTTP も打たない。

規則 (優先度順):
  1. time (date/timestamp) × measure が両方あるなら **line** (時系列)
  2. dimension (cardinality <= 20) × measure なら **bar** (カテゴリ集計)
  3. dimension (cardinality <= 8) だけなら **pie** (構成比)
  4. measure が 1 つあるなら **kpi** (代表値)
  5. どれにも該当しなければ **table** (先頭 100 行を素表示)

出力は :class:`VizPlan` に相当する dict のリスト。LLM (VizPlannerAgent) は
これを見て「不要そうな Visual を削る」「タイトルを日本語化する」だけを
担当する — 型判定・軸選定は決定論的な Python 側で完了させる。
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from thor.transport.errors import ok
from thor.transport.tool_base import BaseThorTool
from thor.transport.user_context import UserContext


# ------------------------------------------------------------------ #
# 引数スキーマ
# ------------------------------------------------------------------ #
class _ColumnInput(BaseModel):
    """VizHeuristicTool に渡すカラム 1 個分。"""

    model_config = {"extra": "ignore"}

    name: str
    trino_type: Optional[str] = None
    role: str = Field(
        "dimension",
        description="dimension | measure | time — TableInspectionResult と同じ規約",
    )
    distinct_count: Optional[int] = Field(None, ge=0)
    null_ratio: Optional[float] = Field(None, ge=0.0, le=1.0)


class VizHeuristicArgs(BaseModel):
    fq_table_name: str
    columns: list[_ColumnInput] = Field(..., min_length=1)
    max_visuals: int = Field(6, ge=1, le=20)

    model_config = {"extra": "ignore"}


# ------------------------------------------------------------------ #
# VizHeuristicTool
# ------------------------------------------------------------------ #
class VizHeuristicTool(BaseThorTool):
    """カラム情報から Visual 候補を機械的に列挙する。

    :attr:`requires_auth` を False にしているのは HTTP を打たず外部リソースを
    触らないため。認証チェックを飛ばして純粋関数として扱う。
    """

    name: str = "viz_heuristic"
    description: str = (
        "Given a table's fq_table_name and its columns (with role/cardinality), "
        "propose 1-6 visualisations (line/bar/pie/kpi/table) using deterministic "
        "rules. This tool does not call an LLM or hit any external service."
    )
    args_schema: type[BaseModel] = VizHeuristicArgs
    requires_auth: bool = False

    def run(
        self,
        user_ctx: Optional[UserContext],
        fq_table_name: str,
        columns: list[dict[str, Any]] | list[_ColumnInput],
        max_visuals: int = 6,
        **_: Any,
    ) -> dict[str, Any]:
        # dict / モデルどちらでも受け付ける
        cols: list[_ColumnInput] = [
            c if isinstance(c, _ColumnInput) else _ColumnInput.model_validate(c)
            for c in columns
        ]

        times = [c for c in cols if c.role == "time" or _is_time_type(c.trino_type)]
        measures = [c for c in cols if c.role == "measure" or _is_numeric_type(c.trino_type)]
        # dimension: role=dimension のもの、または role 未設定の非数値・非時刻
        dims = [
            c
            for c in cols
            if c not in times and c not in measures
        ]
        low_card_dims = [
            c for c in dims if c.distinct_count is not None and c.distinct_count <= 20
        ]
        very_low_card_dims = [
            c for c in low_card_dims if c.distinct_count is not None and c.distinct_count <= 8
        ]

        visuals: list[dict[str, Any]] = []

        # 1) 時系列 line (time × measure)
        if times and measures:
            t = times[0]
            m = measures[0]
            visuals.append(
                {
                    "name": f"{m.name} の推移",
                    "viz_type": "line",
                    "x": t.name,
                    "y": m.name,
                    "aggregation": "sum",
                    "group_by": low_card_dims[0].name if low_card_dims else None,
                    "description": (
                        f"{t.name} 軸で {m.name} の推移を集計。"
                        + (
                            f" {low_card_dims[0].name} で色分け。"
                            if low_card_dims
                            else ""
                        )
                    ),
                }
            )

        # 2) カテゴリ bar (low card dimension × measure)
        if low_card_dims and measures:
            for d in low_card_dims[:2]:
                m = measures[0]
                visuals.append(
                    {
                        "name": f"{d.name} 別の {m.name}",
                        "viz_type": "bar",
                        "x": d.name,
                        "y": m.name,
                        "aggregation": "sum",
                        "group_by": None,
                        "description": f"{d.name} 別に {m.name} を集計。",
                    }
                )

        # 3) 構成比 pie (very low card dim)
        if very_low_card_dims and not measures:
            d = very_low_card_dims[0]
            visuals.append(
                {
                    "name": f"{d.name} の構成比",
                    "viz_type": "pie",
                    "x": d.name,
                    "y": None,
                    "aggregation": "count",
                    "group_by": None,
                    "description": f"{d.name} のレコード数構成比。",
                }
            )

        # 4) KPI (measure 単独)
        if measures:
            m = measures[0]
            visuals.append(
                {
                    "name": f"{m.name} 合計",
                    "viz_type": "kpi",
                    "x": None,
                    "y": m.name,
                    "aggregation": "sum",
                    "group_by": None,
                    "description": f"{m.name} の総和 KPI。",
                }
            )

        # 5) フォールバック: 生テーブル
        if not visuals:
            visuals.append(
                {
                    "name": "先頭 100 行",
                    "viz_type": "table",
                    "x": None,
                    "y": None,
                    "aggregation": "count",
                    "group_by": None,
                    "description": "適切な measure / time が検出できなかったので先頭行を表示。",
                }
            )

        visuals = visuals[:max_visuals]

        return ok(
            {
                "fq_table_name": fq_table_name,
                "title": f"{fq_table_name} のダッシュボード",
                "visuals": visuals,
            }
        )


# ------------------------------------------------------------------ #
# 型判定ヘルパ (Trino 型名の緩い一致)
# ------------------------------------------------------------------ #
_NUMERIC_TYPES = (
    "bigint",
    "integer",
    "int",
    "smallint",
    "tinyint",
    "double",
    "real",
    "float",
    "decimal",
    "numeric",
)
_TIME_TYPES = ("date", "timestamp", "time")


def _is_numeric_type(t: Optional[str]) -> bool:
    if not t:
        return False
    tl = t.lower()
    return any(k in tl for k in _NUMERIC_TYPES)


def _is_time_type(t: Optional[str]) -> bool:
    if not t:
        return False
    tl = t.lower()
    return any(k in tl for k in _TIME_TYPES)


__all__ = ["VizHeuristicTool"]
