"""AMP セットアップ Step 4: Agent Studio manifest の drift 検出。

`python -m thor.manifest --check` を走らせて、agent_studio_manifest/*.yaml が
Python の source of truth と一致していることを確認する (Model C ハイブリッド
契約)。ズレていたら AMP セットアップ全体を失敗させる。
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
        here = Path(__file__).resolve()
        return here.parent.parent
    except NameError:
        pass
    cwd = Path.cwd().resolve()
    if cwd.name == "amp" and (cwd.parent / "pyproject.toml").exists():
        return cwd.parent
    return cwd


REPO_ROOT = _repo_root()


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
    # Jupyter kernel (run_session) は SystemExit を例外として catch し、
    # rc=0 でも CML engine が失敗扱いにする。成功時は素通りさせる。
    _rc = main()
    if _rc:
        raise SystemExit(_rc)
