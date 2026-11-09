/** 部门知识库分享 (wiki-hub) 客户端 (11/4)。走 gateway /v1/wiki/* 反代, 不直连 :8994。
 *
 * hub 按身份里的部门过滤: 员工看得到本部门的 + 管理员审过、全公司可见的 (11/09), admin / sysadmin 看全部。
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
  /** 11/08 分享治理: 知识页发了即可见但标「待审」; 主管看过打钩 → approved; 撤下 → withdrawn。
   *  老的 (上线前发的) 后端返 approved + legacy。没带这个字段的按 approved 算。 */
  review_status?: WikiReviewStatus;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  review_note?: string | null;
  withdrawn_by?: string | null;
  withdrawn_at?: string | null;
  legacy?: boolean;
  submitted_by?: string;
  submitted_at?: string;
  /** 11/09 全公司范围: scope=company 是发布人申请了全公司; visible_to=company 是管理员已通过、全公司可见。
   *  页面始终存在发布人部门里 (namespace 不变)。老 hub 没这些字段 = 本部门。 */
  scope?: WikiScope;
  visible_to?: WikiScope;
  company_status?: "pending" | "approved" | "rejected";
  company_requested_by?: string;
  company_requested_at?: string;
  company_reviewed_by?: string;
  company_reviewed_at?: string;
  company_note?: string;
  /** 待我审列表: 这一页等的是哪一档 (部门主管审 department / 管理员审 company) */
  needs?: WikiScope[];
}

export type WikiScope = "department" | "company";

/** 范围标的文字: 全公司 / 申请全公司 / 全公司未通过 / 本部门 */
export function scopeLabel(d: Pick<WikiDocSummary, "scope" | "visible_to" | "company_status">): {
  text: string;
  tone: "ok" | "warn" | "neutral";
} {
  if (d.visible_to === "company") return { text: "全公司", tone: "ok" };
  if (d.scope === "company" && d.company_status === "pending") return { text: "申请全公司", tone: "warn" };
  if (d.scope === "company" && d.company_status === "rejected") return { text: "全公司未通过", tone: "neutral" };
  return { text: "本部门", tone: "neutral" };
}

export type WikiReviewStatus = "pending" | "approved" | "withdrawn";

export const REVIEW_LABEL: Record<WikiReviewStatus, string> = { pending: "待审", approved: "已审", withdrawn: "已撤回" };

export function wikiReviewStatus(d: Pick<WikiDocSummary, "review_status" | "stale_after_unpublish">): WikiReviewStatus {
  if (d.review_status === "withdrawn") return "withdrawn";
  if (d.stale_after_unpublish) return "withdrawn";
  return d.review_status ?? "approved";
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

/** 待我审的知识页 (主管: 管的部门; admin: 全部)。 */
export const listReviewWikiDocs = () => api.get<{ documents: WikiDocSummary[] }>("/v1/wiki/review");

/** 主管 / admin: 看过打钩 (approve) 或撤下 (withdraw, 要写原因; 等于 unpublish + 标 withdrawn)。
 *  11/09 admin: approve_company (全公司可见) / reject_company (驳回, 要写原因, 页面留在本部门)。 */
export type WikiReviewDecision = "approve" | "withdraw" | "approve_company" | "reject_company";
export const reviewWikiDoc = (dept: string, fileId: string, decision: WikiReviewDecision, note: string) =>
  api.post<Record<string, unknown>>(`${docPath(dept, fileId)}/review`, { decision, note });

/** 11/09 作者 / admin: 申请全公司可见 (管理员审) / 收回到只在本部门。不重发。 */
export const setWikiScope = (dept: string, fileId: string, scope: WikiScope) =>
  api.post<WikiDocSummary & { ok: boolean }>(`${docPath(dept, fileId)}/scope`, { scope });
