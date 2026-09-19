/**
 * TreePane の iceberg ルート用: schemas / tables / columns を叩く hook 群。
 * すべて Knox JWT (credentials: 'include') 前提。
 */
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "./client";
import type {
  ColumnListResponse,
  SchemaListResponse,
  TableListResponse,
} from "../types";

export function useSchemas(catalog: string = "iceberg") {
  return useQuery({
    queryKey: ["catalog", "schemas", catalog],
    queryFn: () =>
      apiFetch<SchemaListResponse>(
        `/api/catalog/schemas?catalog=${encodeURIComponent(catalog)}`,
      ),
  });
}

export function useTables(schema: string | null, catalog: string = "iceberg") {
  return useQuery({
    queryKey: ["catalog", "tables", catalog, schema],
    enabled: !!schema,
    queryFn: () =>
      apiFetch<TableListResponse>(
        `/api/catalog/tables?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema!)}`,
      ),
  });
}

export function useColumns(fq: string | null) {
  return useQuery({
    queryKey: ["catalog", "columns", fq],
    enabled: !!fq,
    queryFn: () =>
      apiFetch<ColumnListResponse>(
        `/api/catalog/columns?fq=${encodeURIComponent(fq!)}`,
      ),
  });
}
