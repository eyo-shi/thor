/**
 * ChatPane から SSE `/api/wish` を叩き、Store を更新する hook。
 *
 * - Store 経由で MessageList / StepIndicator / TabBar が反応する
 * - 送信中は AbortController を保持して cancel() を露出
 * - artifact イベントが来たら zustand tabStore に自動でタブを追加
 *   (title / kind / ref はサーバから来た値を使う)
 */
import { useCallback, useRef } from "react";
import { streamWish } from "../../api/wish";
import { useChatStore } from "../../stores/chatStore";
import { useSessionStore } from "../../stores/sessionStore";
import { useTabStore } from "../../stores/tabStore";
import type { ArtifactType, WishEvent } from "../../types";

export interface UseWishStream {
  send: (prompt: string) => Promise<void>;
  cancel: () => void;
}

export function useWishStream(): UseWishStream {
  const controllerRef = useRef<AbortController | null>(null);
  const appendUser = useChatStore((s) => s.appendUser);
  const appendThor = useChatStore((s) => s.appendThor);
  const appendToLastThor = useChatStore((s) => s.appendToLastThor);
  const addStep = useChatStore((s) => s.addStep);
  const clearSteps = useChatStore((s) => s.clearSteps);
  const setStreaming = useChatStore((s) => s.setStreaming);
  const addArtifactToLastThor = useChatStore((s) => s.addArtifactToLastThor);
  const openTab = useTabStore((s) => s.openTab);
  const sessionId = useSessionStore((s) => s.sessionId);

  const cancel = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setStreaming(false);
  }, [setStreaming]);

  const send = useCallback(
    async (prompt: string) => {
      const trimmed = prompt.trim();
      if (!trimmed) return;
      appendUser(trimmed);
      clearSteps();
      setStreaming(true);
      // Thor 側のバブルは token / artifact が来る前に空で用意
      appendThor("");

      const controller = new AbortController();
      controllerRef.current = controller;

      await streamWish(
        { prompt: trimmed, session_id: sessionId ?? undefined },
        {
          signal: controller.signal,
          onEvent: (evt: WishEvent) => {
            switch (evt.event) {
              case "step":
                addStep({
                  agent: evt.data.agent,
                  status: evt.data.status,
                  message: evt.data.message,
                  at: Date.now(),
                });
                break;
              case "token":
                appendToLastThor(evt.data.delta);
                break;
              case "artifact":
                addArtifactToLastThor(evt.data.id);
                openTab({
                  title: titleFor(evt.data.type, evt.data.ref),
                  kind: evt.data.type,
                  ref: { ...evt.data.ref, artifact_id: evt.data.id },
                  dedupeKey: `art:${evt.data.id}`,
                });
                break;
              case "error":
                appendToLastThor(
                  `\n\n[${evt.data.error_code}] ${evt.data.message}`,
                );
                break;
              case "done":
                // タブは既に開いているので特に何もしない
                break;
            }
          },
          onError: (e) => {
            appendToLastThor(`\n\n[STREAM_ERROR] ${String(e)}`);
          },
          onClose: () => {
            setStreaming(false);
            controllerRef.current = null;
          },
        },
      );
    },
    [
      appendUser,
      appendThor,
      appendToLastThor,
      addStep,
      addArtifactToLastThor,
      clearSteps,
      setStreaming,
      sessionId,
      openTab,
    ],
  );

  return { send, cancel };
}

function titleFor(kind: ArtifactType, ref: Record<string, unknown>): string {
  switch (kind) {
    case "table_preview":
      return String(ref.fq ?? "Table").split(".").slice(-1)[0] || "Table";
    case "dashboard":
      return String(ref.title ?? "Dashboard");
    case "summary":
      return String(ref.title ?? "Summary");
    case "sql":
      return "SQL";
    case "file_preview":
      return String(ref.key ?? "File").split("/").slice(-1)[0] || "File";
    default:
      return "Result";
  }
}
