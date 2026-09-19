"""S3 ブラウザ API (`/api/files/*`)。

TreePane の左ペイン `s3` ルートと ResultPane の File タブに対応。
IDBroker で発行されたエンドユーザー STS 資格情報で S3 を叩く。

エンドポイント:

* ``GET /api/files/list``     — 指定 prefix 下の一覧 (フォルダ + オブジェクト)
* ``GET /api/files/preview``  — フォーマット判定 + 中身プレビュー
  (CSV/TSV/JSON/JSONL/Excel/Parquet 対応)

プレビューは Range-GET で先頭 :data:`_PREVIEW_RANGE_BYTES` (2 MB) のみ取得し、
:mod:`thor.tools.format` / :mod:`thor.tools.excel` の判定ロジックを共用する
(``FormatSnifferAgent`` と同じ Tool を UI からも呼べるように)。
"""
from __future__ import annotations

import base64
import io
import json
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from thor.api.auth import require_user_context
from thor.transport.errors import ErrorCode, err
from thor.transport.user_context import UserContext
from thor.tools._s3_client import map_s3_error, s3_client_for_user
from thor.tools.excel import ExcelHeaderDetectTool
from thor.tools.format import CSVSnifferTool, MagicByteTool, ParquetMetaTool

router = APIRouter(prefix="/api/files", tags=["files"])

# Range-GET のバイト数。マジックバイト判定 + CSV/JSON プレビューはここに収まる。
# xlsx / parquet は別途 GetObject で全体を読む (:class:`ExcelHeaderDetectTool`
# / :class:`ParquetMetaTool` の要件)。
_PREVIEW_RANGE_BYTES = 2 * 1024 * 1024  # 2 MB


# ------------------------------------------------------------------ #
# /api/files/list
# ------------------------------------------------------------------ #
@router.get("/list")
def list_objects(
    user_ctx: Annotated[UserContext, Depends(require_user_context)],
    bucket: Annotated[str, Query(min_length=1, max_length=256)],
    prefix: Annotated[str, Query(max_length=1024)] = "",
    delimiter: Annotated[str, Query(max_length=4)] = "/",
    max_keys: Annotated[int, Query(ge=1, le=10000)] = 1000,
) -> dict[str, Any]:
    client = s3_client_for_user(user_ctx)
    if isinstance(client, dict):
        raise HTTPException(status_code=502, detail=client)
    try:
        resp = client.list_objects_v2(
            Bucket=bucket,
            Prefix=prefix,
            Delimiter=delimiter,
            MaxKeys=max_keys,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=map_s3_error(e, bucket, prefix)) from e

    objects = [
        {
            "key": o["Key"],
            "size": o["Size"],
            "last_modified": o["LastModified"].isoformat()
            if hasattr(o.get("LastModified"), "isoformat")
            else str(o.get("LastModified", "")),
        }
        for o in resp.get("Contents", [])
    ]
    subfolders = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
    return {
        "bucket": bucket,
        "prefix": prefix,
        "delimiter": delimiter,
        "objects": objects,
        "subfolders": subfolders,
        "is_truncated": bool(resp.get("IsTruncated")),
    }


