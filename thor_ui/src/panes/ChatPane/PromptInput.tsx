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
            aria-label="停止"
            title="停止"
            onClick={() => wish.cancel()}
          >
            <IconStop />
          </button>
        ) : (
          <button
            type="button"
            className="chat-composer__btn chat-composer__btn--send"
            aria-label="送信"
            title="送信"
            onClick={() => void submit()}
            disabled={!text.trim()}
          >
            <IconSend />
          </button>
        )}
      </div>
      <p className="chat-composer__disclaimer">
        AI-generated results may be incorrect. Please exercise caution.
      </p>
    </div>
  );
}

function IconSend() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="chat-composer__icon">
      <path
        d="M5 12h12M13 6l6 6-6 6"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function IconStop() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="chat-composer__icon">
      <rect x="7" y="7" width="10" height="10" rx="1" fill="currentColor" />
    </svg>
  );
}
