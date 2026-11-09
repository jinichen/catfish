/** 分享到部门: 员工在对话框里勾了什么 → 依次发哪几页、要不要先确认警告 (11/09)。纯函数, 界面在 WikiShareDialog。
 *
 * 顺序: 原件 → 一起分享的关联页 → 这一页。原件先上去, 后面几页里的 [[原件 · 名称]] 同事装的时候才连得上。
 * 每一页都带同一份 attached_sources (勾了的原件 + 部门里已经有的原件): 关联页引用同一份原件时也改成链接。
 */
import type { WikiSharePlan, WikiShareScan, WikiShareWarning } from "./tauri_wiki";

export interface ShareChoice {
  /** 勾了「把原件也分享」的来源 (source.key) */
  attach: Set<string>;
  /** 勾了「一起分享」的关联页 (rel_path) */
  together: Set<string>;
}

export interface ShareStep {
  /** 界面上这一行叫什么 */
  label: string;
  kind: "source" | "related" | "main";
  args: Record<string, unknown>;
  scan: WikiShareScan | null | undefined;
}

/** 发出去那几页里要改成链接的原件: 勾了的 + 部门里本来就有的 */
export function attachedKeys(plan: WikiSharePlan, choice: ShareChoice): string[] {
  return plan.sources
    .filter((s) => s.kind === "raw" && s.key && (choice.attach.has(s.key) || s.shared))
    .map((s) => s.key!);
}

export function shareSteps(plan: WikiSharePlan, choice: ShareChoice, ackWarnings: boolean): ShareStep[] {
  const attached = attachedKeys(plan, choice);
  const steps: ShareStep[] = [];
  for (const s of plan.sources) {
    if (s.kind !== "raw" || !s.key || !s.publish_path || !s.attachable || !choice.attach.has(s.key)) continue;
    steps.push({
      label: s.title ?? s.key,
      kind: "source",
      args: { wiki_rel_path: s.publish_path, acknowledge_warnings: ackWarnings },
      scan: s.scan,
    });
  }
  for (const r of plan.relations) {
    if (r.status !== "local" || !r.rel_path || !choice.together.has(r.rel_path)) continue;
    steps.push({
      label: r.title ?? r.name,
      kind: "related",
      args: { wiki_rel_path: r.rel_path, acknowledge_warnings: ackWarnings, attached_sources: attached },
      scan: r.scan,
    });
  }
  steps.push({
    label: plan.title,
    kind: "main",
    args: { wiki_rel_path: plan.rel_path, acknowledge_warnings: ackWarnings, attached_sources: attached },
    scan: plan.scan,
  });
  return steps;
}

/** 这次要发的几页里, 哪些有警告 (要员工看过点确认) / 哪些拒发 (含凭据) */
export function shareIssues(steps: ShareStep[]): {
  warned: Array<{ label: string; warnings: WikiShareWarning[] }>;
  blocked: Array<{ label: string; reason: string }>;
} {
  const warned: Array<{ label: string; warnings: WikiShareWarning[] }> = [];
  const blocked: Array<{ label: string; reason: string }> = [];
  for (const s of steps) {
    if (s.scan?.blocked) blocked.push({ label: s.label, reason: s.scan.blocked });
    else if (s.scan?.warnings.length) warned.push({ label: s.label, warnings: s.scan.warnings });
  }
  return { warned, blocked };
}

const CATEGORY: Record<string, string> = {
  pii: "个人信息 (身份证 / 手机 / 工号 / 银行卡)",
  intranet: "内网地址",
  sensitive_terms: "你设的敏感词",
};
export const warningLabel = (w: WikiShareWarning) => `${CATEGORY[w.category] ?? w.category} ${w.hits.length} 处`;

export const sizeLabel = (n?: number) => (!n ? "" : n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);
