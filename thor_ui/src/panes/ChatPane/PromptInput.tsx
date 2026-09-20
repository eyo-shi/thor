/**
 * multiline prompt 入力。Ctrl+Enter (Mac は Cmd+Enter) で Send。
 * streaming 中は Send が Cancel に切り替わる。
 */
import { useEffect, useState } from "react";
import { useChatStore } from "../../stores/chatStore";
import type { UseWishStream } from "./useWishStream";

interface Props {
  wish: UseWishStream;
}

export function PromptInput({ wish }: Props) {
  const [text, setText] = useState("");
  const streaming = useChatStore((s) => s.streaming);
  const pendingPrompt = useChatStore((s) => s.pendingPrompt);
  const setPendingPrompt = useChatStore((s) => s.setPendingPrompt);

  // TreePane 等からの予約プロンプトを textarea に反映する
  useEffect(() => {
    if (pendingPrompt != null) {
      setText(pendingPrompt);
      setPendingPrompt(null);
    }
  }, [pendingPrompt, setPendingPrompt]);

  async function submit() {
    const t = text.trim();
    if (!t) return;
    setText("");
    await wish.send(t);
  }

  return (
    <div className="chat-composer">
      <div className="chat-composer__inner">
        <textarea
          className="chat-composer__input"
          placeholder="Thor に質問する… (Ctrl/⌘ + Enter で送信)"
          rows={1}
          value={text}
          disabled={streaming}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
              e.preventDefault();
              void submit();
            }
          }}
        />
        {streaming ? (
          <button
            type="button"
            className="chat-composer__btn chat-composer__btn--cancel"
            onClick={() => wish.cancel()}
          >
            停止
          </button>
        ) : (
          <button
            type="button"
            className="chat-composer__btn chat-composer__btn--send"
            onClick={() => void submit()}
            disabled={!text.trim()}
          >
            送信
          </button>
        )}
      </div>
    </div>
  );
}
