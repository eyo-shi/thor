"""Ingestion Crew: S3 上のファイルを Iceberg テーブルとして取り込む。

公開 API:

  build_ingestion_crew(llm_light, llm_strong) -> Crew
  kickoff_ingestion(user_ctx, bucket, key, target_schema, ...) -> dict

Task 出力型 (Pydantic v2) は :mod:`thor.ingestion.models` に定義。
"""
from thor.ingestion.crew import build_ingestion_crew, kickoff_ingestion
from thor.ingestion.models import (
    ConflictAndPermissionsResult,
    CreateIcebergTableResult,
    DraftOssieResult,
    ExtractDataFrameResult,
    IngestionReport,
    LocateS3ObjectResult,
    ProposeSchemaAndNameResult,
    SniffFormatResult,
)

__all__ = [
    "build_ingestion_crew",
    "kickoff_ingestion",
    # models
    "LocateS3ObjectResult",
    "SniffFormatResult",
    "ExtractDataFrameResult",
    "ProposeSchemaAndNameResult",
    "ConflictAndPermissionsResult",
    "CreateIcebergTableResult",
    "DraftOssieResult",
    "IngestionReport",
]
