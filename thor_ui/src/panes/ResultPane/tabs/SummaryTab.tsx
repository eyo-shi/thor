/**
 * Summary タブ: Thor が生成した Markdown を描画する。
 * ref.markdown か ref.text にサマリー本文が入っている想定 (SSE artifact.ref)。
 */
import Markdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import type { TabDescriptor } from "../../../types";

interface Props {
  tab: TabDescriptor;
}

export function SummaryTab({ tab }: Props) {
  const md = String(tab.ref.markdown ?? tab.ref.text ?? "");
  if (!md) {
    return (
      <div className="tab-content">
        <p className="placeholder">Summary 本文が空です</p>
      </div>
    );
  }
  return (
    <div className="tab-content markdown-body">
      <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
        {md}
      </Markdown>
      <div className="tab-footer">
        <button
          onClick={() => {
            navigator.clipboard?.writeText(md).catch(() => {
              /* clipboard 権限拒否は無視 */
            });
          }}
        >
          Copy Markdown
        </button>
      </div>
    </div>
  );
}
