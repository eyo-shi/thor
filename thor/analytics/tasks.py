"""Analytics Crew の Task 定義 (Summary パス, Sequential 2 タスク)。

タスク順序:

  1. inspect_table_task    (TableInspector, guardrail 付き)
  2. write_summary_task    (SummaryWriter, LLM 主体)

副作用なし。すべて max_retries=1 (LLM が JSON を壊した場合の 1 回リカバリ)。
"""
from __future__ import annotations

from typing import Any, Optional

from thor.analytics.models import SummaryReport, TableInspectionResult

try:
    from crewai import Task  # type: ignore
except Exception:  # pragma: no cover
    class Task:  # type: ignore[no-redef]
        """crewai.Task のスタブ。属性を保持するだけ。"""

        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)


# ------------------------------------------------------------------ #
# 1. inspect_table_task
# ------------------------------------------------------------------ #
def make_inspect_table_task(agent: Any) -> Task:
    """存在確認・権限・統計・Ossie 参照を 1 タスクにまとめる。

    LLM が実行順を間違えないよう、description に手順を明示。
    """
    return Task(
        description=(
            "対象テーブル fq_table_name={fq_table_name} を調査せよ。"
            "以下の順で必ず実行する: "
            "(a) fq_table_name を catalog / schema / table にパースする "
            "(3 セグメント想定、'iceberg.demo.sales_2024' → catalog='iceberg', "
            "schema='demo', table='sales_2024')。"
            "(b) trino_meta で columns と SHOW STATS を取る。"
            "  権限不足なら error_code=PERM_SELECT_DENIED、"
            "  テーブル未存在なら error_code=TRINO_TABLE_NOT_FOUND を返し、"
            "  それ以外のフィールドは空/None で構わない。"
            "(c) ossie_read で fq_name={fq_table_name} を読み、"
            "  存在すれば description / dimensions / measures を取り出す "
            "  (無くても error にせず、ossie_yaml_path=None で継続する)。"
            "(d) trino_query で 'SELECT * FROM {fq_table_name} LIMIT 20' を実行し、"
            "  sample_rows に詰める。0 行でも error にはしない。"
            "(e) 各カラムの role は Ossie に一致名があればそれを、"
            "  無ければ trino_type から 'measure' (数値系) / 'time' (date/timestamp) / "
            "  'dimension' (それ以外) を選ぶ。"
        ),
        expected_output=(
            "TableInspectionResult の JSON。fq_table_name, catalog, schema, "
            "table, exists, has_select_priv, error_code, message, columns "
            "(name/trino_type/nullable/null_ratio/distinct_count/low_value/"
            "high_value/role), row_count_estimate, ossie_yaml_path, "
            "ossie_description, ossie_dimensions, ossie_measures, sample_rows "
            "を含む。"
        ),
        agent=agent,
        output_json=TableInspectionResult,
        max_retries=1,
    )


# ------------------------------------------------------------------ #
# 2. write_summary_task
# ------------------------------------------------------------------ #
def make_write_summary_task(agent: Any, context: list[Task]) -> Task:
    """LLM が Markdown サマリーを書き切るタスク。

    guardrail (inspect_table) を通過している前提。ここでは追加の Trino
    アクセスはせず、context の統計とサンプル行だけで書く。
    """
    return Task(
        description=(
            "前タスクの TableInspectionResult を踏まえ、日本語 Markdown で "
            "テーブル内容を 5-15 行程度に要約せよ。"
            "含めるべき観点 (それぞれ 1-3 行): "
            "  1. 全体像 (行数目安・カラム数・主な dimension と measure)。"
            "     row_count_estimate が None のときは「統計未収集」と明示。"
            "  2. 分布 (distinct_count が小さい dimension は代表値を列挙、"
            "     数値カラムは low_value / high_value を提示)。"
            "  3. データ品質 (null_ratio が 0.2 以上のカラムは列挙、"
            "     sample_rows が空なら「サンプル取得 0 行」と警告)。"
            "  4. Ossie に description があればそれを冒頭に置き、"
            "     無ければ「セマンティックレイヤ未整備」と 1 行で触れる。"
            "  5. 個人情報が疑われるカラム名 (email/phone/address/name 等) は "
            "     具体値を書かず warnings に回す。"
            "最後に sample_queries として "
            "'question (日本語) と sql (Trino SELECT)' の組を 2-3 件提案。"
            "SQL は fq_table_name={fq_table_name} を使い、"
            "破壊系 (INSERT/UPDATE/DELETE/CREATE/DROP) は絶対に含めない。"
        ),
        expected_output=(
            "SummaryReport の JSON。fq_table_name, summary_markdown, "
            "observations (category/headline/detail), warnings, sample_queries "
            "([{question, sql}]) を含む。"
        ),
        agent=agent,
        context=context,
        output_json=SummaryReport,
        max_retries=1,
    )


# ------------------------------------------------------------------ #
# guardrail helper
# ------------------------------------------------------------------ #
def inspect_table_guardrail(
    output: Any,
) -> tuple[bool, Optional[str]]:
    """``inspect_table_task`` の結果を検査するガードレール。

    Crew.ai の Task には ``guardrail`` パラメータがあり、``(ok, feedback)``
    を返す関数を受け取る。ok=False で Crew は次タスクに進まず停止する。

    テーブル未存在 / SELECT 権限なしの場合、SummaryWriter を呼ばずに終了する。
    (LLM トークンの無駄と、ユーザーへの誤解を招くサマリーの生成を防ぐ)。
    """
    if isinstance(output, TableInspectionResult):
        result = output
    elif isinstance(output, dict):
        try:
            result = TableInspectionResult.model_validate(output)
        except Exception:  # noqa: BLE001
            return False, f"guardrail: could not parse output: {output!r}"
    else:
        return False, f"guardrail: unexpected output type: {type(output).__name__}"

    if not result.exists:
        return False, (
            f"Table {result.fq_table_name!r} not found: "
            f"{result.message or 'no such table'}. Ask the user to verify the name."
        )
    if not result.has_select_priv:
        return False, (
            f"SELECT permission denied on {result.fq_table_name!r}: "
            f"{result.message or 'contact the workspace admin'}."
        )
    return True, None


__all__ = [
    "make_inspect_table_task",
    "make_write_summary_task",
    "inspect_table_guardrail",
]
