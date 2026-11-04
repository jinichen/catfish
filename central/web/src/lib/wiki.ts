/** 部门知识库分享 (wiki-hub) 客户端 (11/4)。走 gateway /v1/wiki/* 反代, 不直连 :8994。
 *
 * hub 按身份里的部门过滤: 员工只看得到本部门, admin / sysadmin 看全部。
 * 发布在 Companion 里做 (笔记在员工自己电脑上), 网页只看和撤回。
 */
import { api } from "./api";

export interface WikiDocSummary {
  namespace: string;
  file_id: string;
  filename: string;
  title: string;
  kind: string;
  description_preview?: string;
  published_by?: string;
  published_at?: string | null;
  updated_at?: string | null;
  size_bytes?: number;
  stale_after_unpublish?: boolean;
  unpublished_at?: string | null;
  unpublished_reason?: string | null;
}

export interface WikiDocDetail extends WikiDocSummary {
  frontmatter_yaml?: string;
  body_md?: string;
}

export const KIND_LABEL: Record<string, string> = { entity: "实体", concept: "概念", query: "查询" };

/** namespace 固定 `dept/<部门>`, 部门名可以是中文。 */
export function deptOf(namespace: string): string {
  return namespace.startsWith("dept/") ? namespace.slice(5) : namespace;
}

const docPath = (dept: string, fileId: string) =>
  `/v1/wiki/documents/dept/${encodeURIComponent(dept)}/${encodeURIComponent(fileId)}`;

export const listWikiDocs = () =>
  api.get<{ documents: WikiDocSummary[]; count: number }>("/v1/wiki/documents?include_stale=true");

export const getWikiDoc = (dept: string, fileId: string) => api.get<WikiDocDetail>(docPath(dept, fileId));

export const unpublishWikiDoc = (dept: string, fileId: string, reason: string) =>
  api.post<{ ok: boolean }>(`${docPath(dept, fileId)}/unpublish`, { reason });
