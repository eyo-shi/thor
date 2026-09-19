"""Ingestion Crew の Task 出力スキーマ (Pydantic v2)。

各 Task の ``output_json`` に指定することで、LLM が JSON でない自由記述を
返した場合に Crew.ai が自動で再要求してくれる。Task 間の受け渡し
(``context=[prev_task]``) はこの型を経由する。

タスク → 型の対応:

  locate_s3_object_task            -> LocateS3ObjectResult
  sniff_format_task                -> SniffFormatResult
  extract_dataframe_task           -> ExtractDataFrameResult
  propose_schema_and_name_task     -> ProposeSchemaAndNameResult
  check_conflict_and_permissions_task -> ConflictAndPermissionsResult
  create_iceberg_table_task        -> CreateIcebergTableResult
  draft_ossie_task                 -> DraftOssieResult
  wrap_up_task                     -> IngestionReport
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ------------------------------------------------------------------ #
# 1. locate_s3_object
# ------------------------------------------------------------------ #
class LocateS3ObjectResult(BaseModel):
    """S3 上のオブジェクトの物理情報。"""

    bucket: str
    key: str
    size: int
    content_type: Optional[str] = None
    last_modified: Optional[str] = None
    exists: bool = True


# ------------------------------------------------------------------ #
# 2. sniff_format
# ------------------------------------------------------------------ #
class SniffFormatResult(BaseModel):
    """フォーマット判定結果 (MagicByte / CSVSniffer / ExcelHeaderDetect の集約)。"""

    format: str = Field(..., description="csv / tsv / xlsx / xls / parquet / json")
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    encoding: Optional[str] = None  # csv/tsv 用
    delimiter: Optional[str] = None  # csv/tsv 用
    sheet: Optional[str] = None  # xlsx/xls 用
    header_row: Optional[int] = Field(None, description="0-indexed. xlsx で必要")
    supported: bool = True
    reason: Optional[str] = None  # supported=False のときの説明


# ------------------------------------------------------------------ #
# 3. extract_dataframe
# ------------------------------------------------------------------ #
class ColumnSample(BaseModel):
    name: str
    sample_values: list[Any] = Field(default_factory=list)


class ExtractDataFrameResult(BaseModel):
    """DataFramePreview の生の結果 + 次ステップに渡す最小情報。"""

    columns: list[ColumnSample]
    preview_rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count_preview: int
    full_row_count_hint: Optional[int] = None


# ------------------------------------------------------------------ #
# 4. propose_schema_and_name
# ------------------------------------------------------------------ #
class ColumnProposal(BaseModel):
    name: str
    trino_type: str
    nullable: bool = True
    role: str = Field("dimension", description="dimension / measure / time")
    description: Optional[str] = None


class ProposeSchemaAndNameResult(BaseModel):
    """スキーマ + テーブル名の提案。命名衝突検索の結果を同梱。"""

    catalog: str = "iceberg"
    target_schema: str
    proposed_table_name: str
    columns: list[ColumnProposal]
    partitioning: list[str] = Field(default_factory=list)
    similar_tables: list[dict[str, Any]] = Field(default_factory=list)
    exact_conflicts: list[dict[str, Any]] = Field(default_factory=list)


# ------------------------------------------------------------------ #
# 5. check_conflict_and_permissions  (guardrail)
# ------------------------------------------------------------------ #
class ConflictAndPermissionsResult(BaseModel):
    """CREATE 前の最終ゲート。

    * ``has_conflict=True`` なら Crew を止めて呼び出し側に別名を問い合わせる
    * ``has_create_priv=False`` なら Crew を止めて権限エラーを返す
    """

    has_conflict: bool
    has_create_priv: bool
    resolved_table: str
    error_code: Optional[str] = None
    message: Optional[str] = None


# ------------------------------------------------------------------ #
# 6. create_iceberg_table
# ------------------------------------------------------------------ #
class CreateIcebergTableResult(BaseModel):
    fq_table_name: str
    ddl: str
    column_count: int


# ------------------------------------------------------------------ #
# 7. draft_ossie
# ------------------------------------------------------------------ #
class DraftOssieResult(BaseModel):
    fq_name: str
    yaml_path: str
    git_status: str  # "committed" / "skipped"
    commit_sha: Optional[str] = None


# ------------------------------------------------------------------ #
# 8. wrap_up
# ------------------------------------------------------------------ #
class IngestionReport(BaseModel):
    """ユーザーに返す最終レポート (Markdown はレンダー時に組み立てる)。"""

    model_config = ConfigDict(extra="ignore")

    fq_table_name: str
    ddl: str
    column_count: int
    ossie_yaml_path: str
    similar_tables: list[dict[str, Any]] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)
    summary_markdown: str = ""
