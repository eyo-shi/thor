"""AMP セットアップ Step 1: Python 依存を editable install する。

Workbench の run_session 環境で pip install -e .[dev] を走らせる。
pyproject.toml の [project] dependencies と [project.optional-dependencies].dev
がすべて入る。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    """Cloudera Workbench の run_session (Jupyter kernel exec) では ``__file__``
    が定義されないため、env と cwd から repo root を解決する。"""
    for k in ("CDSW_PROJECT_DIR", "CML_PROJECT_DIR"):
        v = os.environ.get(k)
        if v and Path(v).exists():
            return Path(v).resolve()
    try:
        here = Path(__file__).resolve()  # 通常 python 実行時
        return here.parent.parent
    except NameError:
        pass
    cwd = Path.cwd().resolve()
    if cwd.name == "amp" and (cwd.parent / "pyproject.toml").exists():
        return cwd.parent
    return cwd


REPO_ROOT = _repo_root()


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
    # Jupyter kernel (run_session) は SystemExit を例外として catch し、
    # rc=0 でも CML engine が失敗扱いにする。成功時は素通りさせる。
    _rc = main()
    if _rc:
        raise SystemExit(_rc)
