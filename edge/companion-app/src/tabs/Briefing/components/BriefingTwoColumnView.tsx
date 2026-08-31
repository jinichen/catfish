/** P3.3.6 (2026-06-10 鸿波) — 早安 tab 左右两栏 view.
 *
 * P3.3.7 Phase 1 (6/10): DetailPane 砍'目标/进展/建议' 静态段, 改成 in-memory
 * task-scoped chat. system prompt 注入 task 上下文.
 * P3.3.7 Phase 2 (6/10): chat 持久化到 ~/.catfish/task_chat/<key>.jsonl
 * P3.3.8/.9 (6/10): 天气 / task_uid stable key
 * P3.3.10 (6/10): chat 升到工作台同款 — useTaskChat hook (含 tool calling),
 *   ChatToolCall 卡片 render tool 进度 / 调用结果, P15/P15.2 approval banner
 *   监听 catfish:approval-pending event 弹顶部, catfish:approval-send event
 *   走 send 路径.
 *
 * 左 sidebar 按 urgency 分组 (急/中/低/已默认处理) 保持不变.
 *
 * 数据 0 后端改动: 复用 MainTask / HandledSilentlyItem / advisorTaskState.
 */

import { useEffect, useMemo, useState } from "react";

// 8/15: 本文件原来 1017 行 —— 左栏列表 + 右栏详情长在一起, DetailPane 一个人
// 就 707 行。搬去 BriefingDetailPane.tsx, 左右两栏本来就是两件事。
//
// 跟着它一起走的还有一长串 import: task_chat / sessionMessages /
// taskSystemPrompt / task_uid_cache / 附件三件套 / useTaskChat / ChatToolCall /
// Markdown —— 全是右栏聊天才用的东西。剩下这几个才是左栏真正需要的。
//
// setTaskManualStatus 也走了: **写**状态的动作只在右栏发生, 左栏只读
// effectiveStatusByUid 这个派生 map (P3.5.208-A 定的 SSOT 约定, 见下面 props
// 上的注释)。这条依赖关系在拆分前是看不出来的。
import { DetailPane } from "./BriefingDetailPane";

import {
  type EffectiveTaskStatus,
  type TaskStateFetch,
  type TaskStatus,
} from "../../../lib/advisor_cache";
import type {
  BlindSpotItem,
  GraveyardItem,
  HandledSilentlyItem,
  MainTask,
  SubconsciousItem,
} from "../../../lib/briefing_advisor";
import {
  BlindSpotsCard,
  GraveyardCard,
  SubconsciousCard,
} from "./InsightReflectCards";
// P3.3.43 (6/12 鸿波): revert P3.3.41 — 砍 options/合规/历史 渲染 import
import { useUIStore } from "../../../store/ui";

interface BriefingTwoColumnViewProps {
  tasks: MainTask[];                                  // 已 filter ignored
  handledItems: HandledSilentlyItem[];
  // P3.5.32 Phase 10 (6/18 鸿波 OpenWiki 借鉴) — 3 维 self-aware reflection.
  // optional 默认空 array — LLM 没返时 cards 0 渲染 (length 0 早返).
  subconscious: SubconsciousItem[];
  graveyard: GraveyardItem[];
  blindSpots: BlindSpotItem[];
  /** P3.5.208-A 过渡期保留 taskState prop (老 taskState.json 双写兼容 + 兜底 yesterdaySnoozed). */
  taskState: TaskStateFetch;
  /** P3.5.208-A (7/9 鸿波 catch 'view-side merge 不是真 SSOT'):
   *  effectiveStatusByUid 是 SSOT selector 派生的**只读** map (key=taskUid).
   *  UI 判徽章 + action 按钮 disabled 全走这个, 不再直接读 taskState.today[title]
   *  或 chatStatusByUid raw 值 — 保证读取语义单调, 单一 source. */
  effectiveStatusByUid?: Map<string, EffectiveTaskStatus>;
  wasSnoozedYesterday: (title: string) => boolean;
  onStatusChange: (taskUid: string, taskTitle: string, status: TaskStatus | null) => void;
}


type UrgencyGroup = "high" | "medium" | "low";

