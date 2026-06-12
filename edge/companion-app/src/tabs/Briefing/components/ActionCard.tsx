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

import {
  advisorTaskStateClear,
  advisorTaskStateSet,
  type TaskStatus,
} from "../../../lib/advisor_cache";
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
  /** 5/22 鸿波: 任务级状态 (done/snoozed/ignored). null = pending. */
  initialStatus?: TaskStatus | null;
  /** 5/22 鸿波: 昨天 snoozed 今天又出来的, 顶上加角标 "⏰ 昨天推的" */
  wasSnoozedYesterday?: boolean;
  /** 员工选 option 后回调 — caller 可以 refresh / 折叠等 */
  onOptionSelected?: (label: string) => void;
  /** 5/22 鸿波: 员工切状态后回调 — caller 可以从列表过滤掉 */
  onStatusChange?: (status: TaskStatus | null) => void;
}

export default function ActionCard({
  task,
  initialStatus = null,
  wasSnoozedYesterday = false,
  onOptionSelected,
  onStatusChange,
}: ActionCardProps) {
  const [selectedLabel, setSelectedLabel] = useState<string | null>(null);
  const [status, setStatus] = useState<TaskStatus | null>(initialStatus);

  const accent =
    task.urgency === "high"
      ? "#c2410c"
      : task.urgency === "medium"
      ? "#2563eb"
      : "#6b7280";

  // 5/22 鸿波 二修: "我没看到打开什么编辑器" — draftOpenInEditor 失败被 console.warn 吞了.
  // 改: 失败把错存到 selectedOpenError, UI 显红条让员工看到原因 (常见: LLM 编了 draftPath
  // 但没真调 catfish_draft_email_reply 落盘 → 文件不存在 → Rust open 命令报错).
  const [selectedOpenError, setSelectedOpenError] = useState<string | null>(null);
  const [selectedOpenedPath, setSelectedOpenedPath] = useState<string | null>(null);

  const handleSelect = async (opt: AdvisorOption) => {
    setSelectedLabel(opt.label);
    setSelectedOpenError(null);
    setSelectedOpenedPath(null);

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
        setSelectedOpenedPath(opt.draftPath);
        console.log("[ActionCard] 草稿已打开:", opt.draftPath);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        console.error("[ActionCard] 打开草稿失败:", msg, "draftPath:", opt.draftPath);
        // 常见原因猜测让员工知道下一步:
        let hint = "";
        if (msg.includes("不在 outputs/") || msg.includes("路径含")) {
          hint = " (LLM 编了不合法路径)";
        } else if (msg.toLowerCase().includes("no such file") || msg.includes("不存在")) {
          hint = " (LLM 给了 draftPath 但没真调 draft_email_reply 落盘, 文件不存在)";
        }
        setSelectedOpenError(`${msg}${hint}\n路径: ${opt.draftPath}`);
      }
    }

    onOptionSelected?.(opt.label);
  };

  // 5/22 鸿波: 切任务状态 + 持久化 + 通知 parent
  // 5/22 二修 — 鸿波反馈"标记完成无效":
  //   1. setStatus 立即同步触发, UI 切折叠态不依赖 backend 成功
  //   2. backend 挂了用 setBackendError 弹个红条让员工看到 (不再 console.warn 静默)
  //   3. console.log 标"点击进入"让员工 DevTools 能确认 onClick 真触发了
  const [backendError, setBackendError] = useState<string | null>(null);
  const handleStatusChange = async (newStatus: TaskStatus | null) => {
    console.log("[ActionCard] handleStatusChange 进入:", {
      title: task.title,
      newStatus,
      currentStatus: status,
    });
    setBackendError(null);
    setStatus(newStatus);  // 同步: UI 立刻切换 (不等 backend)
    try {
      if (newStatus === null) {
        await advisorTaskStateClear(task.title);
      } else {
        await advisorTaskStateSet(task.title, newStatus);
      }
      console.log("[ActionCard] backend 持久化 OK:", { title: task.title, newStatus });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      console.error("[ActionCard] 后端持久化失败 (UI 仍切了, 但重启 Companion 会丢):", msg);
      setBackendError(
        `状态没存到 ~/.catfish/advisor_task_state.json (可能 Rust 后端没重 build). 错: ${msg.slice(0, 100)}`,
      );
    }
    onStatusChange?.(newStatus);
  };

  // ─── 状态下的视觉降级 ───
  // done: 整卡折叠成一行 + ✓ + "撤销"
  // snoozed: 整卡变灰 + ⏰ + "撤销"
  // ignored: 整卡完全藏 (parent 过滤)
  if (status === "done") {
    return (
      <>
        <CollapsedRow
          accent="#16a34a"
          icon="✓"
          title={task.title}
          statusText="已完成"
          onUndo={() => void handleStatusChange(null)}
        />
        {backendError && <BackendErrorRow msg={backendError} />}
      </>
    );
  }
  if (status === "snoozed") {
    return (
      <>
        <CollapsedRow
          accent="#6b7280"
          icon="⏰"
          title={task.title}
          statusText="已推迟到明天"
          onUndo={() => void handleStatusChange(null)}
        />
        {backendError && <BackendErrorRow msg={backendError} />}
      </>
    );
  }

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
      {/* 标题区 — 5/22 鸿波: 数字 emoji 在 macOS 渲染像 checkbox 误导, 改成圆形序号 */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 4 }}>
        <span
          style={{
            fontSize: 12,
            color: accent,
            fontWeight: 700,
            background: `${accent}15`,
            borderRadius: "50%",
            minWidth: 22,
            height: 22,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          {task.id}
        </span>
        <strong style={{ fontSize: 15, color: "var(--catfish-text)", flex: 1 }}>
          {task.title}
        </strong>
        {wasSnoozedYesterday && (
          <span
            style={{
              fontSize: 10,
              padding: "2px 6px",
              borderRadius: 3,
              color: "#a16207",
              border: "1px solid #a1620740",
              background: "#a1620710",
            }}
            title="昨天你推迟过这条, 今天又出来了"
          >
            ⏰ 昨天推的
          </span>
        )}
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
              openError={selectedLabel === opt.label ? selectedOpenError : null}
              openedPath={selectedLabel === opt.label ? selectedOpenedPath : null}
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

      {/* 5/22 鸿波: 任务级状态切换 — 已完成 / 推迟 / 不做 */}
      <div
        style={{
          display: "flex",
          gap: 8,
          marginTop: 10,
          paddingTop: 8,
          borderTop: "1px dashed var(--catfish-border)",
          justifyContent: "flex-end",
        }}
      >
        <StatusButton
          icon="✓"
          label="标记完成"
          color="#16a34a"
          onClick={() => void handleStatusChange("done")}
        />
        <StatusButton
          icon="⏰"
          label="推迟到明天"
          color="#a16207"
          onClick={() => void handleStatusChange("snoozed")}
        />
        <StatusButton
          icon="✗"
          label="不做"
          color="#6b7280"
          onClick={() => void handleStatusChange("ignored")}
        />
      </div>
    </div>
  );
}

