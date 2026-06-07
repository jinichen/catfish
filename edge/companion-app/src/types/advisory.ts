/** Advisory feed types — 6/7 BL-MANIFESTO-ADVISORY-PHASE1.
 *
 * 跟 docs/ADVISORY-FEED-SPEC.md §2 一致.
 *
 * 服务端 schema 见 central/llm-gateway/config/advisories.yaml.
 */

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
  skillVersionPattern?: string;
  mcpName?: string;
  catfishVersionPattern?: string;
}

export type RemediationKind =
  | "uninstall_skill"
  | "install_skill"
  | "uninstall_and_install_skill"
  | "update_catfish"
  | "open_settings"
  | "open_url";

export interface RemediationAction {
  label: string;
  kind: RemediationKind;
  params?: Record<string, unknown>;
}

export interface Advisory {
  id: string;
  severity: AdvisorySeverity;
  category: AdvisoryCategory;
  target?: AdvisoryTarget;
  title: string;
  description?: string;
  recommendation?: string;
  published: string;
  expires?: string;
  remediation_actions?: RemediationAction[];
  references?: string[];
  tags?: string[];
}

export interface AdvisoryFeedResponse {
  version: string;
  generated_at: string;
  advisories: Advisory[];
  metadata: {
    total: number;
    active_count: number;
    expired_count: number;
  };
}

export type LocalAdvisoryStatus =
  | "unseen"
  | "seen"
  | "snoozed"
  | "acked"
  | "dismissed";

export interface AdvisoryLocalState {
  advisoryId: string;
  status: LocalAdvisoryStatus;
  lastShown?: string;
  snoozeUntil?: string;
  ackedAt?: string;
  uploadConsent: boolean;
}
