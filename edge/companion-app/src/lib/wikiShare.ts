/** 分享到部门: 员工在对话框里勾了什么 → 依次发哪几页、要不要先确认警告 (11/09)。纯函数, 界面在 WikiShareDialog。
 *
 * 顺序: 原件 → 一起分享的关联页 → 这一页。原件先上去, 后面几页里的 [[原件 · 名称]] 同事装的时候才连得上。
 * 每一页都带同一份 attached_sources (勾了的原件 + 部门里已经有的原件): 关联页引用同一份原件时也改成链接。
 */
import type { WikiSharePlan, WikiShareScan, WikiShareScope, WikiShareWarning } from "./tauri_wiki";

/** 11/09 按分享范围重算「同事那边点不点得开」。
 *
 * 部门: 预检原样 (我能看到的 = 同部门同事能看到的: 本部门的 + 全公司可见的)。
 * 全公司: 只有全公司可见的 (管理员已通过) 才算别的部门点得开;
 *   我发过但只在部门里的关联页 → 可以勾「一起分享」(重发成全公司, 一样等管理员审);
 *   别人发的、只在他部门里的 → dept_only, 别的部门点不开, 我也没法替他发。
 */
export function scopePlan(plan: WikiSharePlan, scope: WikiShareScope): WikiSharePlan {
  if (scope === "department") return plan;
  return {
    ...plan,
    sources: plan.sources.map((s) => (s.kind === "raw" ? { ...s, shared: !!s.shared_company } : s)),
    relations: plan.relations.map((r) => {
      if (r.status !== "shared" || r.company) return r;
      return r.mine && r.rel_path ? { ...r, status: "local" as const } : { ...r, status: "dept_only" as const };
    }),
  };
}

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

/** plan 先过 scopePlan; 每一页都带同一个 scope (原件、关联页跟这一页同一个范围) */
export function shareSteps(
  plan: WikiSharePlan,
  choice: ShareChoice,
  ackWarnings: boolean,
  scope: WikiShareScope = "department",
): ShareStep[] {
  const attached = attachedKeys(plan, choice);
  const steps: ShareStep[] = [];
  for (const s of plan.sources) {
    if (s.kind !== "raw" || !s.key || !s.publish_path || !s.attachable || !choice.attach.has(s.key)) continue;
    steps.push({
      label: s.title ?? s.key,
      kind: "source",
      args: { wiki_rel_path: s.publish_path, acknowledge_warnings: ackWarnings, scope },
      scan: s.scan,
    });
  }
  for (const r of plan.relations) {
    if (r.status !== "local" || !r.rel_path || !choice.together.has(r.rel_path)) continue;
    steps.push({
      label: r.title ?? r.name,
      kind: "related",
      args: { wiki_rel_path: r.rel_path, acknowledge_warnings: ackWarnings, attached_sources: attached, scope },
      scan: r.scan,
    });
  }
  steps.push({
    label: plan.title,
    kind: "main",
    args: { wiki_rel_path: plan.rel_path, acknowledge_warnings: ackWarnings, attached_sources: attached, scope },
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
