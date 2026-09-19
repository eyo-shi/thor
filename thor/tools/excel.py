"""Excel ヘッダー検出 Tool。

「上部にメタ K/V → 空行 → 表ヘッダー → データ行」パターンに対応する。
プラン記載の 2 段構え:

  * **Step 1 (heuristic)**: :class:`ExcelHeaderDetectTool` — 非空セル数の急増と
    直下 5 行の型均一性で候補行を選ぶ。
  * **Step 2 (LLM 検証)**: :class:`ExcelHeaderValidateTool` — heuristic の候補
    行と周辺 10 行を LLM に見せ、妥当性を JSON で判定させる。LLM が使えない
    (未インストール / 環境変数不足) 場合は heuristic を素通しする。

Heuristic の主要メトリクス:
  * 行ごとの非空セル数
  * 型均一性: 直下 5 行の型の一致率 (数値優勢 or 文字列優勢)
  * セル長中央値
"""
from __future__ import annotations

import io
import json
import statistics
from typing import Any, Optional

from pydantic import BaseModel, Field

from thor.transport.errors import ErrorCode, err, ok
from thor.transport.llm import try_json_completion
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


# ------------------------------------------------------------------ #
# ExcelHeaderValidateTool (Step 2: LLM 検証段)
# ------------------------------------------------------------------ #
class _SheetCandidate(BaseModel):
    """1 シートあたりの LLM 検証入力。"""

    sheet: str
    header_row: Optional[int] = Field(
        None, description="heuristic が推定した 0-indexed ヘッダー行。None なら未検出。"
    )
    columns: list[str] = Field(default_factory=list)
    meta_kv: list[dict[str, Any]] = Field(default_factory=list)
    surrounding_rows: list[list[Any]] = Field(
        default_factory=list,
        description="candidate 前後 10 行 (合計 21 行以内)。表構造の妥当性判定用。",
    )


class ExcelHeaderValidateArgs(BaseModel):
    candidates: list[_SheetCandidate] = Field(
        ...,
        description=(
            ":class:`ExcelHeaderDetectTool` の出力から作った 1 シートあたりの "
            "候補一覧 (surrounding_rows を付与したもの)。"
        ),
    )
    model: Optional[str] = Field(
        None,
        description="明示モデル ID。未指定なら THOR_LLM_ROUTER_MODEL を使う。",
    )


class ExcelHeaderValidateTool(BaseThorTool):
    """Heuristic が推定した Excel ヘッダー行を LLM に妥当性判定させる。

    LLM が使えない環境 (litellm 未インストール / CAI_INFERENCE_* 未設定 /
    JSON パース失敗) では **heuristic を素通し** で承認する
    (``is_valid=True, confidence="low", source="fallback"``)。デモ環境や
    テストで LLM 依存を持ち込まないための設計。

    LLM への要求は 1 シート 1 プロンプト (バッチしない — トークン膨張の防止)。
    """

    name: str = "excel_header_validate"
    description: str = (
        "For each sheet candidate (heuristic-detected header row + surrounding "
        "10 rows), ask an LLM whether the row is truly a table header. Falls "
        "back to accepting the heuristic when no LLM is configured."
    )
    args_schema: type[BaseModel] = ExcelHeaderValidateArgs
    requires_auth: bool = False  # LLM 呼び出しは api_key 環境変数で完結

    def run(
        self,
        user_ctx: Optional[UserContext],
        candidates: list[dict[str, Any]] | list[_SheetCandidate],
        model: Optional[str] = None,
        **_: Any,
    ) -> dict[str, Any]:
        # crewai が dict のまま渡してくる可能性を許容
        parsed: list[_SheetCandidate] = []
        for c in candidates:
            if isinstance(c, _SheetCandidate):
                parsed.append(c)
            elif isinstance(c, dict):
                try:
                    parsed.append(_SheetCandidate(**c))
                except Exception as e:  # noqa: BLE001 - pydantic の失敗を包む
                    return err(
                        ErrorCode.FORMAT_EXCEL_HEADER_UNDETECTED,
                        f"invalid candidate: {e}",
                    )
            else:
                return err(
                    ErrorCode.FORMAT_EXCEL_HEADER_UNDETECTED,
                    f"unsupported candidate type: {type(c).__name__}",
                )

        validations: list[dict[str, Any]] = []
        for cand in parsed:
            validations.append(_validate_one(cand, model=model))
        return ok({"validations": validations})


def _validate_one(
    cand: _SheetCandidate, *, model: Optional[str]
) -> dict[str, Any]:
    """1 シート分の妥当性判定。LLM 使用可否は :func:`try_json_completion` に委ねる。"""
    base: dict[str, Any] = {
        "sheet": cand.sheet,
        "candidate_header_row": cand.header_row,
        "columns": cand.columns,
    }

    if cand.header_row is None:
        # heuristic 自体が未検出。LLM に頼らず素直にエラー扱い。
        return {
            **base,
            "is_valid": False,
            "confidence": "low",
            "source": "heuristic",
            "reason": "heuristic could not locate a header row",
        }

    prompt = _build_validation_prompt(cand)
    llm_result = try_json_completion(prompt, model=model, max_tokens=256)
    if llm_result is None:
        # LLM 使えない環境 → heuristic を素通し
        return {
            **base,
            "is_valid": True,
            "confidence": "low",
            "source": "fallback",
            "reason": "LLM not configured; accepting heuristic result",
        }

    # LLM が返した JSON の形式を極力弾力的に受ける
    is_valid = bool(llm_result.get("is_valid", False))
    revised = llm_result.get("revised_header_row")
    revised_int: Optional[int] = None
    if isinstance(revised, int):
        revised_int = revised
    elif isinstance(revised, str) and revised.lstrip("-").isdigit():
        revised_int = int(revised)
    return {
        **base,
        "is_valid": is_valid,
        "revised_header_row": revised_int,
        "confidence": str(llm_result.get("confidence", "medium")),
        "source": "llm",
        "reason": str(llm_result.get("reason", ""))[:500],
    }


def _build_validation_prompt(cand: _SheetCandidate) -> str:
    """LLM に渡すプロンプトを組み立てる。返信は必ず JSON。"""
    body = {
        "sheet": cand.sheet,
        "candidate_header_row_index": cand.header_row,
        "candidate_header_values": cand.columns,
        "meta_kv_above_header": cand.meta_kv,
        "surrounding_rows": cand.surrounding_rows,
    }
    return (
        "You are validating whether a spreadsheet row is really a table header.\n"
        "Look at the CANDIDATE header row values, the meta key/value pairs above "
        "it, and the surrounding rows. Decide:\n"
        "  - is_valid (bool): is the candidate a genuine table header?\n"
        "  - revised_header_row (int|null): if not, propose a better row index "
        "in the surrounding_rows window; use null when confident it's correct.\n"
        "  - confidence: 'high'|'medium'|'low'.\n"
        "  - reason: one sentence in English or Japanese, <=200 chars.\n\n"
        "Reply with ONLY a JSON object of shape "
        "{is_valid, revised_header_row, confidence, reason}.\n\n"
        f"INPUT:\n{json.dumps(body, ensure_ascii=False, default=str)}"
    )