// ─── 折叠态 (done / snoozed) ───────────────────────────────────

function CollapsedRow({
  accent,
  icon,
  title,
  statusText,
  onUndo,
}: {
  accent: string;
  icon: string;
  title: string;
  statusText: string;
  onUndo: () => void;
}) {
  return (
    <div
      style={{
        border: `1px solid ${accent}30`,
        borderLeft: `4px solid ${accent}`,
        borderRadius: "var(--radius-md)",
        padding: "8px 14px",
        marginBottom: 8,
        background: "var(--catfish-bg-elevated)",
        opacity: 0.6,
        display: "flex",
        alignItems: "center",
        gap: 10,
      }}
    >
      <span style={{ color: accent, fontSize: 14 }}>{icon}</span>
      <span
        style={{
          flex: 1,
          fontSize: 13,
          color: "var(--catfish-text)",
          textDecoration: icon === "✓" ? "line-through" : "none",
        }}
      >
        {title}
      </span>
      <span style={{ fontSize: 11, color: accent }}>{statusText}</span>
      <button
        type="button"
        onClick={onUndo}
        style={{
          fontSize: 11,
          padding: "2px 8px",
          borderRadius: 3,
          border: "1px solid var(--catfish-border)",
          background: "transparent",
          color: "var(--catfish-text-muted)",
          cursor: "pointer",
        }}
      >
        撤销
      </button>
    </div>
  );
}

function BackendErrorRow({ msg }: { msg: string }) {
  return (
    <div
      style={{
        fontSize: 11,
        color: "#dc2626",
        background: "#dc262610",
        border: "1px solid #dc262640",
        borderRadius: 4,
        padding: "6px 10px",
        marginBottom: 8,
        marginTop: -4,
      }}
    >
      ⚠️ {msg}
    </div>
  );
}

function StatusButton({
  icon,
  label,
  color,
  onClick,
}: {
  icon: string;
  label: string;
  color: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        fontSize: 12,
        padding: "4px 10px",
        borderRadius: 4,
        border: `1px solid ${color}40`,
        background: "transparent",
        color,
        cursor: "pointer",
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
      }}
    >
      <span>{icon}</span>
      <span>{label}</span>
    </button>
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

