"""Agent Studio 用マニフェスト自動生成モジュール。

Model C ハイブリッド方針:
  * production 経路は Python の :mod:`thor.ingestion` / :mod:`thor.router` /
    :mod:`thor.analytics` を直接呼ぶ (single source of truth)。
  * Cloudera AI Agent Studio 上でのビジュアライズ / 実行のためには、Studio 側の
    契約に沿った YAML マニフェストが必要。手書きすると Python 側との drift が
    確実に発生するので、Python 定義を introspect して自動生成する。

このモジュールは 3 種類のマニフェストを組み立てる:

  * :func:`build_tools_manifest`  -- :mod:`thor.tools` の公開 Tool 全てを
    JSON Schema 付きで列挙。
  * :func:`build_agents_manifest` -- 各 Crew の Agent (role/goal/backstory/tools)
    を列挙。
  * :func:`build_crews_manifest`  -- 3 Crew の process / agents / tasks
    (context 依存関係を task 名に解決した形) を列挙。

CLI (:mod:`thor.manifest.cli`) は上記 3 つを ``agent_studio_manifest/`` 配下に
書き出し、``--check`` で CI ドリフト検出を行う。
"""
from thor.manifest.introspect import (
    build_agents_manifest,
    build_crews_manifest,
    build_tools_manifest,
)
from thor.manifest.writer import (
    diff_manifest,
    dump_manifest_yaml,
    write_manifest_file,
    write_manifests,
)

__all__ = [
    "build_tools_manifest",
    "build_agents_manifest",
    "build_crews_manifest",
    "dump_manifest_yaml",
    "write_manifest_file",
    "write_manifests",
    "diff_manifest",
]