# ------------------------------------------------------------------ #
# /api/files/preview
# ------------------------------------------------------------------ #
@router.get("/preview")
def preview_object(
    user_ctx: Annotated[UserContext, Depends(require_user_context)],
    bucket: Annotated[str, Query(min_length=1, max_length=256)],
    key: Annotated[str, Query(min_length=1, max_length=2048)],
    sheet: Annotated[Optional[str], Query(max_length=256)] = None,
    rows: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    """S3 上のファイルをフォーマット別にプレビューする。

    * まず Range-GET で先頭 2 MB を取得し、:class:`MagicByteTool` で
      フォーマットを判定する。
    * CSV/TSV: バッファ内で :class:`CSVSnifferTool` を走らせて encoding /
      delimiter / rows を返す。
    * JSON / JSONL: 先頭 2 MB を parse (JSONL は行単位)。
    * xlsx / xls: 全量取得が必要なので :class:`ExcelHeaderDetectTool` に
      委譲する (シート単位に header_row / meta_kv / sample_rows を返す)。
    * Parquet: :class:`ParquetMetaTool` でスキーマを取り、少量のプレビュー
      行は簡略に返す (フル DataFrame preview は Ingestion Crew 側で行う)。
    * それ以外: ``format`` と ``note`` だけ返す (未対応形式)。

    Query parameters
    ----------------
    bucket / key : S3 バケット + オブジェクトキー。
    sheet        : Excel のみ有効。省略時は全シート走査 (data range 最大が primary)。
    rows         : プレビュー行数の上限 (1..500, default 100)。
    """
    client = s3_client_for_user(user_ctx)
    if isinstance(client, dict):
        raise HTTPException(status_code=502, detail=client)

    # 1) Range-GET で先頭 2 MB を取得
    range_header = f"bytes=0-{_PREVIEW_RANGE_BYTES - 1}"
    try:
        resp = client.get_object(Bucket=bucket, Key=key, Range=range_header)
        head_bytes: bytes = resp["Body"].read()
        content_type = resp.get("ContentType", "application/octet-stream")
        content_length = int(resp.get("ContentLength", len(head_bytes)))
        # Content-Range: "bytes 0-2097151/12345678"
        total_size = _parse_total_from_content_range(
            resp.get("ContentRange", ""), fallback=content_length
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=map_s3_error(e, bucket, key)
        ) from e

    # 2) フォーマット判定
    magic = MagicByteTool()
    magic_result = magic.run(
        user_ctx=None,
        content_b64=base64.b64encode(head_bytes).decode("ascii"),
        filename_hint=key.rsplit("/", 1)[-1],
    )
    if magic_result.get("status") != "ok":
        raise HTTPException(status_code=400, detail=magic_result)

    fmt = magic_result.get("format", "unknown")
    base: dict[str, Any] = {
        "bucket": bucket,
        "key": key,
        "size": total_size,
        "content_type": content_type,
        "format": fmt,
        "truncated": total_size > len(head_bytes),
    }

    # 3) フォーマット別のプレビュー
    if fmt in ("csv", "tsv"):
        result = _preview_csv(head_bytes, rows=rows, magic_hint=magic_result)
    elif fmt == "json":
        result = _preview_json(head_bytes, rows=rows)
    elif fmt in ("xlsx", "xls"):
        result = _preview_excel(user_ctx, bucket, key, sheet=sheet, rows=rows)
    elif fmt == "parquet":
        result = _preview_parquet(user_ctx, bucket, key, rows=rows)
    else:
        result = {
            "note": f"preview is not supported for format={fmt}",
            "detection_method": magic_result.get("method"),
        }

    # 3-a) 内部 tool がエラー dict を返した場合は 400/502 に写像
    if isinstance(result, dict) and result.get("status") == "error":
        raise HTTPException(
            status_code=_http_status_for_error(result), detail=result
        )

    base.update(result if isinstance(result, dict) else {})
    return base


# ------------------------------------------------------------------ #
# 内部ヘルパ: フォーマット別プレビュー
# ------------------------------------------------------------------ #
def _preview_csv(
    head_bytes: bytes, *, rows: int, magic_hint: dict[str, Any]
) -> dict[str, Any]:
    """CSV/TSV: :class:`CSVSnifferTool` を再利用し、rows でトリム。

    Magic byte 側の ``delimiter_hint`` が有れば Sniffer が失敗しても救済。
    """
    tool = CSVSnifferTool()
    sniff = tool.run(
        user_ctx=None,
        content_b64=base64.b64encode(head_bytes).decode("ascii"),
        max_sample_bytes=min(len(head_bytes), 1_048_576),
    )
    if sniff.get("status") != "ok":
        # Sniffer が失敗しても、magic_hint に delimiter があれば fallback で読む
        hint_delim = magic_hint.get("delimiter_hint")
        if not hint_delim:
            return sniff
        return _preview_csv_fallback(head_bytes, delimiter=hint_delim, rows=rows)

    preview_rows = sniff.get("preview_rows", [])[: rows + 1]  # ヘッダ込みで +1
    header: list[str] = []
    data_rows: list[list[Any]] = preview_rows
    if sniff.get("has_header") and preview_rows:
        header = [str(v) for v in preview_rows[0]]
        data_rows = preview_rows[1 : rows + 1]
    else:
        data_rows = preview_rows[:rows]
    return {
        "encoding": sniff.get("encoding"),
        "delimiter": sniff.get("delimiter"),
        "quotechar": sniff.get("quotechar"),
        "has_header": bool(sniff.get("has_header")),
        "header": header,
        "rows": data_rows,
        "row_count": len(data_rows),
    }


def _preview_csv_fallback(
    head_bytes: bytes, *, delimiter: str, rows: int
) -> dict[str, Any]:
    """csv.Sniffer が失敗したときの最小 fallback。encoding は utf-8 前提。"""
    import csv

    try:
        text = head_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return err(
            ErrorCode.FORMAT_ENCODING_UNKNOWN,
            "fallback preview requires UTF-8 text",
        )
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    all_rows = list(reader)[: rows + 1]
    header = [str(v) for v in all_rows[0]] if all_rows else []
    return {
        "encoding": "utf-8",
        "delimiter": delimiter,
        "quotechar": '"',
        "has_header": True,
        "header": header,
        "rows": all_rows[1 : rows + 1],
        "row_count": max(0, len(all_rows) - 1),
    }


def _preview_json(head_bytes: bytes, *, rows: int) -> dict[str, Any]:
    """JSON / JSONL のプレビュー。

    最初に JSON 全体としてパースを試み、それが失敗したら 1 行 1 JSON
    (JSONL) として先頭 :data:`rows` 行を読む。
    """
    encoding = "utf-8"
    text = _decode_lenient(head_bytes)
    if text is None:
        return err(ErrorCode.FORMAT_ENCODING_UNKNOWN, "could not decode as UTF-8/CP932")

    stripped = text.lstrip()
    # 通常の JSON (単一値または配列)
    try:
        value = json.loads(stripped)
        return {
            "encoding": encoding,
            "mode": "json",
            "value": _truncate_json(value, max_list=rows),
        }
    except json.JSONDecodeError:
        pass

    # JSONL
    lines: list[Any] = []
    parse_errors = 0
    for i, line in enumerate(text.splitlines()):
        if i >= rows:
            break
        line = line.strip()
        if not line:
            continue
        try:
            lines.append(json.loads(line))
        except json.JSONDecodeError:
            parse_errors += 1
            if parse_errors > 5 and not lines:
                return err(
                    ErrorCode.FORMAT_CORRUPT,
                    "content is neither valid JSON nor JSONL",
                )
    return {
        "encoding": encoding,
        "mode": "jsonl",
        "rows": lines,
        "row_count": len(lines),
        "parse_errors": parse_errors,
    }


def _preview_excel(
    user_ctx: UserContext,
    bucket: str,
    key: str,
    *,
    sheet: Optional[str],
    rows: int,
) -> dict[str, Any]:
    """Excel: :class:`ExcelHeaderDetectTool` に委譲。"""
    tool = ExcelHeaderDetectTool()
    result = tool.run(user_ctx=user_ctx, bucket=bucket, key=key, sheet=sheet)
    if result.get("status") != "ok":
        return result
    sheets_out: list[dict[str, Any]] = []
    for s in result.get("sheets", []):
        sample_rows = s.get("sample_rows", [])
        sheets_out.append(
            {
                "sheet": s.get("sheet"),
                "header_row": s.get("header_row"),
                "columns": s.get("columns", []),
                "meta_kv": s.get("meta_kv", []),
                "data_row_count": s.get("data_row_count", 0),
                "rows": sample_rows[:rows],
            }
        )
    return {
        "primary_sheet": result.get("primary_sheet"),
        "sheets": sheets_out,
    }


def _preview_parquet(
    user_ctx: UserContext, bucket: str, key: str, *, rows: int
) -> dict[str, Any]:
    """Parquet: :class:`ParquetMetaTool` でスキーマ + 少量サンプル。

    サンプル行取得は pyarrow が使える環境でのみ。エラーは meta 側で吸収。
    """
    meta_tool = ParquetMetaTool()
    meta = meta_tool.run(user_ctx=user_ctx, bucket=bucket, key=key)
    if meta.get("status") != "ok":
        return meta

    schema = meta.get("columns", [])
    num_rows = meta.get("num_rows", 0)
    sample_rows: list[dict[str, Any]] = []

    try:
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        # スキーマだけ返す
        return {
            "schema": schema,
            "num_rows": num_rows,
            "num_row_groups": meta.get("num_row_groups", 0),
            "rows": [],
            "row_count": 0,
            "note": "pyarrow not available for row preview; schema only",
        }

    # サンプル取得は S3 GetObject を再度実行 (メタ tool の内部で body を捨てているため)
    client = s3_client_for_user(user_ctx)
    if isinstance(client, dict):
        return client
    try:
        resp = client.get_object(Bucket=bucket, Key=key)
        body: bytes = resp["Body"].read()
        reader = pq.ParquetFile(io.BytesIO(body))
        # 先頭 row_group を最小限読む (rows 件で切る)
        # read_row_group は Table を返す
        limit = min(rows, num_rows) if num_rows else rows
        if reader.metadata.num_row_groups > 0 and limit > 0:
            table = reader.read_row_group(0).slice(0, limit)
            sample_rows = table.to_pylist()
    except Exception as e:  # noqa: BLE001
        return {
            "schema": schema,
            "num_rows": num_rows,
            "num_row_groups": meta.get("num_row_groups", 0),
            "rows": [],
            "row_count": 0,
            "note": f"row preview failed: {e}",
        }

    return {
        "schema": schema,
        "num_rows": num_rows,
        "num_row_groups": meta.get("num_row_groups", 0),
        "rows": sample_rows,
        "row_count": len(sample_rows),
    }


# ------------------------------------------------------------------ #
# 汎用ヘルパ
# ------------------------------------------------------------------ #
def _parse_total_from_content_range(header: str, *, fallback: int) -> int:
    """"bytes 0-2097151/12345678" の末尾整数を返す。* なら fallback。"""
    if not header:
        return fallback
    # "bytes X-Y/Z"
    try:
        _, _, total = header.rpartition("/")
        return int(total)
    except (ValueError, TypeError):
        return fallback


def _decode_lenient(raw: bytes) -> Optional[str]:
    """UTF-8 → CP932 の順で decode を試す。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for enc in ("utf-8", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def _truncate_json(value: Any, *, max_list: int) -> Any:
    """JSON 値のうち巨大配列を先頭 max_list に切る (再帰)。"""
    if isinstance(value, list):
        return [_truncate_json(v, max_list=max_list) for v in value[:max_list]]
    if isinstance(value, dict):
        return {k: _truncate_json(v, max_list=max_list) for k, v in value.items()}
    return value


def _http_status_for_error(result: dict[str, Any]) -> int:
    """Tool の error dict を HTTP ステータスに写像する。"""
    code = result.get("error_code", "")
    if code in ("S3_NOT_FOUND",):
        return 404
    if code in ("S3_ACCESS_DENIED",):
        return 403
    if code in (
        "FORMAT_UNSUPPORTED",
        "FORMAT_CORRUPT",
        "FORMAT_EXCEL_HEADER_UNDETECTED",
        "FORMAT_ENCODING_UNKNOWN",
    ):
        return 400
    return 502