/** 5/22 鸿波: 按文件名分草稿类型, 显示具体词不显糊"草稿".
 *  reply-*.md → 邮件草稿 / meeting-brief-*.md → 汇报草稿 / 其它 → 草稿 */
function draftKindOf(draftPath: string | undefined): { icon: string; label: string } | null {
  if (!draftPath) return null;
  const fname = draftPath.split("/").pop() ?? "";
  if (fname.startsWith("reply-")) return { icon: "📧", label: "邮件草稿" };
  if (fname.startsWith("meeting-brief-")) return { icon: "📋", label: "汇报草稿" };
  return { icon: "📄", label: "草稿" };
}

function OptionRow({
  option,
  selected,
  openError,
  openedPath,
  onSelect,
}: {
  option: AdvisorOption;
  selected: boolean;
  /** 5/22 鸿波 二修: 上层 handleSelect 失败时把 Rust open 错传下来显红条, 不再静默 */
  openError: string | null;
  /** 5/22 鸿波 二修: 真成功打开时记下路径, 让员工知道文件在哪 (open 命令在某些
   *  Mac 上虽然返 0 但没真弹编辑器 — 路径展示让员工能手动复制粘贴去 Finder) */
  openedPath: string | null;
  onSelect: () => void;
}) {
  const draft = draftKindOf(option.draftPath);
  // 5/22 鸿波: hover/选中时的说人话提示 — 让员工知道点了会发生什么
  const hint = draft
    ? `点 = 打开${draft.label} (${option.draftPath}). 你审完手动复制到邮件/Word 发出. catfish 不替你发.`
    : `点 = 只记下你选了 ${option.label}, 不开任何文件. (这个口径 catfish 没起草, 自己写)`;

  return (
    <div style={{ width: "100%" }}>
      <button
        type="button"
        onClick={onSelect}
        disabled={selected}
        title={hint}
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
        {draft && (
          <span
            style={{
              fontSize: 11,
              color: "#2563eb",
              opacity: 0.85,
              padding: "1px 6px",
              border: "1px solid #2563eb40",
              borderRadius: 3,
              background: "#2563eb08",
            }}
            title={hint}
          >
            {draft.icon} 选 = 打开{draft.label}
          </span>
        )}
        {!draft && (
          <span
            style={{
              fontSize: 10,
              color: "var(--catfish-text-muted)",
              opacity: 0.7,
            }}
            title={hint}
          >
            (无草稿, 自己写)
          </span>
        )}
      </button>
      {/* 5/22 鸿波: 选中后底部 hint — 区分 3 态: 出错 / 成功开了 / 没草稿只记选项 */}
      {selected && openError && (
        <div
          style={{
            fontSize: 11,
            color: "#dc2626",
            background: "#dc262610",
            border: "1px solid #dc262640",
            borderTop: "none",
            borderRadius: "0 0 4px 4px",
            padding: "6px 10px",
            lineHeight: 1.5,
            whiteSpace: "pre-wrap",
          }}
        >
          ⚠️ 草稿打开失败:{"\n"}{openError}
          {"\n\n"}
          <strong>替代方案</strong>: 复制上面的路径 → Finder Cmd+Shift+G 粘贴 → 自己打开看. 或在 chat 让 catfish 重新起草.
        </div>
      )}
      {selected && !openError && draft && (
        <div
          style={{
            fontSize: 11,
            color: "var(--catfish-text-muted)",
            background: "rgba(37,99,235,0.05)",
            border: "1px dashed rgba(37,99,235,0.3)",
            borderTop: "none",
            borderRadius: "0 0 4px 4px",
            padding: "6px 10px",
            lineHeight: 1.5,
          }}
        >
          ✓ 已选 — {draft.label}已打开 (默认编辑器, 没弹出来检查 Finder 默认 .md 关联应用).
          {openedPath && (
            <div style={{ marginTop: 4, opacity: 0.85 }}>
              📂 文件路径: <code style={{ fontSize: 10 }}>{openedPath}</code>
            </div>
          )}
          <div style={{ marginTop: 4 }}>
            <strong>你审完手动复制到
              {draft.label === "邮件草稿" ? " Outlook / Mail.app " : " Word / 邮件 "}
              发出.</strong> catfish 不替你发, 这是产品红线.
          </div>
        </div>
      )}
      {/* P3.3.42 (6/12 鸿波 "不要出这个提示，遮挡对话，耽误事"): 砍 "已记下你选了 X" 蓝条
       *  decisionRecord 已留痕到 decisions.jsonl, UI 提示纯冗余. 选了的视觉态由
       *  OptionRow 的 selected 边框/背景表达即可. */}
    </div>
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
