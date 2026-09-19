"""Analytics Crew の Agent 定義。

プラン準拠の役割分担 (Summary パスで使う 2 Agent のみ実装、Dashboard の
3 Agent は追って実装):

  TableInspectorAgent  -> TrinoQueryTool, TrinoMetaTool, OssieReadTool
  SummaryWriterAgent   -> OssieReadTool  (LLM 主体)

Text2SQL / VizPlanner / DashboardBuilder は Dashboard パスで追加する。
"""
from __future__ import annotations

from typing import Any, Optional

from thor.tools import (
    OssieReadTool,
    TrinoMetaTool,
    TrinoQueryTool,
)

try:  # crewai は本番依存。無い環境でも import は通す
    from crewai import Agent  # type: ignore
except Exception:  # pragma: no cover
    class Agent:  # type: ignore[no-redef]
        """crewai.Agent のスタブ。属性を保持するだけ。"""

        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)


# ------------------------------------------------------------------ #
# TableInspectorAgent
# ------------------------------------------------------------------ #
def make_table_inspector_agent(llm: Optional[Any] = None) -> Agent:
    """既存テーブルの存在確認・権限確認・統計取得・Ossie 参照を行う Agent。

    Summary / Dashboard の両パスで前処理として使う。副作用なし。
    """
    return Agent(
        role="Table Inspector",
        goal=(
            "指示された fq_table_name (catalog.schema.table) について、"
            "TrinoMetaTool で カラム定義と SHOW STATS を取得し、"
            "OssieReadTool で該当データセットの Ossie YAML があるか確認し、"
            "TrinoQueryTool で先頭 20 行のサンプルを取得する。"
            "SELECT 権限が無い場合は has_select_priv=false と "
            "error_code=PERM_SELECT_DENIED を返し、後段を止める。"
            "テーブルが存在しないなら exists=false と "
            "error_code=TRINO_TABLE_NOT_FOUND を返す。"
        ),
        backstory=(
            "Cloudera Data Warehouse (Trino/Iceberg) の運用に習熟したデータ"
            "エンジニアで、Ranger が返す権限エラーを正しく分類する。"
            "統計が未収集のテーブルは SHOW STATS が NULL を返すため、"
            "row_count_estimate は None のまま返して SummaryWriter に判断"
            "を委ねる。"
        ),
        tools=[TrinoMetaTool(), TrinoQueryTool(), OssieReadTool()],
        llm=llm,
        allow_delegation=False,
        verbose=False,
        memory=False,
    )


# ------------------------------------------------------------------ #
# SummaryWriterAgent
# ------------------------------------------------------------------ #
def make_summary_writer_agent(llm: Optional[Any] = None) -> Agent:
    """カラム統計とサンプル行から、日本語の Markdown サマリーを書く Agent。

    副作用なし。LLM 主体。TrinoQueryTool は使わない
    (追加クエリを避け、TableInspector が集めた情報のみで書き切る)。
    """
    return Agent(
        role="Summary Writer",
        goal=(
            "TableInspectionResult のカラム統計 (null_ratio, distinct_count, "
            "low_value, high_value) とサンプル行から、テーブルの内容を "
            "5-15 行の日本語 Markdown で要約する。"
            "見出しレベルは ## から。"
            "分布・トレンド・外れ値・データ品質の 4 観点をカバーし、"
            "根拠のない断定は避ける (統計が None のカラムは 'ヒント不足' と明示)。"
            "Ossie が存在するテーブルは、Ossie の description / dimensions / "
            "measures をそのまま踏まえた上で追加情報を書く。"
            "最後に「次に見ると良さそうな SQL」を 2-3 件、"
            "question と sql の組で提案する (Text2SQL の Few-shot に使う想定)。"
        ),
        backstory=(
            "ビジネスサイドとエンジニアの両方から尊敬されるデータアナリスト。"
            "統計が乏しくても分かることだけを丁寧に書き、事実と推測を分けて示す。"
            "個人情報らしきカラム名 (email, phone, address 等) は具体値を"
            "サマリーに書かず、'PII の可能性あり' と警告に回す。"
        ),
        tools=[OssieReadTool()],
        llm=llm,
        allow_delegation=False,
        verbose=False,
        memory=False,
    )


__all__ = [
    "make_table_inspector_agent",
    "make_summary_writer_agent",
]
