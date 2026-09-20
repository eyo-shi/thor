/**
 * ChatPane のメッセージ / ステップ状態。
 * - messages: ユーザー / thor のバブル
 * - steps: 現在ターンのサブステップ (Router → 子 Crew の進捗)
 * - streaming: 送信中かどうか (Send ボタンの二重押し防止)
 */
import { create } from "zustand";
import type { SetupGuideError } from "../api/client";

export interface ChatMessage {
  id: string;
  role: "user" | "thor";
  text: string;
  /** Thor 応答に artifact が付いていたときのタブへのジャンプ用 */
  artifactIds?: string[];
  /** エラーコード (thor 側) */
  errorCode?: string;
  createdAt: number;
}

export interface StepEntry {
  agent: string;
  status: "running" | "done" | "skipped" | "error";
  message: string;
  at: number;
}

interface ChatState {
  messages: ChatMessage[];
  steps: StepEntry[];
  streaming: boolean;
  /** TreePane 等から prompt を予約する。PromptInput が読み取って textarea に反映。 */
  pendingPrompt: string | null;
  /** Deploy 後の設定不足 (LLM / Trino / CDV) を UI に伝える 503 由来のエラー。 */
  setupError: SetupGuideError | null;
  setPendingPrompt: (t: string | null) => void;
  appendUser: (text: string) => string;
  appendThor: (text: string, opts?: { artifactIds?: string[]; errorCode?: string }) => string;
  appendToLastThor: (delta: string) => void;
  addStep: (step: StepEntry) => void;
  clearSteps: () => void;
  setStreaming: (v: boolean) => void;
  addArtifactToLastThor: (artifactId: string) => void;
  setSetupError: (e: SetupGuideError | null) => void;
}

let _seq = 0;
const mid = () => {
  _seq += 1;
  return `m${_seq}-${Math.random().toString(36).slice(2, 6)}`;
};

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  steps: [],
  streaming: false,
  pendingPrompt: null,
  setupError: null,
  setPendingPrompt: (t) => set({ pendingPrompt: t }),
  setSetupError: (e) => set({ setupError: e }),
  appendUser: (text) => {
    const id = mid();
    set((s) => ({
      messages: [...s.messages, { id, role: "user", text, createdAt: Date.now() }],
    }));
    return id;
  },
  appendThor: (text, opts) => {
    const id = mid();
    set((s) => ({
      messages: [
        ...s.messages,
        {
          id,
          role: "thor",
          text,
          artifactIds: opts?.artifactIds ?? [],
          errorCode: opts?.errorCode,
          createdAt: Date.now(),
        },
      ],
    }));
    return id;
  },
  appendToLastThor: (delta) => {
    set((s) => {
      const idx = [...s.messages].reverse().findIndex((m) => m.role === "thor");
      if (idx < 0) {
        // 無ければ新規で thor バブルを作る
        const id = mid();
        return {
          messages: [
            ...s.messages,
            { id, role: "thor", text: delta, createdAt: Date.now() },
          ],
        };
      }
      const realIdx = s.messages.length - 1 - idx;
      const next = [...s.messages];
      next[realIdx] = { ...next[realIdx], text: next[realIdx].text + delta };
      return { messages: next };
    });
  },
  addStep: (step) => set((s) => ({ steps: [...s.steps, step] })),
  clearSteps: () => set({ steps: [] }),
  setStreaming: (v) => set({ streaming: v }),
  addArtifactToLastThor: (artifactId) => {
    set((s) => {
      const idx = [...s.messages].reverse().findIndex((m) => m.role === "thor");
      if (idx < 0) return s;
      const realIdx = s.messages.length - 1 - idx;
      const target = s.messages[realIdx];
      const existing = target.artifactIds ?? [];
      if (existing.includes(artifactId)) return s;
      const next = [...s.messages];
      next[realIdx] = { ...target, artifactIds: [...existing, artifactId] };
      return { messages: next };
    });
  },
}));
