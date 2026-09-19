/**
 * Table Preview 用の SELECT クエリ発行 hook。
 * サーバー側で LIMIT が強制されるので、UI は max_rows の上限だけ提示すればよい。
 */
import { useMutation } from "@tanstack/react-query";
import { apiFetch } from "./client";
import type { QueryRequest, QueryResponse } from "../types";

export function useRunQuery() {
  return useMutation({
    mutationFn: (req: QueryRequest) =>
      apiFetch<QueryResponse>("/api/query", {
        method: "POST",
        body: JSON.stringify(req),
      }),
  });
}

/**
 * fq (`catalog.schema.table`) から先頭 N 行を取る簡易ヘルパ。
 * table 部分にドットが含まれる場合は quoted identifier で包む。
 */
export async function fetchTablePreview(
  fq: string,
  maxRows: number = 100,
): Promise<QueryResponse> {
  const parts = fq.split(".");
  if (parts.length !== 3) {
    throw new Error(`Invalid fq: ${fq}`);
  }
  const [catalog, schema, table] = parts;
  const sql = `SELECT * FROM "${catalog}"."${schema}"."${table}" LIMIT ${maxRows}`;
  return apiFetch<QueryResponse>("/api/query", {
    method: "POST",
    body: JSON.stringify({ sql, catalog, max_rows: maxRows }),
  });
}
