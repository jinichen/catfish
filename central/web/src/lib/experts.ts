/** 专家包 (10/18, docs/EXPERT-PACK-PLAN.md §5): 资源市场列表 / 我提交的 / 待审核 / 审核。走 gateway /v1/hub/experts/*。
 *
 * 门户里不装 (装在 Companion 里, 要验签、落 profile); 这里看、审。
 */
import { api } from "./api";

export interface ExpertStep {
  id: string;
  block: "skill" | "action" | "xiaonian";
  what: string;
  confirm: boolean;
}

export interface ExpertPackMeta {
  id: string;
  version: string;
  name: string;
  description: string;
  routes: { when: string; use: string; ask?: string[] }[];
  checks: { after?: string; verify: string }[];
  tool_groups: string[];
  allow_web: boolean;
  allow_code: boolean;
  quick_prompts: string[];
  /** 打包时算的权限摘要 (Companion 装之前会按包内容再算一遍) */
  summary: {
    read?: string[]; local?: string[]; outward?: string[]; browser?: boolean; sites?: string[];
    /** 对外积木的名字: 跑到这一步一律停下等员工确认 */
    outward_actions?: string[];
  };
  /** 服务器自己从包里拆出来的: 每个工作流的步骤 */
  workflows: Record<string, ExpertStep[]>;
  skills: string[];
  /** 服务器自己从脚本 / 说明里扫出来的网址 */
  sites: string[];
  file_count: number;
  scope: "department" | "company";
  departments: string[];
  sha256: string;
  size: number;
  submitted_by: string;
  submitted_dept: string;
  submitted_at: string;
  submit_note: string;
  status: "pending" | "approved" | "rejected" | "withdrawn";
  reviewed_by?: string;
  reviewed_at?: string;
  review_note?: string;
  signature?: string;
}

type List = { experts: ExpertPackMeta[] };

export const listMarketExperts = () => api.get<List>("/v1/hub/experts");
export const listMyExperts = () => api.get<List>("/v1/hub/experts/mine");
export const listReviewExperts = () => api.get<List>("/v1/hub/experts/review");
export const reviewExpert = (id: string, version: string, decision: "approve" | "reject", note: string) =>
  api.post<ExpertPackMeta>(`/v1/hub/experts/${id}/${version}/review`, { decision, note });
export const withdrawExpert = (id: string, version: string) =>
  api.post<ExpertPackMeta>(`/v1/hub/experts/${id}/${version}/withdraw`);

export const STATUS_LABEL: Record<ExpertPackMeta["status"], string> = {
  pending: "待审核", approved: "已上架", rejected: "被驳回", withdrawn: "已撤回",
};

export const BLOCK_LABEL: Record<ExpertStep["block"], string> = { skill: "技能", action: "积木", xiaonian: "小鲶判断" };
