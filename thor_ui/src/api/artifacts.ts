/**
 * ResultPane タブが再描画に使う artifact 取得。
 * SSE で `event: artifact` を受け取ったら useArtifact でメタを取り、
 * type に応じて tabs/*Tab.tsx が適切なフェッチを走らせる。
 */
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "./client";
import type { Artifact } from "../types";

export function useArtifact(artifactId: string | null) {
  return useQuery({
    queryKey: ["artifact", artifactId],
    enabled: !!artifactId,
    queryFn: () =>
      apiFetch<Artifact>(`/api/artifacts/${encodeURIComponent(artifactId!)}`),
  });
}
