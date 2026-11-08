/** Skills Hub API 客户端 (BL-ARCH1 5/10).
 *
 * 全部走 gateway /v1/hub/* 反代, 不直连 hub :8997.
 *
 * 11/08 分享治理 (docs/SHARE-GOVERNANCE-PLAN.md §6.1): 发技能不再直接上架 —— 进待审核,
 * 部门范围由本部门主管审、全公司由管理员审; 审批人可以在 Companion 里「装到我这里试用」,
 * 试跑结果回传到审核记录 (trials)。
 */

import { api } from "./api";

// ── 分享治理 ──────────────────────────────────────────────────────────────

export type ShareScope = "department" | "company";
export type ReviewStatus = "pending" | "approved" | "rejected" | "withdrawn";

/** 审批人在 Companion 里「装到我这里试用」后回传的试跑结果。 */
export interface TrialResult {
  ok: boolean;
  summary?: string;
  files?: string[];
  steps?: string[];
  at?: string;
  /** 专家包的试跑多一个: 跑的是哪条工作流 */
  workflow?: string;
}

export interface TrialRecord {
  by: string;
  at: string;
  expires?: number | string;
  result?: TrialResult;
}

/** 一个技能版本的审核记录。老的 (上线前发的) 一律 legacy + approved, 不回溯。 */
export interface SkillReview {
  namespace?: string;
  name?: string;
  version?: string;
  scope: ShareScope;
  departments: string[];
  status: ReviewStatus;
  submitted_by?: string;
  submitted_dept?: string;
  submitted_at?: string;
  submit_note?: string;
  reviewed_by?: string;
  reviewed_at?: string;
  review_note?: string;
  legacy?: boolean;
  bundle_sha256?: string;
  file_count?: number;
  /** 只有审批人 / 提交人拿得到 */
  trials?: TrialRecord[];
}

export const REVIEW_STATUS_LABEL: Record<ReviewStatus, string> = {
  pending: "待审核", approved: "已上架", rejected: "被驳回", withdrawn: "已撤回",
};

export function reviewStatusLabel(r?: Pick<SkillReview, "status" | "legacy"> | null): string {
  if (!r || r.legacy) return REVIEW_STATUS_LABEL.approved;
  return REVIEW_STATUS_LABEL[r.status] ?? r.status;
}

export const SCOPE_LABEL: Record<ShareScope, string> = { department: "本部门", company: "全公司" };

export interface SkillSummary {
  namespace: string;
  name: string;
  latest_version: string;
  all_versions?: string[];
  description?: string;
  deprecated?: boolean;
  published_by?: string;
  published_at?: string;
  file_count?: number;
  total_bytes?: number;
  subscribe_count?: number;
  rating_avg?: number | null;
  rating_count?: number;
  review?: SkillReview;
}

export interface SkillsListResponse {
  skills: SkillSummary[];
  count: number;
}

export interface SkillDetail {
  namespace: string;
  name: string;
  version: string;
  description: string;
  deprecated: boolean;
  files: string[];
  files_sha256: Record<string, string>;
  skill_md: string;
  review?: SkillReview;
}

export async function listSkills(namespace?: string): Promise<SkillsListResponse> {
  const q = namespace ? `?namespace=${encodeURIComponent(namespace)}` : "";
  return api.get<SkillsListResponse>(`/v1/hub/skills${q}`);
}

export async function getSkill(
  namespace: string,
  name: string,
  version: string = "latest",
): Promise<SkillDetail> {
  if (version === "latest") {
    return api.get<SkillDetail>(`/v1/hub/skills/${namespace}/${name}`);
  }
  return api.get<SkillDetail>(`/v1/hub/skills/${namespace}/${name}/${version}`);
}

/** 下载 skill 文件 (二进制返 ArrayBuffer). 当前 page 不直接装, 留 Companion 做. */
export async function downloadSkillFile(
  namespace: string,
  name: string,
  version: string,
  filePath: string,
): Promise<string> {
  return api.get<string>(
    `/v1/hub/skills/${namespace}/${name}/${version}/files/${filePath}`,
  );
}

export interface PublishOptions {
  scope?: ShareScope;
  departments?: string[];
  note?: string;
}

/** Publish skill — multipart 多文件上传。11/08 起是「提交」: 范围 / 部门 / 说明走 query 参数,
 *  multipart 正文只放文件; 返回里多 review (status=pending) 和一句 summary。 */
export async function publishSkill(
  namespace: string,
  files: File[],
  opts: PublishOptions = {},
): Promise<{ ok: boolean; name?: string; version?: string; error?: string; review?: SkillReview; summary?: string }> {
  const fd = new FormData();
  for (const f of files) {
    fd.append("files", f, f.name);
  }
  const q = new URLSearchParams();
  if (opts.scope) q.set("scope", opts.scope);
  if (opts.departments?.length) q.set("departments", opts.departments.join(","));
  if (opts.note) q.set("note", opts.note);
  const qs = q.toString();
  try {
    const r = await api.post<{ ok: boolean; name: string; version: string; review?: SkillReview; summary?: string }>(
      `/v1/hub/skills/${encodeURIComponent(namespace)}${qs ? `?${qs}` : ""}`,
      fd,
    );
    return r;
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

/** 待我审的技能 (主管: 管的部门; admin: 全公司范围的)。 */
export const listReviewSkills = () => api.get<{ skills: SkillReview[] }>("/v1/hub/skills/review");
/** 我提交的技能 (各状态都有)。 */
export const listMySkills = () => api.get<{ skills: SkillReview[] }>("/v1/hub/skills/mine");

export const reviewSkill = (
  namespace: string, name: string, version: string, decision: "approve" | "reject", note: string,
) => api.post<SkillReview & { signature?: string }>(
  `/v1/hub/skills/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}/${encodeURIComponent(version)}/review`,
  { decision, note },
);

export const withdrawSkill = (namespace: string, name: string, version: string) =>
  api.post<SkillReview>(
    `/v1/hub/skills/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}/${encodeURIComponent(version)}/withdraw`,
  );

/** Admin 删 skill 版本. */
export async function deleteSkillVersion(
  namespace: string,
  name: string,
  version: string,
): Promise<{ ok: boolean; error?: string }> {
  try {
    return await api.delete<{ ok: boolean }>(
      `/v1/hub/skills/${namespace}/${name}/${version}`,
    );
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

export interface AuditEvent {
  ts: string;
  event: string;
  /** 11/08: skill | expert (hub 两条线合在一个 /audit 里) */
  line?: string;
  namespace?: string;
  name?: string;
  version?: string;
  by_user?: string;
  /** 专家包那条线写的是 by / id, 不是 by_user / name */
  by?: string;
  id?: string;
  note?: string;
  [k: string]: unknown;
}

export async function fetchAudit(limit: number = 100): Promise<AuditEvent[]> {
  const r = await api.get<{ events: AuditEvent[] }>(
    `/v1/hub/audit?limit=${limit}`,
  );
  return r.events || [];
}
