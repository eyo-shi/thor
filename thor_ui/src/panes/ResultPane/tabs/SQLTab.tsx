/**
 * SQL タブ: 生成 SQL を highlight.js でハイライト表示する。
 * EXPLAIN プランのツリー表示は v2。まずは Copy / (将来) Run のボタンだけ用意。
 */
import { useEffect, useRef } from "react";
import hljs from "highlight.js/lib/core";
import sql from "highlight.js/lib/languages/sql";
import "highlight.js/styles/github.css";
import type { TabDescriptor } from "../../../types";

hljs.registerLanguage("sql", sql);

interface Props {
  tab: TabDescriptor;
}

export function SQLTab({ tab }: Props) {
  const sqlText = String(tab.ref.sql ?? tab.ref.text ?? "");
  const explain = String(tab.ref.explain ?? "");
  const codeRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (codeRef.current) {
      // hljs 側の重複ハイライトを避ける
      delete (codeRef.current as any).dataset.highlighted;
      codeRef.current.classList.remove("hljs");
      hljs.highlightElement(codeRef.current);
    }
  }, [sqlText]);

  if (!sqlText) {
    return (
      <div className="tab-content">
        <p className="placeholder">SQL 本文が空です</p>
      </div>
    );
  }
  return (
    <div className="tab-content">
      <div className="tab-meta">
        <span className="meta-badge">SQL</span>
        <button
          className="tab-btn"
          onClick={() => {
            navigator.clipboard?.writeText(sqlText).catch(() => {
              /* clipboard 拒否は無視 */
            });
          }}
        >
          Copy SQL
        </button>
      </div>
      <pre className="sql-block">
        <code ref={codeRef} className="language-sql">
          {sqlText}
        </code>
      </pre>
      {explain && (
        <>
          <div className="tab-meta">
            <span className="meta-badge">EXPLAIN</span>
          </div>
          <pre className="sql-block sql-block--explain">{explain}</pre>
        </>
      )}
    </div>
  );
}
