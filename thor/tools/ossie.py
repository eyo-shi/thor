"""Ossie セマンティックレイヤ操作 Tool。

* :class:`OssieReadTool` — fq_name で 1 データセットを読む
* :class:`OssieWriteTool` — Ingestion Crew が draft YAML を書く
* :class:`OssieSearchTool` — Text2SQL の RAG 段で類似データセットを取る
* :class:`SimilarTableSearchTool` — 命名衝突/類似検索 (Ingestion 用のエイリアス)
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from thor.semantic.models import OssieDataset
from thor.semantic.store import (
    list_datasets,
    read_dataset,
    search_datasets,
    write_dataset,
)
from thor.transport.errors import ErrorCode, err, ok
from thor.transport.logging import get_logger
from thor.transport.tool_base import BaseThorTool
from thor.transport.user_context import UserContext

_logger = get_logger(__name__)


# ------------------------------------------------------------------ #
# OssieReadTool
# ------------------------------------------------------------------ #
class OssieReadArgs(BaseModel):
    fq_name: str = Field(..., description="読み出したいデータセットの fq_name")


class OssieReadTool(BaseThorTool):
    """1 データセットの YAML を辞書として返す。"""

    name: str = "ossie_read"
    description: str = (
        "Read one Ossie dataset YAML by its fully-qualified name "
        "(catalog.schema.table). Returns the dataset as a dict."
    )
    args_schema: type[BaseModel] = OssieReadArgs
    requires_auth: bool = False  # ローカル FS。Git 上の ACL は別レイヤで制御

    def run(
        self, user_ctx: Optional[UserContext], fq_name: str, **_: Any
    ) -> dict[str, Any]:
        return read_dataset(fq_name)


# ------------------------------------------------------------------ #
# OssieWriteTool
# ------------------------------------------------------------------ #
class OssieWriteArgs(BaseModel):
    dataset: dict[str, Any] = Field(
        ...,
        description="Ossie dataset (name, fq_name, source, dimensions, measures, ...) の dict",
    )
    commit: bool = Field(
        False, description="Git add + commit まで行う場合 True。push はしない。"
    )


class OssieWriteTool(BaseThorTool):
    """Ingestion Crew が draft YAML をコミット可能な形で書き出す。

    Pydantic 検証を通すので、LLM が壊れた JSON を出しても error_code=OSSIE_YAML_INVALID
    で早期終了できる。
    """

    name: str = "ossie_write"
    description: str = (
        "Persist an Ossie dataset YAML under THOR_SEMANTIC_ROOT/datasets/<catalog>/"
        "<schema>/<table>.yaml. If commit=true, git-add and commit the file."
    )
    args_schema: type[BaseModel] = OssieWriteArgs

    def run(
        self,
        user_ctx: Optional[UserContext],
        dataset: dict[str, Any],
        commit: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        try:
            ds = OssieDataset.model_validate(dataset)
        except ValidationError as e:
            return err(ErrorCode.OSSIE_YAML_INVALID, f"Ossie dataset invalid: {e}")
        author = user_ctx.user_name if user_ctx else None
        return write_dataset(ds, author_user=author, commit=commit)


# ------------------------------------------------------------------ #
# OssieSearchTool
# ------------------------------------------------------------------ #
class OssieSearchArgs(BaseModel):
    query: str = Field(..., description="自然言語のクエリ (Text2SQL 用)")
    top_k: int = Field(3, ge=1, le=10)


class OssieSearchTool(BaseThorTool):
    """クエリと最も関連の高い ``top_k`` データセットを返す。"""

    name: str = "ossie_search"
    description: str = (
        "Find the top-k Ossie datasets most relevant to a natural-language "
        "query. Used by Text-to-SQL RAG."
    )
    args_schema: type[BaseModel] = OssieSearchArgs
    requires_auth: bool = False

    def run(
        self,
        user_ctx: Optional[UserContext],
        query: str,
        top_k: int = 3,
        **_: Any,
    ) -> dict[str, Any]:
        hits = search_datasets(query, top_k=top_k)
        return ok({"query": query, "top_k": top_k, "results": hits})


# ------------------------------------------------------------------ #
# SimilarTableSearchTool  (Ingestion Crew から使う軽量ラッパ)
# ------------------------------------------------------------------ #
class SimilarTableSearchArgs(BaseModel):
    proposed_name: str = Field(..., description="これから作ろうとしているテーブル名")
    top_k: int = Field(5, ge=1, le=20)


class SimilarTableSearchTool(BaseThorTool):
    """既存 Ossie データセットから、命名が近いものを列挙する。

    完全一致は「衝突」、部分一致は「類似」として上位に返す。
    Ingestion の ``propose_schema_and_name_task`` から呼ぶ想定。
    """

    name: str = "similar_table_search"
    description: str = (
        "List existing datasets whose name is identical or similar to the "
        "proposed table name. Use before CREATE TABLE to warn about name "
        "collisions."
    )
    args_schema: type[BaseModel] = SimilarTableSearchArgs
    requires_auth: bool = False

    def run(
        self,
        user_ctx: Optional[UserContext],
        proposed_name: str,
        top_k: int = 5,
        **_: Any,
    ) -> dict[str, Any]:
        proposed = proposed_name.lower()
        exact: list[dict[str, str]] = []
        similar: list[dict[str, Any]] = []
        for entry in list_datasets():
            table = entry["fq_name"].rsplit(".", 1)[-1].lower()
            if table == proposed:
                exact.append(entry)
                continue
            # 部分一致: 3 文字以上の共通部分
            if len(proposed) >= 3 and (
                proposed in table or table in proposed
            ):
                similar.append({**entry, "table": table})
        # TF-IDF 検索で意味的類似も拾う
        semantic = search_datasets(proposed_name.replace("_", " "), top_k=top_k)
        return ok(
            {
                "proposed_name": proposed_name,
                "exact_matches": exact,  # 空なら衝突なし
                "name_similar": similar[:top_k],
                "semantic_similar": [
                    {"fq_name": s["fq_name"], "score": s["score"]}
                    for s in semantic
                ],
            }
        )
