"""S3 上のファイルを DataFrame として読み、Ingestion Crew に渡す。

各フォーマットの読み込みは既存の Tool を組み合わせるだけの薄いラッパ:

  csv/tsv   -> pandas.read_csv (encoding + delimiter は CSVSniffer 結果を受け取る)
  xlsx/xls  -> pandas.read_excel (header は ExcelHeaderDetect 結果)
  parquet   -> pandas.read_parquet
  json      -> pandas.read_json (records / lines 両対応)

戻り値:
  columns: [{"name": "region", "sample_values": [...]}, ...]
  row_count_preview: プレビューに使ったサンプル行数
  full_row_count_hint: 分かる範囲での総行数 (Excel/Parquet のみ)
  preview_rows: 先頭 N 行 (dict のリスト)

サンプル値の詰め方は :class:`TypeInferTool` の ``columns`` 引数に直接渡せる形。
"""
from __future__ import annotations

import io
from typing import Any, Optional

from pydantic import BaseModel, Field

from thor.transport.errors import ErrorCode, err, ok
from thor.transport.logging import get_logger
from thor.transport.tool_base import BaseThorTool
from thor.transport.user_context import UserContext
from thor.tools._s3_client import map_s3_error, s3_client_for_user

_logger = get_logger(__name__)


class DataFramePreviewArgs(BaseModel):
    bucket: str
    key: str
    format: str = Field(
        ..., description="csv / tsv / xlsx / xls / parquet / json のいずれか"
    )
    max_rows: int = Field(200, ge=10, le=5000)
    # csv 用
    encoding: Optional[str] = Field(None, description="CSVSniffer 結果を渡す")
    delimiter: Optional[str] = Field(None, description="CSVSniffer 結果を渡す")
    # excel 用
    sheet: Optional[str] = None
    header_row: Optional[int] = Field(
        None, description="ExcelHeaderDetect 結果を渡す (0-indexed)"
    )
    # json 用
    json_lines: bool = Field(False, description="JSONL 形式なら True")


class DataFramePreviewTool(BaseThorTool):
    """S3 上のファイルを DataFrame として読み、プレビューを返す統一 Tool。

    最初の :attr:`max_rows` 行を読み、列ごとにサンプル値のリストを組み立てる。
    続けて :class:`TypeInferTool` に渡す想定。
    """

    name: str = "dataframe_preview"
    description: str = (
        "Read an S3-hosted file into a pandas DataFrame and return a small "
        "preview: column names, per-column sample values, first N rows. "
        "Format-specific hints (encoding, delimiter, sheet, header_row) should "
        "come from the FormatSniffer / ExcelHeaderDetect result."
    )
    args_schema: type[BaseModel] = DataFramePreviewArgs

    def run(
        self,
        user_ctx: Optional[UserContext],
        bucket: str,
        key: str,
        format: str,
        max_rows: int = 200,
        encoding: Optional[str] = None,
        delimiter: Optional[str] = None,
        sheet: Optional[str] = None,
        header_row: Optional[int] = None,
        json_lines: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        try:
            import pandas as pd  # type: ignore
        except ImportError:
            return err(ErrorCode.FORMAT_UNSUPPORTED, "pandas is not installed")

        client = s3_client_for_user(user_ctx)
        if isinstance(client, dict):
            return client
        try:
            resp = client.get_object(Bucket=bucket, Key=key)
            body: bytes = resp["Body"].read()
        except Exception as e:  # noqa: BLE001
            return map_s3_error(e, bucket, key)

        fmt = format.lower()
        try:
            df = _read_bytes_to_df(
                body,
                fmt=fmt,
                encoding=encoding,
                delimiter=delimiter,
                sheet=sheet,
                header_row=header_row,
                json_lines=json_lines,
                max_rows=max_rows,
                pd=pd,
            )
        except _ReadError as e:
            return err(e.code, e.message)
        except Exception as e:  # noqa: BLE001
            return err(ErrorCode.FORMAT_CORRUPT, f"{fmt} read failed: {e}")

        # サンプル値と preview を構築
        df_preview = df.head(max_rows)
        columns: list[dict[str, Any]] = []
        for col in df_preview.columns:
            series = df_preview[col]
            samples = [_jsonify(v) for v in series.head(50).tolist()]
            columns.append({"name": str(col), "sample_values": samples})
        preview_rows = [
            {str(k): _jsonify(v) for k, v in row.items()}
            for row in df_preview.to_dict(orient="records")
        ]
        # 全行数 (pandas が全量ロードした場合のみ確定)
        full_row_count = int(len(df))
        return ok(
            {
                "format": fmt,
                "columns": columns,
                "preview_rows": preview_rows,
                "row_count_preview": len(preview_rows),
                "full_row_count_hint": full_row_count,
            }
        )


# ------------------------------------------------------------------ #
# 内部
# ------------------------------------------------------------------ #

class _ReadError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message


def _read_bytes_to_df(
    body: bytes,
    *,
    fmt: str,
    encoding: Optional[str],
    delimiter: Optional[str],
    sheet: Optional[str],
    header_row: Optional[int],
    json_lines: bool,
    max_rows: int,
    pd: Any,
) -> Any:
    buf = io.BytesIO(body)
    if fmt in ("csv", "tsv"):
        # デフォルト
        sep = delimiter or ("\t" if fmt == "tsv" else ",")
        enc = encoding or "utf-8"
        return pd.read_csv(buf, sep=sep, encoding=enc, nrows=max_rows)
    if fmt in ("xlsx", "xls"):
        try:
            return pd.read_excel(
                buf,
                sheet_name=sheet,
                header=header_row if header_row is not None else 0,
                nrows=max_rows,
            )
        except Exception as e:  # noqa: BLE001
            raise _ReadError(ErrorCode.FORMAT_CORRUPT, f"excel read failed: {e}")
    if fmt == "parquet":
        try:
            df = pd.read_parquet(buf)
            return df.head(max_rows)
        except Exception as e:  # noqa: BLE001
            raise _ReadError(ErrorCode.FORMAT_CORRUPT, f"parquet read failed: {e}")
    if fmt == "json":
        try:
            df = pd.read_json(buf, lines=json_lines)
            return df.head(max_rows)
        except Exception as e:  # noqa: BLE001
            raise _ReadError(ErrorCode.FORMAT_CORRUPT, f"json read failed: {e}")
    raise _ReadError(
        ErrorCode.FORMAT_UNSUPPORTED, f"unsupported format for preview: {fmt}"
    )


def _jsonify(v: Any) -> Any:
    """pandas / numpy 由来のスカラを JSON 安全な型に落とす。"""
    if v is None:
        return None
    # numpy 型は np.integer / np.floating / np.bool_ を経由するが依存を増やしたくない
    try:
        import numpy as np  # type: ignore

        if isinstance(v, np.generic):
            return v.item()
    except ImportError:  # pragma: no cover
        pass
    # pandas.Timestamp / datetime
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:  # noqa: BLE001
            pass
    # 極端に長い文字列は切り詰め
    if isinstance(v, str) and len(v) > 500:
        return v[:497] + "..."
    return v
