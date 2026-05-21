/** BL-ADVISOR-UI (5/21 Phase 7 第 6 步): 早安主菜卡片.
 *
 * 设计稿 §7.1: ActionCard 视觉规范.
 *   - 高优 red border / 中优 orange / 低优 gray
 *   - 选项 🅰 🅱 🅒 并列
 *   - ai_lean 角标"我倾向" 灰色
 *   - 草稿"📄 看草稿"按钮 → 调 draftOpenInEditor
 *   - 合规/政治 flag 黄底小标
 *   - 上下文引用底部小字
 *
 * 简化: 设计稿里的 OptionSelector / DraftPreview / ComplianceFlag / ContextRefLink
 * 都内联到本文件作为子组件 — 各组件 < 30 行, 不值得独立文件.
 */

import { useState } from "react";

import { decisionRecord } from "../../../lib/decisions";
import { draftOpenInEditor } from "../../../lib/drafts";
import type {
  AdvisorOption,
  ComplianceFlag,
  MainTask,
  PoliticalFlag,
} from "../../../lib/briefing_advisor";

interface ActionCardProps {
  task: MainTask;
  /** 员工选 option 后回调 — caller 可以 refresh / 折叠等 */
  onOptionSelected?: (label: string) => void;
}

export default function ActionCard({ task, onOptionSelected }: ActionCardProps) {
  const [selectedLabel, setSelectedLabel] = useState<string | null>(null);

  const accent =
    task.urgency === "high"
      ? "#c2410c"
      : task.urgency === "medium"
      ? "#2563eb"
      : "#6b7280";

  const handleSelect = async (opt: AdvisorOption) => {
    setSelectedLabel(opt.label);

    // 留痕 decisions.jsonl
    try {
      await decisionRecord({
        mainTaskId: task.id,
        taskTitle: task.title,
        optionsOffered: task.options.map((o) => ({
          label: o.label,
          tone: o.tone,
          summary: o.summary,
        })),
        aiLean: task.options.find((o) => o.aiLean)?.label,
        userChoice: opt.label,
        userAction: opt.draftPath ? "drafted_but_held" : "ignored",
        complianceFlagsAtDecision: task.complianceFlags.map((f) => f.type),
        contextRefs: task.contextRefs,
        draftPathChosen: opt.draftPath,
      });
    } catch (e) {
      console.warn("[ActionCard] decision 留痕失败:", e);
    }

    // 自动打开草稿 (员工自己改 + 复制后发)
    if (opt.draftPath) {
      try {
        await draftOpenInEditor(opt.draftPath);
      } catch (e) {
        console.warn("[ActionCard] 打开草稿失败:", e);
      }
    }

    onOptionSelected?.(opt.label);
  };

  return (
    <div
      style={{
        border: `1px solid ${accent}40`,
        borderLeft: `4px solid ${accent}`,
        borderRadius: "var(--radius-md)",
        padding: "12px 16px",
        marginBottom: 14,
        background: "var(--catfish-bg-elevated)",
      }}
    >
      {/* 标题区 */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 4 }}>
        <span
          style={{
            fontSize: 12,
            color: accent,
            fontWeight: 700,
            minWidth: 18,
          }}
        >
          {task.id}️⃣
        </span>
        <strong style={{ fontSize: 15, color: "var(--catfish-text)", flex: 1 }}>
          {task.title}
        </strong>
        <UrgencyBadge urgency={task.urgency} />
      </div>
      {task.reason && (
        <div
          style={{
            fontSize: 12,
            color: "var(--catfish-text-muted)",
            marginBottom: 10,
            paddingLeft: 26,
          }}
        >
          {task.reason}
        </div>
      )}

      {/* 选项区 */}
      {task.options.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 8 }}>
          {task.options.map((opt) => (
            <OptionRow
              key={opt.label}
              option={opt}
              selected={selectedLabel === opt.label}
              onSelect={() => void handleSelect(opt)}
            />
          ))}
        </div>
      )}

      {/* 合规 flag */}
      {task.complianceFlags.map((f, i) => (
        <ComplianceFlagBadge key={`c-${i}`} flag={f} />
      ))}

      {/* 政治 flag */}
      {task.politicalFlags.map((f, i) => (
        <PoliticalFlagBadge key={`p-${i}`} flag={f} />
      ))}

      {/* 上下文引用 */}
      {task.contextRefs.length > 0 && (
        <div
          style={{
            fontSize: 11,
            color: "var(--catfish-text-muted)",
            marginTop: 8,
            opacity: 0.8,
          }}
        >
          🔗 历史: {task.contextRefs.join(" / ")}
        </div>
      )}
    </div>
  );
}

