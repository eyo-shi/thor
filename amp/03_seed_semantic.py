"""AMP セットアップ Step 3: Ossie 用 semantic/ を Git 初期化する。

semantic/{datasets,metrics,relationships,prompts,index} を用意し、Git 管理下に
置く (既に .git があれば no-op)。Ossie Drafter が PR ベースで YAML を積める
状態にすることが目的。

外部 Git remote は AMP セットアップ画面では設定しない (組織ごとに異なるため)。
運用開始後に Workbench の Terminal から `git remote add origin <url>` する想定。
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
SEMANTIC = REPO_ROOT / "semantic"
SUBDIRS = ["datasets", "metrics", "relationships", "prompts", "index"]


def _run(cmd: list[str], cwd: Path) -> int:
    print(f"[amp:03] $ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=cwd).returncode


def main() -> int:
    SEMANTIC.mkdir(parents=True, exist_ok=True)
    for sub in SUBDIRS:
        d = SEMANTIC / sub
        d.mkdir(parents=True, exist_ok=True)
        keep = d / ".gitkeep"
        if not keep.exists() and not any(d.iterdir()):
            keep.write_text("", encoding="utf-8")

    git_dir = SEMANTIC / ".git"
    if git_dir.exists():
        print("[amp:03] semantic/.git already exists — skipping init", flush=True)
        return 0

    # semantic/ 単体で独立 Git repo にする (Workbench プロジェクトの Git とは別)
    if _run(["git", "init", "-b", "main"], cwd=SEMANTIC) != 0:
        return 1
    if _run(["git", "add", "-A"], cwd=SEMANTIC) != 0:
        return 2
    # user.name / user.email が未設定の環境向けに local config を投入
    subprocess.run(
        ["git", "config", "user.name", "Thor Ossie Drafter"], cwd=SEMANTIC
    )
    subprocess.run(
        ["git", "config", "user.email", "ossie@thor.local"], cwd=SEMANTIC
    )
    if (
        _run(
            ["git", "commit", "-m", "chore(ossie): seed semantic/ layout"],
            cwd=SEMANTIC,
        )
        != 0
    ):
        return 3
    print("[amp:03] seeded semantic/ as a fresh git repo (branch=main)", flush=True)
    return 0


if __name__ == "__main__":
    # Jupyter kernel (run_session) は SystemExit を例外として catch し、
    # rc=0 でも CML engine が失敗扱いにする。成功時は素通りさせる。
    _rc = main()
    if _rc:
        raise SystemExit(_rc)
