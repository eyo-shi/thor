#!/usr/bin/env python3
"""Cloudera AI Agent Studio 用マニフェスト生成スクリプト。

これは薄いラッパで、本体は :mod:`thor.manifest.cli` にある。

使用例::

    # デフォルト出力先 (repo ルートの agent_studio_manifest/)
    python scripts/generate_studio_manifest.py

    # CI ドリフト検出
    python scripts/generate_studio_manifest.py --check
"""
from __future__ import annotations

import sys
from pathlib import Path

# ``python scripts/generate_studio_manifest.py`` の形でも import できるように、
# repo ルートを sys.path に足す。
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from thor.manifest.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
