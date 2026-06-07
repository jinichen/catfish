/** Advisory admin API client — 6/7 BL-MANIFESTO-ADVISORY-PHASE2.
 *
 * 走 central/llm-gateway/src/catfish_gateway/advisory_router.py
 * 跟 facts.ts 同模板 (走 api.ts 的 Bearer Authorization 鉴权).
 */

import { api } from "./api";

export type AdvisorySeverity = "critical" | "high" | "medium" | "low" | "info";

export type AdvisoryCategory =
  | "skill_vulnerability"
  | "mcp_vulnerability"
  | "catfish_update"
  | "policy_recommendation"
  | "external_status"
  | "deprecation_notice";

export interface AdvisoryTarget {
  skill?: string;
  skill_version_pattern?: string;
  mcp_name?: string;
  catfish_version_pattern?: string;
}

export interface RemediationAction {
  label: string;
  kind: string;
  params?: Record<string, unknown>;
}

export interface Advisory {
  id: string;
  severity: AdvisorySeverity;
  category: AdvisoryCategory;
  title: string;
  description?: string;
  recommendation?: string;
  target?: AdvisoryTarget;
  references?: string[];
  tags?: string[];
  remediation_actions?: RemediationAction[];
  published: string;
  expires?: string;
  published_by?: string;
  revoked_at?: string;
  revoked_by?: string;
}

export interface AdvisoryListResp {
  advisories: Advisory[];
  count: number;
}

export const advisoryApi = {
  /** GET /api/admin/advisory — admin 看全部 (含 revoked). */
  list: (includeRevoked = true) =>
    api.get<AdvisoryListResp>(
      `/api/admin/advisory?include_revoked=${includeRevoked}`,
    ),

  /** POST /api/admin/advisory — sysadmin publish. */
  publish: (
    advisory: Omit<Advisory, "published_by" | "revoked_at" | "revoked_by">,
  ) => api.post<{ ok: boolean; id: string }>("/api/admin/advisory", advisory),

  /** DELETE /api/admin/advisory/:id — sysadmin revoke. */
  revoke: (id: string) =>
    api.delete<{ ok: boolean; id: string }>(
      `/api/admin/advisory/${encodeURIComponent(id)}`,
    ),
};
