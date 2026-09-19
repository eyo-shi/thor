/**
 * ChatPane のメッセージ履歴。user/thor バブル、artifact ジャンプ、error 色分け。
 */
import { useEffect, useRef } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useChatStore } from "../../stores/chatStore";
import { useTabStore } from "../../stores/tabStore";

export function MessageList() {
  const messages = useChatStore((s) => s.messages);
  const setActive = useTabStore((s) => s.setActive);
  const tabs = useTabStore((s) => s.tabs);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  if (messages.length === 0) {
    return (
      <div className="chat-empty">
        <p>Ask Thor…</p>
        <ul>
          <li>「s3://demo-bucket/... を取り込んで」</li>
          <li>「そのテーブルのサマリーを作って」</li>
          <li>「ダッシュボードを作って」</li>
        </ul>
      </div>
    );
  }

  return (
    <div className="chat-messages">
      {messages.map((m) => (
        <div
          key={m.id}
          className={
            "chat-bubble chat-bubble--" +
            m.role +
            (m.errorCode ? " chat-bubble--error" : "")
          }
        >
          {m.role === "thor" ? (
            <div className="markdown-body chat-md">
              <Markdown remarkPlugins={[remarkGfm]}>{m.text || " "}</Markdown>
            </div>
          ) : (
            <p>{m.text}</p>
          )}
          {m.artifactIds && m.artifactIds.length > 0 && (
            <div className="chat-artifacts">
              {m.artifactIds.map((aid) => {
                const tab = tabs.find(
                  (t) => t.ref && (t.ref as any).artifact_id === aid,
                );
                if (!tab) return null;
                return (
                  <button
                    key={aid}
                    className="chat-artifact-link"
                    onClick={() => setActive(tab.id)}
                  >
                    ▶ 中央ペインで開く: {tab.title}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
