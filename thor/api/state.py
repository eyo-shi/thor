"""In-memory の会話セッション / artifact ストア。

Workbench の 1 Application プロセスに閉じたインメモリ実装。プロセス再起動
で全て消える (デモに支障がある場合は後日 SQLite 化する)。
スレッドセーフに使うため :class:`threading.RLock` で保護する。
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


# ------------------------------------------------------------------ #
# Session
# ------------------------------------------------------------------ #
@dataclass
class SessionTurn:
    turn_id: str
    prompt: str
    response_markdown: str = ""
    artifact_ids: list[str] = field(default_factory=list)
    error_code: Optional[str] = None
    created_at: float = field(default_factory=time.time)


@dataclass
class Session:
    session_id: str
    user_name: str
    turns: list[SessionTurn] = field(default_factory=list)
    #: last_table / last_dashboard_id / last_s3_path / last_ossie_path など
    entity_memory: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


# ------------------------------------------------------------------ #
# Artifact
# ------------------------------------------------------------------ #
@dataclass
class Artifact:
    artifact_id: str
    type: str  # table_preview / dashboard / summary / sql / file_preview
    ref: dict[str, Any]
    session_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)


# ------------------------------------------------------------------ #
# Store
# ------------------------------------------------------------------ #
class InMemoryStore:
    """スレッドセーフな最小限のセッション/artifact ストア。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self._artifacts: dict[str, Artifact] = {}

    # ---- session ----
    def get_or_create_session(
        self, session_id: Optional[str], user_name: str
    ) -> Session:
        with self._lock:
            if session_id and session_id in self._sessions:
                return self._sessions[session_id]
            sid = session_id or f"sess_{uuid.uuid4().hex[:12]}"
            sess = Session(session_id=sid, user_name=user_name)
            self._sessions[sid] = sess
            return sess

    def get_session(self, session_id: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(session_id)

    def append_turn(self, session_id: str, turn: SessionTurn) -> None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                return
            sess.turns.append(turn)

    def update_entity_memory(
        self, session_id: str, updates: dict[str, Any]
    ) -> None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                return
            sess.entity_memory.update(updates)

    # ---- artifact ----
    def new_artifact_id(self, type_: str) -> str:
        return f"art_{type_[:4]}_{uuid.uuid4().hex[:10]}"

    def register_artifact(
        self,
        type_: str,
        ref: dict[str, Any],
        session_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
    ) -> Artifact:
        aid = artifact_id or self.new_artifact_id(type_)
        art = Artifact(artifact_id=aid, type=type_, ref=ref, session_id=session_id)
        with self._lock:
            self._artifacts[aid] = art
        return art

    def get_artifact(self, artifact_id: str) -> Optional[Artifact]:
        with self._lock:
            return self._artifacts.get(artifact_id)


# プロセス唯一のストア (Application は 1 プロセス構成)
_store = InMemoryStore()


def get_store() -> InMemoryStore:
    return _store
