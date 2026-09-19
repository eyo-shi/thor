/**
 * セッション状態: session_id を URL / localStorage で永続化する。
 * URL クエリ ``?session=...`` が最優先、無ければ localStorage、それも無ければ null。
 */
import { create } from "zustand";

const STORAGE_KEY = "thor.sessionId";
const URL_KEY = "session";

function readInitialSessionId(): string | null {
  try {
    const url = new URL(window.location.href);
    const q = url.searchParams.get(URL_KEY);
    if (q) return q;
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function persistSessionId(id: string | null): void {
  try {
    if (id) {
      window.localStorage.setItem(STORAGE_KEY, id);
      const url = new URL(window.location.href);
      url.searchParams.set(URL_KEY, id);
      window.history.replaceState({}, "", url.toString());
    } else {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    /* SSR / restricted storage は無視 */
  }
}

export interface SessionState {
  sessionId: string | null;
  setSessionId: (id: string | null) => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  sessionId: readInitialSessionId(),
  setSessionId: (id) => {
    persistSessionId(id);
    set({ sessionId: id });
  },
}));
