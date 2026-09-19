"""Ingestion Crew の Task 定義 (Sequential)。

各 Task は前タスクの出力を ``context=[prev_task]`` で受け取り、
:class:`pydantic.BaseModel` を ``output_json`` に指定して LLM が JSON を返す
ことを強制する。副作用のあるタスク (CREATE, Git commit) は
``max_retries=0``、LLM 依存タスクは 1 に設定。

タスク順序:

  1. locate_s3_object_task
  2. sniff_format_task              (未対応形式で早期終了)
  3. extract_dataframe_task
  4. propose_schema_and_name_task
  5. check_conflict_and_permissions_task  (guardrail)
  6. create_iceberg_table_task
  7. draft_ossie_task
  8. wrap_up_task
"""
from __future__ import annotations

from typing import Any, Optional

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

try:
    from crewai import Task  # type: ignore
except Exception:  # pragma: no cover
    class Task:  # type: ignore[no-redef]
        """crewai.Task のスタブ。属性を保持するだけ。"""

        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)


# ------------------------------------------------------------------ #
# 1. locate_s3_object_task
# ------------------------------------------------------------------ #
def make_locate_s3_object_task(agent: Any) -> Task:
    return Task(
        description=(
            "ユーザー指示の S3 パス (bucket={bucket}, key={key}) にオブジェクトが"
            "実在するか確認し、size / content_type / last_modified を構造化"
            "して返せ。存在しなければ error_code=S3_NOT_FOUND、権限エラーは"
            "S3_ACCESS_DENIED として exists=false で返す。"
        ),
        expected_output=(
            "LocateS3ObjectResult の JSON。bucket, key, size, content_type, "
            "last_modified, exists を含む。"
        ),
        agent=agent,
        output_json=LocateS3ObjectResult,
        max_retries=1,
    )


# ------------------------------------------------------------------ #
# 2. sniff_format_task
# ------------------------------------------------------------------ #
def make_sniff_format_task(agent: Any, context: list[Task]) -> Task:
    return Task(
        description=(
            "前段で確定した S3 オブジェクトのフォーマットを判定せよ。"
            "手順: (a) S3GetRangeTool で先頭 1MB を取り、MagicByteTool で"
            " format を確定。 (b) csv/tsv なら CSVSnifferTool で encoding と"
            " delimiter を確定。 (c) xlsx/xls なら ExcelHeaderDetectTool で"
            " header_row と sheet を確定。 (d) parquet なら ParquetMetaTool"
            " でスキーマを確認。判定不能なら supported=false + reason を返し、"
            "以降のタスクは実行しない。"
        ),
        expected_output=(
            "SniffFormatResult の JSON。format, encoding, delimiter, sheet, "
            "header_row, supported, reason を含む。"
        ),
        agent=agent,
        context=context,
        output_json=SniffFormatResult,
        max_retries=1,
    )


# ------------------------------------------------------------------ #
# 3. extract_dataframe_task
# ------------------------------------------------------------------ #
def make_extract_dataframe_task(agent: Any, context: list[Task]) -> Task:
    return Task(
        description=(
            "DataFramePreviewTool を使って先頭 200 行を DataFrame として読み"
            "込み、列ごとにサンプル値 50 個と preview_rows を返せ。"
            "SniffFormat の encoding / delimiter / sheet / header_row を"
            "そのまま渡すこと。列名の空白は除去し、NaN は None に置換する。"
        ),
        expected_output=(
            "ExtractDataFrameResult の JSON。columns[{name, sample_values}], "
            "preview_rows, row_count_preview, full_row_count_hint を含む。"
        ),
        agent=agent,
        context=context,
        output_json=ExtractDataFrameResult,
        max_retries=1,
    )


# ------------------------------------------------------------------ #
# 4. propose_schema_and_name_task
# ------------------------------------------------------------------ #
def make_propose_schema_and_name_task(agent: Any, context: list[Task]) -> Task:
    return Task(
        description=(
            "サンプル値から各カラムの Trino 型を TypeInferTool で推定し、"
            "NameProposerTool で英小文字 + アンダースコアのテーブル名候補を"
            "作れ。target_schema (=Trino スキーマ) はユーザー指定の"
            " {target_schema} を使う。SimilarTableSearchTool で命名衝突と"
            "類似データセットを検索し、exact_conflicts と similar_tables に"
            "積め。role (dimension/measure/time) は数値集計系を measure、"
            "date/timestamp を time、それ以外を dimension として分類する。"
        ),
        expected_output=(
            "ProposeSchemaAndNameResult の JSON。catalog, target_schema, "
            "proposed_table_name, columns[{name, trino_type, nullable, role}],"
            " partitioning, similar_tables, exact_conflicts を含む。"
        ),
        agent=agent,
        context=context,
        output_json=ProposeSchemaAndNameResult,
        max_retries=1,
    )


