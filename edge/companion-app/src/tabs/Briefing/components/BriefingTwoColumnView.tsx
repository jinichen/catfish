/** P3.3.6 (2026-06-10 鸿波): 早安 tab 左右两栏 view.
 *
 * 替代 ActionCard 平铺 — left sidebar 按 urgency 分类 (急/中/低/已默认处理),
 * 右侧详情区显 选中 task 的: 目标 (reason) / 当前进展 (contextRefs + flags) /
 * 建议 (options + aiLean + complianceFlag) / 行动按钮.
 *
 * 数据复用现有 MainTask / HandledSilentlyItem, 0 后端改动. 行动按钮逻辑直接
 * 内联 (跟 ActionCard 一致: setStatus + advisorTaskStateSet/Clear + onStatusChange).
 */

import { useEffect, useMemo, useState } from "react";

import {
  advisorTaskStateClear,
  advisorTaskStateSet,
  type TaskStateFetch,
  type TaskStatus,
} from "../../../lib/advisor_cache";
import { decisionRecord } from "../../../lib/decisions";
import { draftOpenInEditor } from "../../../lib/drafts";
import type {
  AdvisorOption,
  ComplianceFlag,
  HandledSilentlyItem,
  MainTask,
  PoliticalFlag,
} from "../../../lib/briefing_advisor";

interface BriefingTwoColumnViewProps {
  tasks: MainTask[];                                  // 已 filter ignored
  handledItems: HandledSilentlyItem[];
  taskState: TaskStateFetch;
  wasSnoozedYesterday: (title: string) => boolean;
  onStatusChange: (taskTitle: string, status: TaskStatus | null) => void;
}

type UrgencyGroup = "high" | "medium" | "low";

const URGENCY_META: Record<UrgencyGroup, { label: string; color: string; bg: string }> = {
  high:   { label: "急", color: "#c2410c", bg: "rgba(194,65,12,0.08)" },
  medium: { label: "中", color: "#a16207", bg: "rgba(161,98,7,0.08)" },
  low:    { label: "低", color: "#6b7280", bg: "rgba(107,114,128,0.06)" },
};

