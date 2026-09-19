"""AMP セットアップ Step 1: Python 依存を editable install する。

Workbench の run_session 環境で pip install -e .[dev] を走らせる。
pyproject.toml の [project] dependencies と [project.optional-dependencies].dev
がすべて入る。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    print(f"[amp:01] pip install -e .[dev]  (cwd={REPO_ROOT})", flush=True)
    cmd = [sys.executable, "-m", "pip", "install", "-e", ".[dev]"]
    proc = subprocess.run(cmd, cwd=REPO_ROOT)
    if proc.returncode != 0:
        print("[amp:01] pip install failed", file=sys.stderr, flush=True)
        return proc.returncode

    # インポートスモークテスト (crewai / pydantic / thor.tools / thor.ingestion)
    print("[amp:01] import smoke test", flush=True)
    smoke = subprocess.run(
        [
            sys.executable,
            "-c",
            "import thor, thor.tools, thor.ingestion, thor.manifest; "
            "print('thor version:', thor.__version__ if hasattr(thor, '__version__') else 'dev')",
        ],
        cwd=REPO_ROOT,
    )
    return smoke.returncode


if __name__ == "__main__":
    raise SystemExit(main())
