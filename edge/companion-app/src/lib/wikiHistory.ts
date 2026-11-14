/** 部门知识库共享页的历史版本 (11/14): 中央每次重发前存上一版, 最近 30 版。直连 gateway, 跟 WikiHubCard 一样。 */
import { config } from "./env";
import { fetchWithAuth } from "./me";

export interface WikiVersion {
  revision: number;
  title?: string;
  published_by?: string;
  updated_at?: string | null;
  size_bytes?: number;
}

export interface WikiHistory {
  current_revision: number;
  current_updated_at?: string | null;
  versions: WikiVersion[];
}

function docUrl(namespace: string, fileId: string, suffix = ""): string {
  const dept = namespace.startsWith("dept/") ? namespace.slice("dept/".length) : namespace;
  return `${config.gatewayUrl}/v1/wiki/documents/dept/${encodeURIComponent(dept)}/${encodeURIComponent(fileId)}${suffix}`;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = "";
    try {
      detail = String(((await res.json()) as { detail?: unknown }).detail ?? "");
    } catch {
      /* 不是 JSON */
    }
    throw new Error(detail || `中央返回 ${res.status}`);
  }
  return (await res.json()) as T;
}

export const wikiHistory = async (namespace: string, fileId: string) =>
  json<WikiHistory>(await fetchWithAuth(docUrl(namespace, fileId, "/history")));

export const wikiHistoryVersion = async (namespace: string, fileId: string, revision: number) =>
  json<WikiVersion & { body_md: string; frontmatter_yaml: string }>(
    await fetchWithAuth(docUrl(namespace, fileId, `/history/${revision}`)));

export const wikiHistoryRestore = async (namespace: string, fileId: string, revision: number) =>
  json<{ revision: number; restored_from: number; review_status: string }>(
    await fetchWithAuth(docUrl(namespace, fileId, `/history/${revision}/restore`), { method: "POST" }));