export default function BriefingTwoColumnView({
  tasks,
  handledItems,
  taskState,
  wasSnoozedYesterday,
  onStatusChange,
}: BriefingTwoColumnViewProps) {
  // 按 urgency 分组
  const grouped = useMemo(() => {
    const g: Record<UrgencyGroup, MainTask[]> = { high: [], medium: [], low: [] };
    for (const t of tasks) {
      const u = (t.urgency === "high" || t.urgency === "medium") ? t.urgency : "low";
      g[u].push(t);
    }
    return g;
  }, [tasks]);

  // 默认选中第一个 high; 没 high 就第一个; 都没就 null
  const defaultId = tasks.length > 0
    ? (grouped.high[0]?.id ?? grouped.medium[0]?.id ?? grouped.low[0]?.id ?? null)
    : null;
  const [selectedId, setSelectedId] = useState<number | null>(defaultId);

  // tasks 变了 (refresh) 同步默认选中
  useEffect(() => {
    if (selectedId == null || !tasks.find((t) => t.id === selectedId)) {
      setSelectedId(defaultId);
    }
  }, [defaultId, tasks, selectedId]);

  const selected = tasks.find((t) => t.id === selectedId) ?? null;

  if (tasks.length === 0 && handledItems.length === 0) {
    return (
      <div className="briefing-2col__empty">
        今天的主菜都处理完了, 喝杯茶吧 ☕
      </div>
    );
  }

  return (
    <div className="briefing-2col">
      <aside className="briefing-2col__sidebar">
        <div className="briefing-2col__sidebar-label">分类</div>

        {(["high", "medium", "low"] as UrgencyGroup[]).map((u) => {
          const items = grouped[u];
          if (items.length === 0) return null;
          const meta = URGENCY_META[u];
          return (
            <div key={u} style={{ marginBottom: 8 }}>
              <div
                className="briefing-2col__group-header"
                style={{ color: meta.color }}
              >
                {meta.label} · {items.length}
              </div>
              {items.map((t) => {
                const status = taskState.today[t.title]?.status as TaskStatus | undefined;
                const isSelected = t.id === selectedId;
                return (
                  <button
                    key={t.id}
                    type="button"
                    className={
                      "briefing-2col__task-row" +
                      (isSelected ? " briefing-2col__task-row--selected" : "") +
                      (status === "done" ? " briefing-2col__task-row--done" : "") +
                      (status === "snoozed" ? " briefing-2col__task-row--snoozed" : "")
                    }
                    style={isSelected ? { background: meta.bg, borderLeftColor: meta.color } : undefined}
                    onClick={() => setSelectedId(t.id)}
                  >
                    <div className="briefing-2col__task-title">
                      {status === "done" && "✓ "}
                      {status === "snoozed" && "⏰ "}
                      {t.title}
                    </div>
                    {t.reason && (
                      <div className="briefing-2col__task-subtitle">
                        {t.reason.length > 24 ? t.reason.slice(0, 24) + "…" : t.reason}
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          );
        })}

        {handledItems.length > 0 && (
          <div className="briefing-2col__handled">
            <div className="briefing-2col__group-header" style={{ color: "var(--catfish-text-muted)" }}>
              已默认处理 · {handledItems.length}
            </div>
            {handledItems.map((h, i) => (
              <div key={i} className="briefing-2col__handled-row">
                {h.category} {h.count > 0 && <span style={{ opacity: 0.7 }}>· {h.count}</span>}
              </div>
            ))}
          </div>
        )}
      </aside>

      <main className="briefing-2col__detail">
        {selected ? (
          <DetailPane
            task={selected}
            status={(taskState.today[selected.title]?.status ?? null) as TaskStatus | null}
            wasSnoozedYesterday={wasSnoozedYesterday(selected.title)}
            onStatusChange={(s) => onStatusChange(selected.title, s)}
          />
        ) : (
          <div className="briefing-2col__detail-empty">选个待办看详情</div>
        )}
      </main>
    </div>
  );
}

// ─── 右侧详情 ──────────────────────────────────────────────────────

function DetailPane({
  task,
  status,
  wasSnoozedYesterday,
  onStatusChange,
}: {
  task: MainTask;
  status: TaskStatus | null;
  wasSnoozedYesterday: boolean;
  onStatusChange: (s: TaskStatus | null) => void;
}) {
  const [selectedLabel, setSelectedLabel] = useState<string | null>(null);
  const [openErr, setOpenErr] = useState<string | null>(null);
  const [openedPath, setOpenedPath] = useState<string | null>(null);
  const [backendError, setBackendError] = useState<string | null>(null);

  const accent =
    task.urgency === "high" ? "#c2410c" :
    task.urgency === "medium" ? "#a16207" : "#6b7280";

  const handleSelect = async (opt: AdvisorOption) => {
    setSelectedLabel(opt.label);
    setOpenErr(null);
    setOpenedPath(null);
    try {
      await decisionRecord({
        mainTaskId: task.id,
        taskTitle: task.title,
        optionsOffered: task.options.map((o) => ({ label: o.label, tone: o.tone, summary: o.summary })),
        aiLean: task.options.find((o) => o.aiLean)?.label,
        userChoice: opt.label,
        userAction: opt.draftPath ? "drafted_but_held" : "ignored",
        complianceFlagsAtDecision: task.complianceFlags.map((f) => f.type),
        contextRefs: task.contextRefs,
        draftPathChosen: opt.draftPath,
      });
    } catch (e) {
      console.warn("[BriefingTwoColumn] decision 留痕失败:", e);
    }
    if (opt.draftPath) {
      try {
        await draftOpenInEditor(opt.draftPath);
        setOpenedPath(opt.draftPath);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        let hint = "";
        if (msg.includes("不在 outputs/") || msg.includes("路径含")) {
          hint = " (LLM 编了不合法路径)";
        } else if (msg.toLowerCase().includes("no such file") || msg.includes("不存在")) {
          hint = " (LLM 给了 draftPath 但没真调 draft tool 落盘)";
        }
        setOpenErr(`${msg}${hint}\n路径: ${opt.draftPath}`);
      }
    }
  };

  const handleStatusChange = async (s: TaskStatus | null) => {
    setBackendError(null);
    try {
      if (s === null) await advisorTaskStateClear(task.title);
      else await advisorTaskStateSet(task.title, s);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setBackendError(`状态没存 (Rust 后端没 build?): ${msg.slice(0, 100)}`);
    }
    onStatusChange(s);
  };

  return (
    <div>
      {/* 标题区 */}
      <div className="briefing-2col__detail-header" style={{ borderLeftColor: accent }}>
        <div className="briefing-2col__detail-badges">
          <span style={{ color: accent, fontSize: 12, fontWeight: 600 }}>
            {task.urgency === "high" ? "急" : task.urgency === "medium" ? "中" : "低"}
          </span>
          {wasSnoozedYesterday && (
            <span className="briefing-2col__badge-warn">⏰ 昨天推过</span>
          )}
          {status === "done" && <span className="briefing-2col__badge-done">已完成</span>}
          {status === "snoozed" && <span className="briefing-2col__badge-warn">已推迟</span>}
        </div>
        <h3 className="briefing-2col__detail-title">{task.title}</h3>
      </div>

      {/* 目标 */}
      {task.reason && (
        <Section title="目标">
          <p className="briefing-2col__section-text">{task.reason}</p>
        </Section>
      )}

      {/* 当前进展 — contextRefs + flags */}
      {(task.contextRefs.length > 0 || task.complianceFlags.length > 0 || task.politicalFlags.length > 0) && (
        <Section title="当前进展">
          {task.contextRefs.length > 0 && (
            <p className="briefing-2col__section-text">
              <span style={{ color: "var(--catfish-text-muted)", fontSize: 11 }}>🔗 历史: </span>
              {task.contextRefs.join(" / ")}
            </p>
          )}
          {task.complianceFlags.map((f, i) => (
            <ComplianceFlagInline key={`c-${i}`} flag={f} />
          ))}
          {task.politicalFlags.map((f, i) => (
            <PoliticalFlagInline key={`p-${i}`} flag={f} />
          ))}
        </Section>
      )}

      {/* 建议 = options */}
      {task.options.length > 0 && (
        <Section title={`建议 · ${task.options.length} 个口径`}>
          {task.options.map((opt) => (
            <OptionRow
              key={opt.label}
              option={opt}
              selected={selectedLabel === opt.label}
              openError={selectedLabel === opt.label ? openErr : null}
              openedPath={selectedLabel === opt.label ? openedPath : null}
              accent={accent}
              onSelect={() => void handleSelect(opt)}
            />
          ))}
        </Section>
      )}

      {/* 行动按钮 */}
      <div className="briefing-2col__actions">
        {status === "done" ? (
          <button type="button" className="briefing-2col__action-btn" onClick={() => void handleStatusChange(null)}>
            撤销完成
          </button>
        ) : status === "snoozed" ? (
          <button type="button" className="briefing-2col__action-btn" onClick={() => void handleStatusChange(null)}>
            撤销推迟
          </button>
        ) : (
          <>
            <button
              type="button"
              className="briefing-2col__action-btn briefing-2col__action-btn--done"
              onClick={() => void handleStatusChange("done")}
            >
              ✓ 标记完成
            </button>
            <button
              type="button"
              className="briefing-2col__action-btn briefing-2col__action-btn--snooze"
              onClick={() => void handleStatusChange("snoozed")}
            >
              ⏰ 推迟到明天
            </button>
            <button
              type="button"
              className="briefing-2col__action-btn briefing-2col__action-btn--ignore"
              onClick={() => void handleStatusChange("ignored")}
            >
              ✗ 不做
            </button>
          </>
        )}
      </div>

      {backendError && (
        <div className="briefing-2col__backend-err">⚠️ {backendError}</div>
      )}
    </div>
  );
}

// ─── helpers ──────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="briefing-2col__section">
      <div className="briefing-2col__section-title">{title}</div>
      <div className="briefing-2col__section-body">{children}</div>
    </div>
  );
}

function OptionRow({
  option,
  selected,
  openError,
  openedPath,
  accent,
  onSelect,
}: {
  option: AdvisorOption;
  selected: boolean;
  openError: string | null;
  openedPath: string | null;
  accent: string;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={"briefing-2col__option" + (selected ? " briefing-2col__option--selected" : "")}
      style={selected ? { borderColor: accent, background: `${accent}10` } : undefined}
    >
      <div className="briefing-2col__option-header">
        <span className="briefing-2col__option-label">
          {option.label} · {option.summary}
        </span>
        {option.aiLean && <span className="briefing-2col__option-ailean">我倾向</span>}
      </div>
      {option.tone && (
        <div className="briefing-2col__option-tone">tone: {option.tone}</div>
      )}
      {openedPath && (
        <div className="briefing-2col__option-opened">📄 草稿已打开: {openedPath.split("/").pop()}</div>
      )}
      {openError && (
        <div className="briefing-2col__option-err">⚠️ {openError}</div>
      )}
    </button>
  );
}

function ComplianceFlagInline({ flag }: { flag: ComplianceFlag }) {
  const sev = flag.severity;
  const color = sev === "high" ? "#dc2626" : sev === "medium" ? "#a16207" : "#6b7280";
  return (
    <div className="briefing-2col__flag" style={{ borderColor: `${color}40`, background: `${color}08` }}>
      <span style={{ color, fontWeight: 600 }}>⚠️ 合规 ({sev})</span>: {flag.reason}
      {flag.suggestion && (
        <div style={{ marginTop: 4, color: "var(--catfish-text-muted)" }}>建议: {flag.suggestion}</div>
      )}
    </div>
  );
}

function PoliticalFlagInline({ flag }: { flag: PoliticalFlag }) {
  const sev = flag.severity;
  const color = sev === "high" ? "#7c3aed" : sev === "medium" ? "#a16207" : "#6b7280";
  return (
    <div className="briefing-2col__flag" style={{ borderColor: `${color}40`, background: `${color}08` }}>
      <span style={{ color, fontWeight: 600 }}>
        {flag.advisoryOnly ? "🔔 提醒" : "⚠️ 关键关系"} ({sev})
      </span>
      : {flag.reason}
      {flag.suggestedPhrasings && flag.suggestedPhrasings.length > 0 && (
        <div style={{ marginTop: 4, color: "var(--catfish-text-muted)" }}>
          建议口径: {flag.suggestedPhrasings.join(" / ")}
        </div>
      )}
    </div>
  );
}
