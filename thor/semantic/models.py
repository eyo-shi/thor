"""Apache Ossie 準拠のセマンティックレイヤ Pydantic モデル。

Ossie の YAML 仕様の抜粋:
  version: 1
  dataset:
    name: sales_2024
    fq_name: iceberg.demo.sales_2024
    description: "..."
    owner: alice
    source:
      type: s3
      path: s3://bucket/path/file.xlsx
    dimensions:
      - name: region
        type: string
    measures:
      - name: revenue
        type: decimal(18,2)
        default_aggregation: sum
    sample_queries:
      - question: "..."
        sql: "..."

Ossie 本家仕様と 100% 互換ではなく、Thor が利用するサブセット。フィールドが
不足していれば無視 (extra='ignore') し、余分でもエラーを出さない設計。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class OssieSource(BaseModel):
    """データセットの物理ソース。"""

    model_config = ConfigDict(extra="allow")

    type: Literal["s3", "trino", "unknown"] = "unknown"
    path: Optional[str] = None
    catalog: Optional[str] = None
    schema_: Optional[str] = Field(None, alias="schema")
    table: Optional[str] = None


class OssieDimension(BaseModel):
    """ディメンション列 (集計軸)。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str
    type: str  # Ossie/dbt 系の string/date/timestamp/... (Trino 型ではない)
    description: Optional[str] = None
    is_time: bool = False
    cardinality: Optional[int] = None
    sample_values: list[Any] = Field(default_factory=list)


class OssieMeasure(BaseModel):
    """メジャー列 (数値集計対象)。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str
    type: str
    description: Optional[str] = None
    default_aggregation: Literal["sum", "avg", "min", "max", "count", "count_distinct"] = "sum"


class OssieSampleQuery(BaseModel):
    """Few-shot 用の質問→SQL 例。Text2SQL の RAG で使う。"""

    model_config = ConfigDict(extra="allow")

    question: str
    sql: str
    notes: Optional[str] = None


class OssieRelationship(BaseModel):
    """他データセットとの結合定義 (単純なケース)。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    to: str = Field(..., description="結合先データセットの fq_name")
    join_type: Literal["inner", "left", "right", "full"] = "inner"
    on: list[dict[str, str]] = Field(
        default_factory=list,
        description='[{"left": "col_a", "right": "col_b"}, ...]',
    )


class OssieDataset(BaseModel):
    """1 テーブル = 1 ファイル の Ossie データセット。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str
    fq_name: str
    description: Optional[str] = None
    owner: Optional[str] = None
    source: OssieSource = Field(default_factory=OssieSource)
    dimensions: list[OssieDimension] = Field(default_factory=list)
    measures: list[OssieMeasure] = Field(default_factory=list)
    relationships: list[OssieRelationship] = Field(default_factory=list)
    sample_queries: list[OssieSampleQuery] = Field(default_factory=list)


class OssieDocument(BaseModel):
    """YAML のトップレベル (version + dataset)。"""

    model_config = ConfigDict(extra="allow")

    version: int = 1
    dataset: OssieDataset


# ------------------------------------------------------------------ #
# Trino 型 → Ossie 型のシンプルな写像
# ------------------------------------------------------------------ #

_TRINO_TO_OSSIE: dict[str, str] = {
    "BOOLEAN": "boolean",
    "TINYINT": "integer",
    "SMALLINT": "integer",
    "INTEGER": "integer",
    "BIGINT": "integer",
    "REAL": "number",
    "DOUBLE": "number",
    "DATE": "date",
    "VARCHAR": "string",
    "CHAR": "string",
    "JSON": "string",
}


def trino_to_ossie_type(trino_type: str) -> str:
    """``TIMESTAMP(6)`` や ``DECIMAL(10,2)`` を Ossie の粒度に丸める。"""
    up = trino_type.upper()
    if up.startswith("TIMESTAMP"):
        return "timestamp"
    if up.startswith("DECIMAL"):
        return "number"
    if up.startswith("VARCHAR") or up.startswith("CHAR"):
        return "string"
    return _TRINO_TO_OSSIE.get(up.split("(", 1)[0], "string")


def classify_column(trino_type: str) -> Literal["dimension", "measure"]:
    """カラムがディメンションかメジャーかをデフォルト分類する。

    数値 (DECIMAL/DOUBLE/BIGINT/INT/REAL) はメジャー、それ以外はディメンション。
    IcebergCreate 時のヒューリスティックで、LLM が後で書き直しても構わない。
    """
    up = trino_type.upper()
    if up.startswith(("DECIMAL", "DOUBLE", "REAL", "BIGINT", "INT", "SMALLINT", "TINYINT")):
        return "measure"
    return "dimension"