// 8/8 评审修正: 老 tailwind orange-700/amber-700 硬编码跟品牌 token 两套橙并存,
// 暗色下发闷. 改走 token — 急=品牌暖橙, 中=状态琥珀, 低=中性弱字.
const URGENCY_META: Record<UrgencyGroup, { label: string; color: string; bg: string }> = {
  high:   { label: "急", color: "var(--catfish-orange)", bg: "color-mix(in srgb, var(--catfish-orange) 9%, transparent)" },
  medium: { label: "中", color: "var(--status-warn)", bg: "color-mix(in srgb, var(--status-warn) 9%, transparent)" },
  low:    { label: "低", color: "var(--catfish-text-muted)", bg: "color-mix(in srgb, var(--catfish-text-muted) 7%, transparent)" },
};

export default function BriefingTwoColumnView({
  tasks,
  handledItems,
  subconscious,
  graveyard,
  blindSpots,
  taskState,
  effectiveStatusByUid,
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

  // P3.5.32 Phase 10 (6/18 鸿波 OpenWiki 借鉴) — reflect button → 切 Chat tab + 复制 prompt.
  // MVP: setActiveTab('chat') + clipboard 复制 prompt + alert toast.
  // 未来 Phase 10.1: 直接进 Chat tab 自动 send (queueMessage / setMessages).
  // 现在最简: 切 tab + 提示员工 Cmd+V send.
  const setActiveTab = useUIStore((s) => s.setActiveTab);
  const handleReflect = (prompt: string) => {
    if (!prompt) return;
    void navigator.clipboard.writeText(prompt).catch(() => {});
    setActiveTab("chat");
    // MVP alert. 未来 Phase 10.1 inline toast.
    window.setTimeout(() => {
      // eslint-disable-next-line no-alert
      alert(
        `已切到 Chat tab + 复制 reflect prompt:\n\n  ${prompt}\n\n💡 Cmd+V 粘贴到输入框 send`,
      );
    }, 50);
  };

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
                // P3.5.208-A (7/9 鸿波): SSOT 读取. effectiveStatusByUid 是唯一
                // 读取入口, 不再自己 merge. 空 map → 视为 pending (默认无徽章).
                const effective: EffectiveTaskStatus =
                  effectiveStatusByUid?.get(t.taskUid) ?? "pending";
                const isSelected = t.id === selectedId;
                return (
                  <button
                    key={t.id}
                    type="button"
                    className={
                      "briefing-2col__task-row" +
                      (isSelected ? " briefing-2col__task-row--selected" : "") +
                      (effective === "resolved" ? " briefing-2col__task-row--done" : "") +
                      (effective === "paused" ? " briefing-2col__task-row--snoozed" : "")
                    }
                    style={isSelected ? { background: meta.bg, borderLeftColor: meta.color } : undefined}
                    onClick={() => setSelectedId(t.id)}
                  >
                    <div className="briefing-2col__task-title">
                      {effective === "resolved" && "✓ "}
                      {effective === "paused" && "⏰ "}
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

        {/* P3.5.32 Phase 10 (6/18 鸿波 OpenWiki 借鉴) — 3 维 self-aware reflection.
            0 item 时 card return null (无视觉噪音). LLM 没返 / 返空 → 0 渲染. */}
        <SubconsciousCard items={subconscious} onReflect={handleReflect} />
        <GraveyardCard items={graveyard} />
        <BlindSpotsCard items={blindSpots} onReflect={handleReflect} />
      </aside>

      <main className="briefing-2col__detail">
        {selected ? (
          <DetailPane
            key={selected.id}
            task={selected}
            /* P3.5.208-A: 直接传 effective, DetailPane 不再自 merge (SSOT selector 已算过).
                statusFromChat 用 taskState.today 反推 (老 taskState 有 = 卡片按钮点的; 无 = chat 里 LLM 判的). */
            effective={effectiveStatusByUid?.get(selected.taskUid) ?? "pending"}
            statusFromChat={
              (effectiveStatusByUid?.get(selected.taskUid) ?? "pending") !== "pending"
                && taskState.today[selected.title]?.status == null
            }
            wasSnoozedYesterday={wasSnoozedYesterday(selected.title)}
            onStatusChange={(s) => onStatusChange(selected.taskUid, selected.title, s)}
          />
        ) : (
          <div className="briefing-2col__detail-empty">选个待办看详情</div>
        )}
      </main>
    </div>
  );
}
