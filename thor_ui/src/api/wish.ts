/**
 * `/api/wish` の SSE クライアント。
 *
 * `EventSource` は GET しか投げられないので、``POST /api/wish`` を叩くには
 * ``fetch`` + ``ReadableStream`` で SSE を手動パースする必要がある。以下は
 * その最小実装:
 *
 * - 各イベントは ``event: <name>\ndata: <json>\n\n`` 形式で改行区切り
 * - 部分読み込みは lastChunk に貯めて、``\n\n`` が来るたびに切り出す
 * - コールバック側で JSON.parse は各自
 *
 * FastAPI 側の契約は ``thor.api.sse`` を参照。
 */
import type { WishEvent } from "../types";

export interface WishStreamHandlers {
  onEvent: (evt: WishEvent) => void;
  onError?: (err: unknown) => void;
  onClose?: () => void;
  signal?: AbortSignal;
}

export interface WishRequestBody {
  prompt: string;
  session_id?: string | null;
  target_schema?: string;
}

/**
 * POST /api/wish を叩き、SSE を Handlers に流し込む。
 * 呼び出し元は AbortController を渡すことで中断できる。
 */
export async function streamWish(
  body: WishRequestBody,
  handlers: WishStreamHandlers,
): Promise<void> {
  const { onEvent, onError, onClose, signal } = handlers;
  let res: Response;
  try {
    res = await fetch("/api/wish", {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    onError?.(e);
    onClose?.();
    return;
  }
  if (!res.ok || !res.body) {
    onError?.({
      status: res.status,
      message: `wish failed: ${res.status} ${res.statusText}`,
    });
    onClose?.();
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  try {
    // eslint-disable-next-line no-constant-condition
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // イベント境界は空行 (\n\n)
      let sep = buffer.indexOf("\n\n");
      while (sep !== -1) {
        const raw = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const parsed = parseSseFrame(raw);
        if (parsed) onEvent(parsed);
        sep = buffer.indexOf("\n\n");
      }
    }
    // 残バッファ (最終フレーム) も出しておく
    if (buffer.trim()) {
      const parsed = parseSseFrame(buffer);
      if (parsed) onEvent(parsed);
    }
  } catch (e) {
    onError?.(e);
  } finally {
    onClose?.();
  }
}

/** SSE の 1 フレーム (`event: ...\ndata: ...`) を型付き WishEvent にする。 */
function parseSseFrame(raw: string): WishEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    }
    // ":" で始まる comment やその他はスキップ
  }
  if (!dataLines.length) return null;
  const dataStr = dataLines.join("\n");
  let data: unknown;
  try {
    data = JSON.parse(dataStr);
  } catch {
    return null;
  }
  switch (event) {
    case "step":
    case "token":
    case "artifact":
    case "error":
    case "done":
      // as ... で緩めに: サーバ契約 (thor.api.sse) に信頼を置く
      return { event, data } as WishEvent;
    default:
      return null;
  }
}
