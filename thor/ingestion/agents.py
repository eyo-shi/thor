"""Ingestion Crew の Agent 定義。

Agent は「役割 (role) / 目的 (goal) / 経歴 (backstory) / 使える Tool」で
構成された Crew.ai の :class:`Agent` オブジェクト。ここではファクトリ関数
として提供し、テストでは Tool 差替え・LLM モックが行えるようにする。

Agent と Tool の対応 (プラン通り):

  S3ScoutAgent       -> S3ListTool, S3HeadTool, S3GetRangeTool
  FormatSnifferAgent -> MagicByteTool, CSVSnifferTool, ExcelHeaderDetectTool,
                        ParquetMetaTool, DataFramePreviewTool
  SchemaDrafterAgent -> TypeInferTool, NameProposerTool, SimilarTableSearchTool
  TableCreatorAgent  -> TableExistsTool, TrinoMetaTool, IcebergCreateTableTool
  OssieDrafterAgent  -> OssieWriteTool

LLM は Crew.ai の :class:`LLM` (LiteLLM ベース) を受け取れば十分。Cloudera
AI Inference のエンドポイントは環境変数から組み立てる想定。
"""
from __future__ import annotations

from typing import Any, Optional

from thor.tools import (
    CSVSnifferTool,
    DataFramePreviewTool,
    ExcelHeaderDetectTool,
    IcebergCreateTableTool,
    MagicByteTool,
    NameProposerTool,
    OssieWriteTool,
    ParquetMetaTool,
    S3GetRangeTool,
    S3HeadTool,
    S3ListTool,
    SimilarTableSearchTool,
    TableExistsTool,
    TrinoMetaTool,
    TypeInferTool,
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
# S3ScoutAgent
# ------------------------------------------------------------------ #
def make_s3_scout_agent(llm: Optional[Any] = None) -> Agent:
    return Agent(
        role="S3 Scout",
        goal=(
            "ユーザーが指示した S3 パスに実際にオブジェクトが存在するか確認し、"
            "その物理属性 (bucket, key, size, content_type, last_modified) を"
            "構造化して返す。"
        ),
        backstory=(
            "Cloudera のデータエンジニアで、IDBroker 経由で払い出された STS "
            "資格情報を使い、ユーザー権限で S3 を安全に探索する専門家。"
            "存在しないキーやアクセス拒否は必ずエラーコード付きで返す。"
        ),
        tools=[S3ListTool(), S3HeadTool(), S3GetRangeTool()],
        llm=llm,
        allow_delegation=False,
        verbose=False,
        memory=False,
    )


# ------------------------------------------------------------------ #
# FormatSnifferAgent
# ------------------------------------------------------------------ #
def make_format_sniffer_agent(llm: Optional[Any] = None) -> Agent:
    return Agent(
        role="Format Sniffer",
        goal=(
            "S3 上のファイルのマジックバイト・拡張子・先頭 1MB のバイナリを"
            "分析し、フォーマット (csv/tsv/xlsx/xls/parquet/json) と "
            "encoding / delimiter / sheet / header_row を確定する。"
            "未対応フォーマットは FORMAT_UNSUPPORTED で早期終了させる。"
        ),
        backstory=(
            "文字コード判定と Excel の複雑なヘッダー構造 (メタ K/V → 空行 →"
            " 表ヘッダー) を熟知したデータエンジニア。判定に自信がない場合は"
            "confidence を下げて返す。"
        ),
        tools=[
            MagicByteTool(),
            CSVSnifferTool(),
            ExcelHeaderDetectTool(),
            ParquetMetaTool(),
            DataFramePreviewTool(),
        ],
        llm=llm,
        allow_delegation=False,
        verbose=False,
        memory=False,
    )


# ------------------------------------------------------------------ #
# SchemaDrafterAgent
# ------------------------------------------------------------------ #
def make_schema_drafter_agent(llm: Optional[Any] = None) -> Agent:
    return Agent(
        role="Schema Drafter",
        goal=(
            "先頭サンプル行から各カラムの Trino 型 (BIGINT / DECIMAL(p,s) / "
            "TIMESTAMP(6) / VARCHAR(N) 等) を推定し、テーブル名の候補を提案"
            "する。既存の Ossie データセット群と命名衝突・意味的類似を"
            "チェックし、warning を同梱する。"
        ),
        backstory=(
            "Iceberg / Trino のデータ型に精通し、日本語カラム名や日付表記の"
            "ゆらぎも扱える。命名は英小文字 + アンダースコアに正規化する。"
        ),
        tools=[TypeInferTool(), NameProposerTool(), SimilarTableSearchTool()],
        llm=llm,
        allow_delegation=False,
        verbose=False,
        memory=False,
    )


# ------------------------------------------------------------------ #
# TableCreatorAgent
# ------------------------------------------------------------------ #
def make_table_creator_agent(llm: Optional[Any] = None) -> Agent:
    return Agent(
        role="Iceberg Table Creator",
        goal=(
            "衝突チェックと CREATE 権限の事前確認を行った上で、Iceberg テーブル"
            "を作成する。DDL は :class:`IcebergCreateTableTool` に組み立て"
            "させ、Agent は絶対に手で SQL を書かない (SQL injection 対策)。"
            "エラーは PERM_* / TRINO_* コードでそのまま返す。"
        ),
        backstory=(
            "Cloudera Data Warehouse (Trino) の運用経験が長く、Ranger の判定を"
            "受けるユーザー権限で DDL を発行する。副作用ありなので "
            "max_retries=0 を守る。"
        ),
        tools=[TableExistsTool(), TrinoMetaTool(), IcebergCreateTableTool()],
        llm=llm,
        allow_delegation=False,
        verbose=False,
        memory=False,
    )


# ------------------------------------------------------------------ #
# OssieDrafterAgent
# ------------------------------------------------------------------ #
def make_ossie_drafter_agent(llm: Optional[Any] = None) -> Agent:
    return Agent(
        role="Ossie Drafter",
        goal=(
            "作成したテーブルのメタデータから Apache Ossie の dataset YAML を"
            "ドラフトし、semantic/ 配下に書き出す。dimensions / measures の"
            "分類、sample_values、sample_queries までを含め、Text-to-SQL の"
            "RAG が使える形にする。commit=true なら Git commit まで行う。"
        ),
        backstory=(
            "セマンティックレイヤはコードとしてレビュー可能であるべきだと"
            "信じるデータプロダクトオーナー。YAML は最小構成でも Text2SQL が"
            "動くようにする。"
        ),
        tools=[OssieWriteTool()],
        llm=llm,
        allow_delegation=False,
        verbose=False,
        memory=False,
    )


__all__ = [
    "make_s3_scout_agent",
    "make_format_sniffer_agent",
    "make_schema_drafter_agent",
    "make_table_creator_agent",
    "make_ossie_drafter_agent",
]
