"""Agent Studio マニフェスト生成 CLI。

使用例::

    # デフォルト: agent_studio_manifest/ に 3 ファイル書き出し
    python -m thor.manifest

    # 出力先を変える
    python -m thor.manifest --out ./out/manifests

    # stdout に結合して出す (dev / パイプ用)
    python -m thor.manifest --stdout

    # CI ドリフト検出: 再生成して既存ファイルと比較、diff があれば exit 1
    python -m thor.manifest --check
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from thor.manifest.introspect import (
    build_agents_manifest,
    build_crews_manifest,
    build_tools_manifest,
)
from thor.manifest.writer import (
    AGENTS_FILE,
    CREWS_FILE,
    TOOLS_FILE,
    diff_manifest,
    dump_manifest_yaml,
    write_manifests,
)

DEFAULT_OUT_DIR = Path("agent_studio_manifest")


def _build_all() -> tuple[dict, dict, dict]:
    return build_tools_manifest(), build_agents_manifest(), build_crews_manifest()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="thor-manifest",
        description="Generate Cloudera AI Agent Studio manifests from thor.* Python truth.",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"output directory (default: {DEFAULT_OUT_DIR})",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--stdout",
        action="store_true",
        help="print all three manifests to stdout instead of writing files",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="regenerate and compare against files on disk; exit 1 on drift (CI mode)",
    )
    return p.parse_args(argv)


def _print_stdout(tools: dict, agents: dict, crews: dict) -> None:
    for label, manifest in (
        (TOOLS_FILE, tools),
        (AGENTS_FILE, agents),
        (CREWS_FILE, crews),
    ):
        sys.stdout.write(f"# ===== {label} =====\n")
        sys.stdout.write(dump_manifest_yaml(manifest))
        sys.stdout.write("\n")


def _run_check(out_dir: Path, tools: dict, agents: dict, crews: dict) -> int:
    drifted: list[str] = []
    for filename, manifest in (
        (TOOLS_FILE, tools),
        (AGENTS_FILE, agents),
        (CREWS_FILE, crews),
    ):
        path = out_dir / filename
        diff = diff_manifest(path, manifest)
        if diff:
            drifted.append(filename)
            sys.stderr.write(f"\n--- drift detected in {path} ---\n")
            sys.stderr.write(diff)
    if drifted:
        sys.stderr.write(
            "\nDRIFT: "
            + ", ".join(drifted)
            + f"\nRegenerate with:  python -m thor.manifest --out {out_dir}\n"
        )
        return 1
    sys.stderr.write(f"OK: {out_dir} is in sync with thor.* Python truth.\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point (thor-manifest console script / python -m thor.manifest)."""
    args = _parse_args(argv)
    tools, agents, crews = _build_all()

    if args.stdout:
        _print_stdout(tools, agents, crews)
        return 0

    if args.check:
        return _run_check(args.out, tools, agents, crews)

    written = write_manifests(args.out, tools, agents, crews)
    for p in written:
        sys.stdout.write(f"wrote {p}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
