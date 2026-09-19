"""Ossie YAML のファイルシステム永続化 + 検索。

配置は AI Workbench プロジェクト内の Git 管理ディレクトリ ``THOR_SEMANTIC_ROOT``
(既定: ``./semantic/``) の直下:

    <root>/datasets/<catalog>/<schema>/<table>.yaml
    <root>/metrics/<domain>.yaml
    <root>/relationships/<domain>.yaml
    <root>/prompts/few_shot_examples.yaml

Git コミットはオプション: :func:`write_dataset` で ``commit=True`` を渡すと
GitPython 経由で add + commit を行う (push はしない。CI で push させる)。

検索 (:func:`search_datasets`) は最初は BM25 相当の単純な TF-IDF スコアで、
将来 embedding に置き換える余地を残す。
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import ValidationError

from thor.semantic.models import OssieDataset, OssieDocument
from thor.transport.errors import ErrorCode, err, ok
from thor.transport.logging import get_logger

_logger = get_logger(__name__)


def semantic_root() -> Path:
    """``THOR_SEMANTIC_ROOT`` 環境変数からルートを解決する。"""
    root = os.environ.get("THOR_SEMANTIC_ROOT", "./semantic")
    return Path(root).expanduser().resolve()


def _dataset_path(root: Path, fq_name: str) -> Path:
    """``iceberg.demo.sales_2024`` → ``<root>/datasets/iceberg/demo/sales_2024.yaml``"""
    parts = fq_name.split(".")
    if len(parts) != 3:
        raise ValueError(
            f"fq_name must be 'catalog.schema.table', got {fq_name!r}"
        )
    catalog, schema, table = parts
    return root / "datasets" / catalog / schema / f"{table}.yaml"


# ------------------------------------------------------------------ #
# read
# ------------------------------------------------------------------ #

def read_dataset(fq_name: str) -> dict[str, Any]:
    """1 データセットの YAML を読み、Pydantic 検証して dict で返す。"""
    root = semantic_root()
    path = _dataset_path(root, fq_name)
    if not path.exists():
        return err(ErrorCode.OSSIE_NOT_FOUND, f"Ossie dataset not found: {fq_name}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as e:
        return err(ErrorCode.OSSIE_YAML_INVALID, f"YAML parse failed: {e}")
    try:
        doc = OssieDocument.model_validate(raw)
    except ValidationError as e:
        return err(ErrorCode.OSSIE_YAML_INVALID, f"Ossie schema invalid: {e}")
    return ok(
        {
            "fq_name": fq_name,
            "path": str(path.relative_to(root)),
            "dataset": doc.dataset.model_dump(by_alias=True, exclude_none=True),
        }
    )


def list_datasets() -> list[dict[str, str]]:
    """データセットの一覧 (fq_name / path) を返す。"""
    root = semantic_root()
    datasets_dir = root / "datasets"
    if not datasets_dir.exists():
        return []
    out: list[dict[str, str]] = []
    for yaml_path in datasets_dir.rglob("*.yaml"):
        rel = yaml_path.relative_to(datasets_dir)
        parts = rel.with_suffix("").parts
        if len(parts) != 3:
            continue
        out.append(
            {
                "fq_name": ".".join(parts),
                "path": str(yaml_path.relative_to(root)),
            }
        )
    out.sort(key=lambda x: x["fq_name"])
    return out


# ------------------------------------------------------------------ #
# write
# ------------------------------------------------------------------ #

def write_dataset(
    dataset: OssieDataset,
    *,
    author_user: Optional[str] = None,
    commit: bool = False,
) -> dict[str, Any]:
    """データセット YAML を書き出す。``commit=True`` なら Git add + commit も行う。

    Git 競合や push 失敗は fatal にしない: ``git.status = "skipped"`` として
    メタ情報だけ返し、上位で通知する。
    """
    root = semantic_root()
    try:
        path = _dataset_path(root, dataset.fq_name)
    except ValueError as e:
        return err(ErrorCode.OSSIE_YAML_INVALID, str(e))
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = OssieDocument(version=1, dataset=dataset)
    payload = doc.model_dump(by_alias=True, exclude_none=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, allow_unicode=True, sort_keys=False)

    git_info: dict[str, Any] = {"status": "skipped"}
    if commit:
        git_info = _git_commit(root, path, author_user, dataset.fq_name)

    return ok(
        {
            "fq_name": dataset.fq_name,
            "path": str(path.relative_to(root)),
            "abs_path": str(path),
            "git": git_info,
        }
    )


def _git_commit(
    root: Path, path: Path, author_user: Optional[str], fq_name: str
) -> dict[str, Any]:
    """path を Git ワーキングツリーに add + commit する。"""
    try:
        from git import Repo  # type: ignore
        from git.exc import InvalidGitRepositoryError, GitCommandError  # type: ignore
    except ImportError:
        return {"status": "skipped", "reason": "GitPython is not installed"}
    try:
        repo = Repo(str(root), search_parent_directories=True)
    except InvalidGitRepositoryError:
        return {"status": "skipped", "reason": "not a git repository"}
    try:
        repo.index.add([str(path)])
        author_str = author_user or "thor-ingestion"
        commit_msg = f"chore(semantic): add/update {fq_name}\n\nBy: {author_str}"
        commit = repo.index.commit(commit_msg)
    except GitCommandError as e:
        return {"status": "skipped", "reason": f"git command failed: {e}"}
    return {
        "status": "committed",
        "commit_sha": commit.hexsha,
        "branch": repo.active_branch.name if not repo.head.is_detached else "(detached)",
    }


# ------------------------------------------------------------------ #
# search — 単純 TF-IDF (BM25 のような正規化はしない)
# ------------------------------------------------------------------ #

_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _dataset_terms(ds: dict[str, Any]) -> list[str]:
    """検索対象文字列群を tokenize してフラット化する。"""
    parts: list[str] = []
    for key in ("name", "fq_name", "description"):
        v = ds.get(key)
        if isinstance(v, str):
            parts.extend(_tokenize(v))
    for col in ds.get("dimensions", []) + ds.get("measures", []):
        for key in ("name", "description"):
            v = col.get(key)
            if isinstance(v, str):
                parts.extend(_tokenize(v))
    for sq in ds.get("sample_queries", []):
        v = sq.get("question")
        if isinstance(v, str):
            parts.extend(_tokenize(v))
    return parts


def search_datasets(query: str, top_k: int = 3) -> list[dict[str, Any]]:
    """クエリと関連度の高いデータセットを返す (単純 TF スコア)。

    完全に空でも安全に動く。ヒットしなければ [] を返す。
    """
    q_terms = _tokenize(query)
    if not q_terms:
        return []
    q_counter = Counter(q_terms)
    listed = list_datasets()
    if not listed:
        return []

    # 各データセットの TF を計算
    corpus: list[tuple[dict[str, str], Counter, list[str]]] = []
    for entry in listed:
        r = read_dataset(entry["fq_name"])
        if r.get("status") != "ok":
            continue
        ds = r["dataset"]
        terms = _dataset_terms(ds)
        corpus.append((entry, Counter(terms), terms))

    if not corpus:
        return []

    # 逆文書頻度
    N = len(corpus)
    df: Counter[str] = Counter()
    for _, tf, _ in corpus:
        for term in tf:
            df[term] += 1

    def _score(tf: Counter, terms: list[str]) -> float:
        s = 0.0
        length = max(len(terms), 1)
        for term, qc in q_counter.items():
            if term not in tf:
                continue
            idf = math.log((N + 1) / (df[term] + 1)) + 1.0
            s += qc * (tf[term] / length) * idf
        return s

    scored: list[tuple[float, dict[str, str]]] = []
    for entry, tf, terms in corpus:
        score = _score(tf, terms)
        if score > 0:
            scored.append((score, entry))
    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[dict[str, Any]] = []
    for score, entry in scored[:top_k]:
        r = read_dataset(entry["fq_name"])
        if r.get("status") == "ok":
            out.append(
                {
                    "fq_name": entry["fq_name"],
                    "path": entry["path"],
                    "score": round(score, 4),
                    "dataset": r["dataset"],
                }
            )
    return out
