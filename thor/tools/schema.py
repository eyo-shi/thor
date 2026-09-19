"""スキーマ推定・テーブル命名 Tool。

* :class:`TypeInferTool` - サンプル値の集合から Trino 型を推定
* :class:`NameProposerTool` - S3 キー / ファイル名からテーブル名候補を作る

いずれも LLM を使わないヒューリスティック実装。Text2SQL の LLM 補正は
上位の Agent で追加する (task #10 のときに再検討)。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from thor.transport.errors import ok
from thor.transport.logging import get_logger
from thor.transport.tool_base import BaseThorTool
from thor.transport.user_context import UserContext

_logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# TypeInferTool
# ------------------------------------------------------------------ #

_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+\.\d+$")
_DECIMAL_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_BOOL_STRS = frozenset({"true", "false", "TRUE", "FALSE", "True", "False", "yes", "no"})
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
)
_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y/%m/%d %H:%M:%S",
)


class TypeInferArgs(BaseModel):
    columns: dict[str, list[Any]] = Field(
        ...,
        description="{column_name: [sample_value, ...]} 形式。空値は None として渡す。",
    )
    max_varchar: int = Field(
        4000, description="VARCHAR の上限長 (超える場合は VARCHAR without length)"
    )


class TypeInferTool(BaseThorTool):
    """列のサンプル値から Trino 型を推定する。

    優先順位: BOOLEAN → BIGINT → DECIMAL(p,s) → DOUBLE → DATE → TIMESTAMP(6) →
    VARCHAR(N) → VARCHAR。すべて NULL の列は VARCHAR にフォールバック。
    """

    name: str = "type_infer"
    description: str = (
        "Infer a Trino/Iceberg type for each column from sample values. "
        "Returns [{name, trino_type, nullable, sample_size}]."
    )
    args_schema: type[BaseModel] = TypeInferArgs
    requires_auth: bool = False

    def run(
        self,
        user_ctx: Optional[UserContext],
        columns: dict[str, list[Any]],
        max_varchar: int = 4000,
        **_: Any,
    ) -> dict[str, Any]:
        out: list[dict[str, Any]] = []
        for name, values in columns.items():
            non_null = [v for v in values if v is not None and v != ""]
            nullable = len(non_null) < len(values)
            if not non_null:
                out.append(
                    {
                        "name": name,
                        "trino_type": "VARCHAR",
                        "nullable": True,
                        "sample_size": 0,
                    }
                )
                continue
            t = _infer_type_for_values(non_null, max_varchar)
            out.append(
                {
                    "name": name,
                    "trino_type": t,
                    "nullable": nullable,
                    "sample_size": len(non_null),
                }
            )
        return ok({"columns": out})


def _infer_type_for_values(values: list[Any], max_varchar: int) -> str:
    # 既に Python の型で来ている場合を先に判定
    if all(isinstance(v, bool) for v in values):
        return "BOOLEAN"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return "BIGINT"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        return "DOUBLE"
    if all(isinstance(v, datetime) for v in values):
        return "TIMESTAMP(6)"
    if all(isinstance(v, date) and not isinstance(v, datetime) for v in values):
        return "DATE"

    # 文字列で来ている場合はパースを試す
    strs = [str(v).strip() for v in values]
    if all(s in _BOOL_STRS for s in strs):
        return "BOOLEAN"
    if all(_INT_RE.match(s) for s in strs):
        return "BIGINT"
    if all(_DECIMAL_RE.match(s) for s in strs):
        # 小数部の桁数を見て DECIMAL(p,s) or DOUBLE
        max_scale = 0
        max_precision = 0
        for s in strs:
            neg = s.startswith("-")
            body = s[1:] if neg else s
            if "." in body:
                int_part, frac_part = body.split(".", 1)
                max_scale = max(max_scale, len(frac_part))
                max_precision = max(max_precision, len(int_part) + len(frac_part))
            else:
                max_precision = max(max_precision, len(body))
        if max_scale > 0 and max_precision <= 38:
            return f"DECIMAL({max_precision},{max_scale})"
        if max_scale == 0 and max_precision <= 18:
            return "BIGINT"
        return "DOUBLE"

    # 日付/時刻パース
    if _all_parse_with(strs, _DATE_FORMATS) is not None:
        return "DATE"
    if _all_parse_with(strs, _TIMESTAMP_FORMATS) is not None:
        return "TIMESTAMP(6)"

    # フォールバック: VARCHAR
    max_len = max(len(s) for s in strs)
    if max_len <= max_varchar:
        return f"VARCHAR({max_len})"
    return "VARCHAR"


def _all_parse_with(strs: list[str], formats: tuple[str, ...]) -> Optional[str]:
    """全値がいずれかのフォーマットでパースできれば、そのフォーマットを返す。"""
    for fmt in formats:
        try:
            for s in strs:
                datetime.strptime(s, fmt)
            return fmt
        except ValueError:
            continue
    return None


# ------------------------------------------------------------------ #
# NameProposerTool
# ------------------------------------------------------------------ #

_SNAKE_STRIP = re.compile(r"[^0-9a-zA-Z]+")
_LEADING_DIGIT = re.compile(r"^(\d)")


class NameProposerArgs(BaseModel):
    source_hint: str = Field(
        ..., description="ファイル名 / S3 キー / シート名など、命名の元ネタ"
    )
    schema_prefix: Optional[str] = Field(
        None, description="生成する fq 名の schema。省略時は table 名のみ"
    )
    catalog: str = Field("iceberg")


class NameProposerTool(BaseThorTool):
    """ファイル名等から snake_case のテーブル名を提案する。

    - 拡張子を落とし
    - 非英数を ``_`` に置換
    - 先頭が数字なら ``t_`` を付与
    - lower case、末尾の連続 ``_`` を削除
    """

    name: str = "name_proposer"
    description: str = (
        "Propose a Trino-friendly snake_case table name from a filename or "
        "S3 key. Returns proposed_table_name and fq_name."
    )
    args_schema: type[BaseModel] = NameProposerArgs
    requires_auth: bool = False

    def run(
        self,
        user_ctx: Optional[UserContext],
        source_hint: str,
        schema_prefix: Optional[str] = None,
        catalog: str = "iceberg",
        **_: Any,
    ) -> dict[str, Any]:
        stem = source_hint.rsplit("/", 1)[-1]
        stem = stem.rsplit(".", 1)[0] if "." in stem else stem
        base = _SNAKE_STRIP.sub("_", stem).strip("_").lower()
        base = re.sub(r"_+", "_", base) or "table"
        base = _LEADING_DIGIT.sub(r"t_\1", base)
        if len(base) > 63:  # PostgreSQL 系の慣習に合わせて 63 で切る
            base = base[:63].rstrip("_")
        fq = (
            f"{catalog}.{schema_prefix}.{base}"
            if schema_prefix
            else f"{catalog}.default.{base}"
        )
        return ok(
            {
                "proposed_table_name": base,
                "fq_name": fq,
                "catalog": catalog,
                "schema": schema_prefix or "default",
            }
        )
