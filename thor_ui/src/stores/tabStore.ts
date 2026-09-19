/**
 * ResultPane のタブ集合。
 * - 同一 (kind, ref-key) のタブは重複を避けて既存を再利用する
 * - active タブは 1 つ
 * - closeTab で消えると近傍のタブが active になる
 */
import { create } from "zustand";
import type { ArtifactType, TabDescriptor } from "../types";

interface OpenTabInput {
  title: string;
  kind: ArtifactType;
  ref: Record<string, unknown>;
  /**
   * 既存タブ判定用の重複キー (fq / bucket:key / artifact_id 等)。
   * 未指定なら JSON.stringify(ref) を使う (要素の順序に依存する)。
   */
  dedupeKey?: string;
}

interface TabState {
  tabs: TabDescriptor[];
  activeId: string | null;
  openTab: (input: OpenTabInput) => string;
  closeTab: (id: string) => void;
  setActive: (id: string) => void;
  clear: () => void;
}

function refKey(kind: ArtifactType, ref: Record<string, unknown>): string {
  return `${kind}::${JSON.stringify(ref)}`;
}

let _seq = 0;
function nextId(kind: ArtifactType): string {
  _seq += 1;
  return `${kind}-${_seq}-${Math.random().toString(36).slice(2, 6)}`;
}

export const useTabStore = create<TabState>((set, get) => ({
  tabs: [],
  activeId: null,
  openTab: (input) => {
    const key = input.dedupeKey ?? refKey(input.kind, input.ref);
    const existing = get().tabs.find((t) => {
      const tKey = t.dedupeKey ?? refKey(t.kind, t.ref);
      return t.kind === input.kind && tKey === key;
    });
    if (existing) {
      set({ activeId: existing.id });
      return existing.id;
    }
    const id = nextId(input.kind);
    const tab: TabDescriptor = {
      id,
      title: input.title,
      kind: input.kind,
      ref: input.ref,
      dedupeKey: input.dedupeKey,
    };
    set((s) => ({ tabs: [...s.tabs, tab], activeId: id }));
    return id;
  },
  closeTab: (id) => {
    set((s) => {
      const idx = s.tabs.findIndex((t) => t.id === id);
      if (idx < 0) return s;
      const nextTabs = s.tabs.filter((t) => t.id !== id);
      let nextActive = s.activeId;
      if (s.activeId === id) {
        nextActive =
          nextTabs[idx]?.id ?? nextTabs[idx - 1]?.id ?? nextTabs[0]?.id ?? null;
      }
      return { tabs: nextTabs, activeId: nextActive };
    });
  },
  setActive: (id) => set({ activeId: id }),
  clear: () => set({ tabs: [], activeId: null }),
}));