# ------------------------------------------------------------------ #
# 5. check_conflict_and_permissions_task  (guardrail)
# ------------------------------------------------------------------ #
def make_check_conflict_and_permissions_task(agent: Any, context: list[Task]) -> Task:
    return Task(
        description=(
            "propose_schema_and_name の結果を受け、TableExistsTool で最終的な"
            "衝突チェックと、TrinoMetaTool で CREATE 権限確認を行え。"
            "衝突がある (かつ overwrite=false) 場合は has_conflict=true と"
            "error_code=SCHEMA_NAME_CONFLICT を、CREATE 不可なら"
            " has_create_priv=false と error_code=PERM_CREATE_DENIED を返せ。"
            "どちらも問題ないときのみ後続タスクに進める (Crew.ai の guardrail)。"
        ),
        expected_output=(
            "ConflictAndPermissionsResult の JSON。has_conflict, "
            "has_create_priv, resolved_table, error_code, message を含む。"
        ),
        agent=agent,
        context=context,
        output_json=ConflictAndPermissionsResult,
        max_retries=0,  # 副作用なしだが再試行しても状態は変わらない
    )


# ------------------------------------------------------------------ #
# 6. create_iceberg_table_task  (副作用あり: max_retries=0)
# ------------------------------------------------------------------ #
def make_create_iceberg_table_task(agent: Any, context: list[Task]) -> Task:
    return Task(
        description=(
            "check_conflict_and_permissions が通っている前提で、"
            "IcebergCreateTableTool に catalog / target_schema / "
            "resolved_table / columns / partitioning を渡し、Iceberg "
            "テーブルを作成せよ。DDL は Tool 内で組み立てられるので、"
            "Agent が手で SQL を書いてはいけない。副作用ありのため"
            " max_retries=0。エラーはそのまま返す (再試行しない)。"
        ),
        expected_output=(
            "CreateIcebergTableResult の JSON。fq_table_name, ddl, column_count"
            " を含む。"
        ),
        agent=agent,
        context=context,
        output_json=CreateIcebergTableResult,
        max_retries=0,
    )


# ------------------------------------------------------------------ #
# 7. draft_ossie_task
# ------------------------------------------------------------------ #
def make_draft_ossie_task(agent: Any, context: list[Task]) -> Task:
    return Task(
        description=(
            "作成したテーブルのメタデータから Ossie dataset を組み立て、"
            "OssieWriteTool で semantic/datasets/<catalog>/<schema>/<table>.yaml"
            " に書き出せ (commit=true)。dimensions / measures の判別は"
            " propose_schema_and_name の role をそのまま利用。"
            " sample_queries は 2-3 件、日本語の自然言語質問と対応する Trino"
            " SQL を提示。副作用ありのため max_retries=0。"
        ),
        expected_output=(
            "DraftOssieResult の JSON。fq_name, yaml_path, git_status, "
            "commit_sha を含む。"
        ),
        agent=agent,
        context=context,
        output_json=DraftOssieResult,
        max_retries=0,
    )


# ------------------------------------------------------------------ #
# 8. wrap_up_task
# ------------------------------------------------------------------ #
def make_wrap_up_task(agent: Any, context: list[Task]) -> Task:
    return Task(
        description=(
            "これまでのタスク結果を統合し、ユーザーへの最終レポートを"
            "IngestionReport JSON として返せ。summary_markdown には作成した"
            " fq_table_name、行数目安、カラム数、類似テーブル、Ossie YAML の"
            "パスを日本語で 5-8 行にまとめる。"
        ),
        expected_output=(
            "IngestionReport の JSON。fq_table_name, ddl, column_count, "
            "ossie_yaml_path, similar_tables, source, summary_markdown を含む。"
        ),
        agent=agent,
        context=context,
        output_json=IngestionReport,
        max_retries=1,
    )


# ------------------------------------------------------------------ #
# guardrail helper
# ------------------------------------------------------------------ #
def conflict_permissions_guardrail(
    output: Any,
) -> tuple[bool, Optional[str]]:
    """`check_conflict_and_permissions_task` の結果を検査するガードレール。

    Crew.ai の Task には ``guardrail`` パラメータがあり、``(ok, feedback)``
    を返す関数を受け取る。ok=False で Crew は次タスクに進まず停止する。
    """
    # output は Pydantic モデル or dict のどちらでも扱えるように
    if isinstance(output, ConflictAndPermissionsResult):
        result = output
    elif isinstance(output, dict):
        try:
            result = ConflictAndPermissionsResult.model_validate(output)
        except Exception:  # noqa: BLE001
            return False, f"guardrail: could not parse output: {output!r}"
    else:
        return False, f"guardrail: unexpected output type: {type(output).__name__}"

    if result.has_conflict:
        return False, (
            f"Table name conflict for {result.resolved_table!r}: "
            f"{result.message or 'already exists'}. Ask the user for another name."
        )
    if not result.has_create_priv:
        return False, (
            f"CREATE permission denied on {result.resolved_table!r}: "
            f"{result.message or 'contact the workspace admin'}."
        )
    return True, None


__all__ = [
    "make_locate_s3_object_task",
    "make_sniff_format_task",
    "make_extract_dataframe_task",
    "make_propose_schema_and_name_task",
    "make_check_conflict_and_permissions_task",
    "make_create_iceberg_table_task",
    "make_draft_ossie_task",
    "make_wrap_up_task",
    "conflict_permissions_guardrail",
]
