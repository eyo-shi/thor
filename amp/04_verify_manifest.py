"""AMP セットアップ Step 4: Agent Studio manifest の drift 検出。

`python -m thor.manifest --check` を走らせて、agent_studio_manifest/*.yaml が
Python の source of truth と一致していることを確認する (Model C ハイブリッド
契約)。ズレていたら AMP セットアップ全体を失敗させる。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    cmd = [
        sys.executable,
        "-m",
        "thor.manifest",
        "--check",
        "--out",
        "agent_studio_manifest",
    ]
    print(f"[amp:04] $ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=REPO_ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
