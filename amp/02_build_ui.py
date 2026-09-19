"""AMP セットアップ Step 2: React + Vite で thor_ui をビルドする。

成果物は thor/api/static/ に出力する (FastAPI が SPA として / で配信)。

Workbench の PBJ Python runtime には Node.js が含まれていない場合があるので、
`which node` の存在チェックをして、無ければ nvm を使うか手順を案内する。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_DIR = REPO_ROOT / "thor_ui"
STATIC_OUT = REPO_ROOT / "thor" / "api" / "static"


def _find_node() -> str | None:
    return shutil.which("node")


def main() -> int:
    if not UI_DIR.exists():
        print(f"[amp:02] {UI_DIR} が見つからない", file=sys.stderr, flush=True)
        return 1
    node = _find_node()
    if node is None:
        print(
            "[amp:02] node が見つからない。Workbench の runtime に Node.js を"
            "含めるか、以下を Workbench ターミナルで実行してください:\n"
            "  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -\n"
            "  sudo apt-get install -y nodejs",
            file=sys.stderr,
            flush=True,
        )
        return 2
    print(f"[amp:02] using node at {node}", flush=True)

    env = {**os.environ, "CI": "1"}

    print("[amp:02] npm ci", flush=True)
    if subprocess.run(["npm", "ci"], cwd=UI_DIR, env=env).returncode != 0:
        return 3

    print("[amp:02] npm run build", flush=True)
    if subprocess.run(["npm", "run", "build"], cwd=UI_DIR, env=env).returncode != 0:
        return 4

    # Vite の出力先 (dist) を thor/api/static へコピー
    dist = UI_DIR / "dist"
    if not dist.exists():
        print(
            f"[amp:02] ビルド後に {dist} が無い。vite.config.ts の build.outDir を確認",
            file=sys.stderr,
            flush=True,
        )
        return 5
    if STATIC_OUT.exists():
        shutil.rmtree(STATIC_OUT)
    shutil.copytree(dist, STATIC_OUT)
    print(f"[amp:02] copied {dist} -> {STATIC_OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