// ─── 子组件 ────────────────────────────────────────────────────

function UrgencyBadge({ urgency }: { urgency: "high" | "medium" | "low" }) {
  const labelMap = { high: "急", medium: "中", low: "缓" } as const;
  const colorMap = { high: "#c2410c", medium: "#2563eb", low: "#6b7280" } as const;
  return (
    <span
      style={{
        fontSize: 10,
        padding: "2px 6px",
        borderRadius: 3,
        color: colorMap[urgency],
        border: `1px solid ${colorMap[urgency]}40`,
        background: `${colorMap[urgency]}10`,
      }}
    >
      {labelMap[urgency]}
    </span>
  );
}

function OptionRow({
  option,
  selected,
  onSelect,
}: {
  option: AdvisorOption;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={selected}
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 8,
        padding: "8px 10px",
        borderRadius: "var(--radius-sm)",
        border: `1px solid ${selected ? "#2563eb" : "var(--catfish-border)"}`,
        background: selected ? "rgba(37,99,235,0.08)" : "transparent",
        cursor: selected ? "default" : "pointer",
        textAlign: "left",
        fontFamily: "inherit",
        color: "var(--catfish-text)",
        fontSize: 13,
        width: "100%",
      }}
    >
      <span style={{ fontWeight: 700, minWidth: 18 }}>{option.label}</span>
      <span style={{ flex: 1, lineHeight: 1.5 }}>{option.summary}</span>
      {option.aiLean && (
        <span
          style={{
            fontSize: 10,
            color: "var(--catfish-text-muted)",
            opacity: 0.8,
          }}
        >
          我倾向
        </span>
      )}
      {option.draftPath && (
        <span
          style={{
            fontSize: 11,
            color: "#2563eb",
            opacity: 0.8,
          }}
        >
          📄 草稿
        </span>
      )}
      {selected && (
        <span style={{ fontSize: 11, color: "#2563eb" }}>✓ 已选 + 打开草稿</span>
      )}
    </button>
  );
}

function ComplianceFlagBadge({ flag }: { flag: ComplianceFlag }) {
  const sevColor =
    flag.severity === "high" ? "#dc2626" : flag.severity === "medium" ? "#d97706" : "#6b7280";
  return (
    <div
      style={{
        fontSize: 11,
        color: sevColor,
        background: `${sevColor}10`,
        border: `1px solid ${sevColor}30`,
        borderRadius: "var(--radius-sm)",
        padding: "4px 8px",
        marginTop: 6,
        lineHeight: 1.5,
      }}
      title={flag.suggestion}
    >
      ⚠️ 合规 ({flag.severity}): {flag.reason}
      {flag.suggestion && (
        <div style={{ marginTop: 2, fontSize: 11, opacity: 0.9 }}>
          建议: {flag.suggestion}
        </div>
      )}
    </div>
  );
}

function PoliticalFlagBadge({ flag }: { flag: PoliticalFlag }) {
  const sevColor =
    flag.severity === "high" ? "#dc2626" : flag.severity === "medium" ? "#d97706" : "#6b7280";
  const isAdvisory = flag.advisoryOnly === true;
  return (
    <div
      style={{
        fontSize: 11,
        color: sevColor,
        background: `${sevColor}10`,
        border: `1px solid ${sevColor}30`,
        borderRadius: "var(--radius-sm)",
        padding: "4px 8px",
        marginTop: 6,
        lineHeight: 1.5,
      }}
    >
      🎯 {isAdvisory ? "提醒人工核对" : "措辞"} ({flag.severity})
      {flag.person && <span>: 对 {flag.person}</span>}
      <div style={{ marginTop: 2, opacity: 0.9 }}>{flag.reason}</div>
      {!isAdvisory && flag.suggestedPhrasings && flag.suggestedPhrasings.length > 0 && (
        <div style={{ marginTop: 2, fontSize: 11, opacity: 0.9 }}>
          建议改: {flag.suggestedPhrasings.join(" / ")}
        </div>
      )}
    </div>
  );
}
