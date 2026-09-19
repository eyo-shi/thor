"""Excel ヘッダー検出 Tool。

「上部にメタ K/V → 空行 → 表ヘッダー → データ行」パターンに対応する
ヒューリスティック検出。プラン記載の 2 段構え (heuristic → LLM 検証) のうち、
ここでは **heuristic** のみ実装。LLM 検証は Ingestion Crew の後続タスクで
呼び出す想定 (task #10)。

主要メトリクス:
  * 行ごとの非空セル数
  * 型均一性: 直下 5 行の型の一致率 (数値優勢 or 文字列優勢)
  * セル長中央値
"""
from __future__ import annotations

import io
import statistics
from typing import Any, Optional

from pydantic import BaseModel, Field

from thor.transport.errors import ErrorCode, err, ok
from thor.transport.logging import get_logger
from thor.transport.tool_base import BaseThorTool
from thor.transport.user_context import UserContext
from thor.tools._s3_client import map_s3_error, s3_client_for_user

_logger = get_logger(__name__)

_MAX_SCAN_ROWS = 50
_MAX_COLS = 200


class ExcelHeaderDetectArgs(BaseModel):
    bucket: str = Field(...)
    key: str = Field(...)
    sheet: Optional[str] = Field(
        None, description="対象シート名 (省略時は全シート走査、data range 最大を主とする)"
    )


class ExcelHeaderDetectTool(BaseThorTool):
    """xlsx を読み、シートごとにヘッダー行と meta K/V を推定する。

    heuristic 段のみ。判別に自信がない場合は
    :attr:`ErrorCode.FORMAT_EXCEL_HEADER_UNDETECTED` を返し、
    ユーザーに ``header_row`` を聞き返す。
    """

    name: str = "excel_header_detect"
    description: str = (
        "For an S3-hosted xlsx, detect the header row and meta key/value area "
        "for each sheet using a heuristic. The primary sheet is the one with "
        "the largest data range."
    )
    args_schema: type[BaseModel] = ExcelHeaderDetectArgs

    def run(
        self,
        user_ctx: Optional[UserContext],
        bucket: str,
        key: str,
        sheet: Optional[str] = None,
        **_: Any,
    ) -> dict[str, Any]:
        try:
            import openpyxl  # type: ignore
        except ImportError:
            return err(ErrorCode.FORMAT_UNSUPPORTED, "openpyxl is not installed")
        client = s3_client_for_user(user_ctx)
        if isinstance(client, dict):
            return client
        try:
            resp = client.get_object(Bucket=bucket, Key=key)
            body: bytes = resp["Body"].read()
        except Exception as e:  # noqa: BLE001
            return map_s3_error(e, bucket, key)
        try:
            wb = openpyxl.load_workbook(
                io.BytesIO(body), read_only=True, data_only=True
            )
        except Exception as e:  # noqa: BLE001
            return err(ErrorCode.FORMAT_CORRUPT, f"xlsx open failed: {e}")

        target_sheets = [sheet] if sheet else wb.sheetnames
        results = []
        for sname in target_sheets:
            if sname not in wb.sheetnames:
                continue
            ws = wb[sname]
            analysis = _analyze_sheet(ws)
            if analysis is not None:
                analysis["sheet"] = sname
                results.append(analysis)

        if not results:
            return err(
                ErrorCode.FORMAT_EXCEL_HEADER_UNDETECTED,
                "no analyzable sheet found in workbook",
            )

        # 主シート = data range が最大のシート
        primary = max(results, key=lambda r: r.get("data_row_count", 0))
        return ok(
            {
                "primary_sheet": primary["sheet"],
                "sheets": results,
            }
        )


# ------------------------------------------------------------------ #
# 内部: 1 シートの解析
# ------------------------------------------------------------------ #

def _analyze_sheet(ws: Any) -> Optional[dict[str, Any]]:
    """先頭 :data:`_MAX_SCAN_ROWS` 行を走査してヘッダー候補を決める。"""
    scanned: list[list[Any]] = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i >= _MAX_SCAN_ROWS:
            break
        scanned.append(list(row[:_MAX_COLS]))
    if not scanned:
        return None

    row_metrics = [_row_metrics(r) for r in scanned]
    header_idx = _find_header_row(row_metrics)
    if header_idx is None:
        return {
            "header_row": None,
            "data_row_count": 0,
            "meta_kv": _extract_meta_kv(scanned, upper_bound=len(scanned)),
            "columns": [],
            "note": "header row could not be located by heuristic",
        }

    header_values = [
        _stringify(c) for c in scanned[header_idx][: row_metrics[header_idx]["last_nonempty"] + 1]
    ]
    meta_kv = _extract_meta_kv(scanned, upper_bound=header_idx)
    data_rows = scanned[header_idx + 1 :]
    data_row_count = sum(1 for r in data_rows if any(v not in (None, "") for v in r))
    return {
        "header_row": header_idx,  # 0-indexed
        "columns": header_values,
        "meta_kv": meta_kv,
        "data_row_count": data_row_count,
        "sample_rows": [list(r) for r in data_rows[:10]],
    }


def _row_metrics(row: list[Any]) -> dict[str, Any]:
    non_empty = [c for c in row if c not in (None, "")]
    numeric = sum(1 for c in non_empty if isinstance(c, (int, float)))
    strings = sum(1 for c in non_empty if isinstance(c, str))
    lengths = [len(str(c)) for c in non_empty]
    last_nonempty = -1
    for i, c in enumerate(row):
        if c not in (None, ""):
            last_nonempty = i
    return {
        "non_empty": len(non_empty),
        "numeric_ratio": numeric / len(non_empty) if non_empty else 0.0,
        "string_ratio": strings / len(non_empty) if non_empty else 0.0,
        "median_len": statistics.median(lengths) if lengths else 0,
        "last_nonempty": last_nonempty,
    }


def _find_header_row(metrics: list[dict[str, Any]]) -> Optional[int]:
    """非空セル数が急増し、直下 5 行の型が均一な最初の行を返す。"""
    n = len(metrics)
    for i, m in enumerate(metrics):
        if m["non_empty"] < 2:
            continue
        # 上と比べて非空が有意に増加している (>= 2 倍 or +3 以上)
        prev_ne = metrics[i - 1]["non_empty"] if i > 0 else 0
        if not (m["non_empty"] >= max(prev_ne * 2, prev_ne + 3, 3)):
            continue
        # ヘッダー行自体は文字列優勢が望ましい
        if m["string_ratio"] < 0.5:
            continue
        # 直下 5 行が型均一 (数値優勢が続く or 文字列優勢が続く) か
        window = metrics[i + 1 : i + 6]
        if not window:
            continue
        avg_numeric = sum(w["numeric_ratio"] for w in window) / len(window)
        avg_string = sum(w["string_ratio"] for w in window) / len(window)
        if max(avg_numeric, avg_string) >= 0.5:
            return i
    # fallback: 最初の非空セル >= 2 の行
    for i, m in enumerate(metrics):
        if m["non_empty"] >= 2 and m["string_ratio"] >= 0.5:
            return i
    return None


def _extract_meta_kv(scanned: list[list[Any]], upper_bound: int) -> list[dict[str, Any]]:
    """ヘッダー行より上を ``key: value`` 抽出する。

    - 列 A / 列 B の 2 セルペアを K/V とする
    - どちらかが空ならスキップ
    """
    meta = []
    for r in scanned[:upper_bound]:
        if len(r) >= 2 and r[0] not in (None, "") and r[1] not in (None, ""):
            meta.append({"key": _stringify(r[0]), "value": _stringify(r[1])})
    return meta


def _stringify(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)
