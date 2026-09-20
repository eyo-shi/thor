"""AMP セットアップ Step 2: React + Vite で thor_ui をビルドする。

成果物は vite.config.ts の ``build.outDir`` どおり ``thor/api/static/`` に
出力する (FastAPI が SPA として / で配信)。

Workbench の PBJ Python runtime には Node.js が含まれないことが多い。
``sudo`` も使えないため、ユーザー領域へ nvm + Node.js 20 を自動導入する。
``package-lock.json`` があれば ``npm ci``、無ければ ``npm install`` を使う。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

NVM_INSTALL_URL = (
    "https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh"
)
NODE_MAJOR = "20"


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
UI_DIR = REPO_ROOT / "thor_ui"
STATIC_OUT = REPO_ROOT / "thor" / "api" / "static"
LOCK_FILE = UI_DIR / "package-lock.json"


def _nvm_dir(env: dict[str, str]) -> Path:
    return Path(env.get("NVM_DIR", str(Path.home() / ".nvm"))).expanduser()


def _discover_node_bin_dir(nvm_dir: Path) -> Path | None:
    """``nvm`` が導入済みの Node ``bin/`` ディレクトリを探す (v20 優先)。"""
    versions_root = nvm_dir / "versions" / "node"
    if not versions_root.is_dir():
        return None
    candidates = sorted(versions_root.glob(f"v{NODE_MAJOR}*"), reverse=True)
    if not candidates:
        candidates = sorted(
            (p for p in versions_root.iterdir() if p.is_dir()),
            reverse=True,
        )
    for version_dir in candidates:
        bindir = version_dir / "bin"
        if (bindir / "node").is_file() and (bindir / "npm").is_file():
            return bindir
    return None


def _find_node_bin_dir(env: dict[str, str]) -> Path | None:
    """PATH または nvm 配下から node/npm の bin ディレクトリを返す。"""
    node_in_path = shutil.which("node", path=env.get("PATH"))
    if node_in_path:
        return Path(node_in_path).parent.resolve()
    nvm_bindir = _discover_node_bin_dir(_nvm_dir(env))
    if nvm_bindir:
        return nvm_bindir.resolve()
    return None


def _env_with_node_bin(env: dict[str, str], bindir: Path) -> dict[str, str]:
    prefix = str(bindir)
    path = env.get("PATH", os.environ.get("PATH", ""))
    if not path.startswith(prefix):
        path = f"{prefix}:{path}" if path else prefix
    out = {**env, "PATH": path, "NVM_DIR": str(_nvm_dir(env))}
    return out


def _run(
    cmd: list[str] | str,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    shell: bool = False,
) -> int:
    if isinstance(cmd, list):
        printable = " ".join(cmd)
    else:
        printable = cmd
    print(f"[amp:02] $ {printable}", flush=True)
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        shell=shell,
        check=False,
    ).returncode


def _install_nvm(env: dict[str, str]) -> bool:
    nvm_dir = _nvm_dir(env)
    nvm_sh = nvm_dir / "nvm.sh"
    if nvm_sh.is_file():
        return True
    print(
        "[amp:02] node not found — installing nvm into user space (no sudo)",
        flush=True,
    )
    script = f'curl -fsSL "{NVM_INSTALL_URL}" | bash'
    rc = _run(
        script,
        env={**env, "NVM_DIR": str(nvm_dir)},
        shell=True,
    )
    if rc != 0:
        print("[amp:02] nvm install failed", file=sys.stderr, flush=True)
        return False
    return nvm_sh.is_file()


def _install_node_via_nvm(env: dict[str, str]) -> bool:
    nvm_dir = _nvm_dir(env)
    script = f"""
set -e
export NVM_DIR="{nvm_dir}"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
nvm install {NODE_MAJOR}
nvm alias default {NODE_MAJOR}
nvm use default
node --version
npm --version
"""
    rc = _run(script, env={**env, "NVM_DIR": str(nvm_dir)}, shell=True)
    if rc != 0:
        print("[amp:02] node install via nvm failed", file=sys.stderr, flush=True)
        return False
    return _discover_node_bin_dir(nvm_dir) is not None


def _ensure_node_env(base_env: dict[str, str]) -> dict[str, str] | None:
    """node/npm が PATH に載った env を返す。必要なら nvm で導入する。"""
    bindir = _find_node_bin_dir(base_env)
    if bindir:
        print(f"[amp:02] using node at {bindir / 'node'}", flush=True)
        return _env_with_node_bin(base_env, bindir)

    if not _install_nvm(base_env):
        return None
    if not _install_node_via_nvm(base_env):
        return None

    bindir = _find_node_bin_dir({**base_env, "NVM_DIR": str(_nvm_dir(base_env))})
    if bindir is None:
        print(
            "[amp:02] node/npm still not found after nvm install",
            file=sys.stderr,
            flush=True,
        )
        return None
    print(f"[amp:02] using node at {bindir / 'node'}", flush=True)
    return _env_with_node_bin(base_env, bindir)


def _npm_install_cmd() -> list[str]:
    if LOCK_FILE.is_file():
        return ["npm", "ci"]
    print(
        "[amp:02] package-lock.json not found — falling back to npm install",
        flush=True,
    )
    return ["npm", "install"]


def _verify_static_out() -> bool:
    index_html = STATIC_OUT / "index.html"
    assets_dir = STATIC_OUT / "assets"
    if index_html.is_file() and assets_dir.is_dir():
        print(f"[amp:02] build output verified at {STATIC_OUT}", flush=True)
        return True
    print(
        f"[amp:02] expected {index_html} and {assets_dir}/ after build",
        file=sys.stderr,
        flush=True,
    )
    return False


def main() -> int:
    if not UI_DIR.exists():
        print(f"[amp:02] {UI_DIR} が見つからない", file=sys.stderr, flush=True)
        return 1

    env = _ensure_node_env({**os.environ, "CI": "1"})
    if env is None:
        return 2

    install_cmd = _npm_install_cmd()
    if _run(install_cmd, cwd=UI_DIR, env=env) != 0:
        return 3

    if _run(["npm", "run", "build"], cwd=UI_DIR, env=env) != 0:
        return 4

    if not _verify_static_out():
        return 5

    return 0


if __name__ == "__main__":
    # Jupyter kernel (run_session) は SystemExit を例外として catch し、
    # rc=0 でも CML engine が失敗扱いにする。成功時は素通りさせる。
    _rc = main()
    if _rc:
        raise SystemExit(_rc)
