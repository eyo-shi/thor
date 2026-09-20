/**
 * fetch ラッパ。Cloudera Knox 越しに配信される想定で credentials: 'include' を既定にする。
 * ベース URL は同一オリジン (/api/...)、開発時は vite の proxy で 127.0.0.1:8000 にフォワード。
 *
 * サーバーが HTTP 503 + `{error_code, message, instruction}` (または FastAPI
 * HTTPException 由来の `{detail: {...}}`) を返した場合は :class:`SetupGuideError`
 * を投げ、UI 側 (:mod:`ChatPane` / :mod:`TreePane`) が SetupGuide カードを表示する。
 */
export interface ApiError {
  status: number;
  message: string;
  code?: string;
}

/**
 * Deploy 後の設定不足 (LLM / Trino / CDV 未設定) を示すエラー。
 *
 * :class:`ChatPane` / :class:`TreePane` はこの型を検出したら SetupGuide カードを
 * 表示し、Project → Settings → Advanced → Environment Variables で該当 env を
 * 設定して Application を再起動する手順を提示する。
 */
export class SetupGuideError extends Error {
  readonly status: number;
  readonly errorCode: string;
  readonly instruction: string;
  constructor(errorCode: string, message: string, instruction: string) {
    super(message);
    this.name = "SetupGuideError";
    this.status = 503;
    this.errorCode = errorCode;
    this.instruction = instruction;
  }
}

/** サーバー応答から SetupGuide 用のフィールドを取り出す (見つからなければ null)。 */
export function parseSetupGuidePayload(
  payload: unknown,
): { errorCode: string; message: string; instruction: string } | null {
  if (!payload || typeof payload !== "object") return null;
  const p = payload as Record<string, unknown>;
  // wish.py の JSONResponse 直返しパターン: 上位に error_code / instruction
  const topEC = typeof p.error_code === "string" ? (p.error_code as string) : "";
  const topInstr =
    typeof p.instruction === "string" ? (p.instruction as string) : "";
  if (topEC && topInstr) {
    return {
      errorCode: topEC,
      message: typeof p.message === "string" ? (p.message as string) : topEC,
      instruction: topInstr,
    };
  }
  // catalog / query の HTTPException(detail=dict) パターン: .detail.error_code
  const detail = p.detail;
  if (detail && typeof detail === "object") {
    const d = detail as Record<string, unknown>;
    const ec = typeof d.error_code === "string" ? (d.error_code as string) : "";
    const instr =
      typeof d.instruction === "string" ? (d.instruction as string) : "";
    if (ec && instr) {
      return {
        errorCode: ec,
        message: typeof d.message === "string" ? (d.message as string) : ec,
        instruction: instr,
      };
    }
  }
  return null;
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers ?? {}),
    },
    ...init,
  });
  if (!res.ok) {
    let payload: unknown;
    try {
      payload = await res.json();
    } catch {
      payload = { message: res.statusText };
    }
    // Deploy 後の設定不足なら SetupGuide 用の型で再送出する
    if (res.status === 503) {
      const guide = parseSetupGuidePayload(payload);
      if (guide) {
        throw new SetupGuideError(
          guide.errorCode,
          guide.message,
          guide.instruction,
        );
      }
    }
    const err = payload as Partial<ApiError> & { detail?: Partial<ApiError> };
    const inner = err.detail ?? err;
    throw {
      status: res.status,
      message: inner.message ?? err.message ?? res.statusText,
      code: inner.code ?? err.code,
    } satisfies ApiError;
  }
  return (await res.json()) as T;
}
