/** 事实补丁系统 API client — BL-Q3-FACT P0 MVP Day 2 (5/10).
 *
 * 走 gateway /api/facts/* (facts_router.py).
 * gateway 端 OIDC 验签, FactsPage 这边只管 UI.
 */

import { api } from "./api";

export type FactStatus =
  | "uploaded"
  | "extracted"
  | "analyzed"
  | "patches_ready"
  | "approved"
  | "dismissed";

export interface FactMeta {
  id: string;
  title: string;
  original_filename: string;
  raw_path: string;
  ext: string;
  size_bytes: number;
  effective_date: string | null;
  uploaded_by: string;
  uploaded_at_ms: number;
  extracted_at_ms?: number;
  analyzed_at_ms?: number;
  dismissed_at_ms?: number;
  dismissed_by?: string;
  status: FactStatus;
  impacts_count?: number;
  patches_count?: number;
}

export interface FactPoint {
  id: string;
  title: string;
  summary: string;
  category: string;
  keywords: string[];
  raw_quote: string;
  impact_scope: string;
}

export interface FactExtractResult {
  summary?: string;
  effective_date?: string | null;
  facts: FactPoint[];
  error?: string;
}

export interface SkillImpact {
  fact_id: string;
  fact_summary: string;
  skill_namespace: string;
  skill_name: string;
  confidence: number;
  reason: string;
  detection_method: string;
}

export interface SkillPatch {
  fact_id: string;
  skill_namespace: string;
  skill_name: string;
  skill_version_base: string;
  confidence: number;
  rationale: string;
  changes: Array<{
    description: string;
    old_snippet: string;
    new_snippet: string;
  }>;
  full_new_content: string;
  status: "pending" | "approved" | "rejected";
  generated_at_ms: number;
}

export interface FactAuditEvent {
  ts_ms: number;
  action: string;
  by_user: string;
  meta: Record<string, unknown>;
}

export interface FactDetail {
  meta: FactMeta;
  facts: FactExtractResult | null;
  impacts: SkillImpact[];
  patches: SkillPatch[];
  audit: FactAuditEvent[];
}

export interface UploadResult {
  fact_id: string;
  status: FactStatus;
  meta: FactMeta;
}

export interface AnalyzeResult {
  fact_id: string;
  status: FactStatus;
  impacts_count: number;
  patches_count: number;
  impacts: SkillImpact[];
  patches: SkillPatch[];
}

export const factsApi = {
  list: () => api.get<{ facts: FactMeta[]; count: number }>("/api/facts"),

  get: (id: string) => api.get<FactDetail>(`/api/facts/${encodeURIComponent(id)}`),

  upload: async (
    file: File,
    title: string,
    effectiveDate?: string,
  ): Promise<UploadResult> => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("title", title || "");
    fd.append("effective_date", effectiveDate || "");
    // 复用 api 的 baseURL + 加 Bearer header (api.ts 自动)
    return api.postFormData<UploadResult>("/api/facts/upload", fd);
  },

  extract: (id: string) =>
    api.post<{ fact_id: string; status: FactStatus; facts: FactExtractResult }>(
      `/api/facts/${encodeURIComponent(id)}/extract`,
      {},
    ),

  analyze: (id: string) =>
    api.post<AnalyzeResult>(`/api/facts/${encodeURIComponent(id)}/analyze`, {}),

  dismiss: (id: string) =>
    api.delete<{ fact_id: string; status: FactStatus }>(
      `/api/facts/${encodeURIComponent(id)}`,
    ),
};
