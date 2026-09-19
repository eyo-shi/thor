import { create } from "zustand";

/**
 * セッション状態: session_id、開いているタブ、Entity Memory スナップショット。
 * 具体的な永続化 (URL クエリ / localStorage) は後続タスクで組み込む。
 */
export interface SessionState {
  sessionId: string | null;
  setSessionId: (id: string | null) => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  sessionId: null,
  setSessionId: (id) => set({ sessionId: id }),
}));
