/**
 * fetch ラッパ。Cloudera Knox 越しに配信される想定で credentials: 'include' を既定にする。
 * ベース URL は同一オリジン (/api/...)、開発時は vite の proxy で 127.0.0.1:8000 にフォワード。
 */
export interface ApiError {
  status: number;
  message: string;
  code?: string;
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
    const err = payload as Partial<ApiError>;
    throw {
      status: res.status,
      message: err.message ?? res.statusText,
      code: err.code,
    } satisfies ApiError;
  }
  return (await res.json()) as T;
}
