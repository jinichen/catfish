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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  setTaskManualStatus,               // P3.5.208-A: SSOT write helper
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
import { taskChatClear, taskChatGet } from "../../../lib/task_chat";
import {
  toolBridgeChatApproval,
  getSession,
  sessionCreate,
  sessionGetByTaskUid,
  sessionSetTaskUid,
} from "../../../lib/tauri";
import { loadSessionMessagesAsChat } from "../../../lib/sessionMessages";
// P3.3.19 C Phase 3 (6/11): buildTaskSystemPrompt 抽到 lib 共享 (ChatTab 也用)
import { buildTaskSystemPrompt } from "../../../lib/taskSystemPrompt";
// P3.5.91 (6/23 鸿波): 早安丢消息治本 — task title → canonical taskUid client cache
import { resolveCanonicalTaskUid } from "../../../lib/task_uid_cache";
import type { Attachment, ChatMessage } from "../../../types/chat";
// P3.3.20 (6/11): 复用工作台 chat input 附件三件套 — helpers / chip / thumb
//   都独立于 useChat / useChatStore, 不耦合 ChatInput.tsx ~600 行那套.
import {
  fileToAttachment,
  MAX_ATTACHMENTS,
  SUPPORTED_AUDIO_EXTS,
  SUPPORTED_FILE_EXTS,
} from "../../Chat/components/attachmentHelpers";
import FileChip from "../../Chat/components/FileChip";
import ThumbCard from "../../Chat/components/ThumbCard";
import { useChatStore } from "../../../store/chat";
import { useUIStore } from "../../../store/ui";
import { useTaskChat } from "../../../hooks/useTaskChat";
import ChatToolCall from "../../Chat/ChatToolCall";
import { Markdown } from "../../../lib/markdown";  // P3.3.10 fix (6/10): 复用工作台 markdown render (粗体/列表/代码块)

/** P3.3.10: hermes approval pending event 数据 (跟 ChatPanel PendingApproval 同款). */
interface PendingApproval {
  approval_session_key: string;
  command: string;
  description: string;
  pattern_key: string;
}

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
            onStatusChange={(s) => onStatusChange(selected.title, s)}
          />
        ) : (
          <div className="briefing-2col__detail-empty">选个待办看详情</div>
        )}
      </main>
    </div>
  );
}

// ─── 右侧详情 (Phase 1 6/10): task-scoped chat ────────────────────

// P3.3.19 C Phase 3 (6/11): buildTaskSystemPrompt 抽到 lib/taskSystemPrompt.ts.
// DetailPane + ChatTab task picker 共享同一份. 改 system prompt 必须改 lib 那份.

function DetailPane({
  task,
  effective,
  statusFromChat,
  wasSnoozedYesterday,
  onStatusChange,
}: {
  task: MainTask;
  // P3.5.208-A: props 从 SSOT selector 传下来的 effective + statusFromChat 视觉标.
  // DetailPane 不再 merge (parent 已算), 也不再关心 raw taskState/chatStatus.
  effective: EffectiveTaskStatus;
  statusFromChat: boolean;
  wasSnoozedYesterday: boolean;
  onStatusChange: (s: TaskStatus | null) => void;
}) {
  const status = effective;
  const accent =
    task.urgency === "high" ? "#c2410c" :
    task.urgency === "medium" ? "#a16207" : "#6b7280";

  // P3.5.91 (6/23 鸿波): canonical taskUid — 跟 LLM 给的可能不同.
  //   client 端按 normalized title 做 first-seen cache (~/.catfish/task_uid_cache.json),
  //   advisor refresh 给同 title 新 uid 时, 用 cache 中 canonical uid 找 session,
  //   不被 LLM 给的新 uid 误导. 解 P3.5.90 audit "刷新丢消息" 真因.
  //   sessionIdRef / ensureSessionId / mount fetch 全用 canonical uid.
  const canonicalTaskUidRef = useRef<string>(task.taskUid);
  // P3.3.9 (6/10): chat key 用 task.taskUid (LLM 给的稳定 6 字符 uid),
  //   fallback title (老数据 / 极端情况 taskUid 为空). 这样 LLM 每天重写
  //   title 也不会让 chat 历史丢, 因为 uid 在 advisor_cache 里复用了.
  const chatKey = task.taskUid || task.title;
  const model = useChatStore((s) => s.model);

  // P3.3.10: system prompt + persist 走 useTaskChat hook 接口.
  //   buildSystemPrompt 用 ref 拿当前 task 不停, 不重建 hook.
  const taskRef = useRef(task);
  taskRef.current = task;
  const chatKeyRef = useRef(chatKey);
  chatKeyRef.current = chatKey;

  const buildSystemPrompt = useCallback(
    () => buildTaskSystemPrompt(taskRef.current),
    [],
  );
  // P3.3.19 C Phase 2d (6/11) + P3.5.10 (6/16 鸿波 lazy create):
  //   mount 只 sessionGetByTaskUid 拿已有 sid (sid 有 → loadHistory; 没 → 显空白).
  //   不再 mount 就 sessionCreate — 那会导致打开早安 tab 默认选中的 task / 切别的
  //   task 卡片时建一堆空 session 污染工作台 sidebar (鸿波 6/16 16:00 反馈).
  //   真建 session 推迟到 ensureSessionId callback, useTaskChat.send 入口才调.
  const [sessionId, setSessionId] = useState<string | null>(null);
  // P3.5.10: sessionId 给 ref, 让 ensureSessionId callback 不需要重建 (避免
  // useTaskChat opts 闭包随每次 setSessionId 重新 init).
  const sessionIdRef = useRef<string | null>(null);
  sessionIdRef.current = sessionId;

  /** P3.5.10 (6/16 鸿波): lazy ensure session.
   *  - 已有 sid → 直接返
   *  - 没 sid → sessionCreate + sessionSetTaskUid 建关联 + setSessionId
   *  - sessionCreate 失败 → 返 null, useTaskChat 软退化跳过 persist (chat 仍 work)
   *
   *  ensureSessionId 不依赖 selectedId 变化 — 切走 task 后 sessionIdRef 会被新
   *  task useEffect 改 (mount → sessionGetByTaskUid 或 null), 但本轮 send 已经
   *  snapshot 了 sid 到 sidForRound, 不受影响. */
  const ensureSessionId = useCallback(async (): Promise<string | null> => {
    if (sessionIdRef.current) return sessionIdRef.current;
    const taskNow = taskRef.current;
    try {
      const created = await sessionCreate({
        model,
        title: taskNow.title,
        systemPrompt: buildTaskSystemPrompt(taskNow),
      });
      sessionIdRef.current = created.id;
      setSessionId(created.id);
      // P3.5.91 (6/23 鸿波) D 路径: 关联 canonical task uid (cache 中或 LLM 给的).
      // 加 retry 1 次 + UI warning. 不再静默 fail (P3.5.90 audit 真因 ③).
      // 关联失败 → 下次 mount sessionGetByTaskUid 找不到 → 显空白 = "消息丢失" 直接症状.
      const canonical = canonicalTaskUidRef.current || taskNow.taskUid;
      let lastErr: unknown = null;
      for (let attempt = 1; attempt <= 2; attempt++) {
        try {
          await sessionSetTaskUid(created.id, canonical);
          lastErr = null;
          break;
        } catch (e) {
          lastErr = e;
          console.warn(
            `[BriefingTwoColumn] sessionSetTaskUid attempt ${attempt}/2 失败:`,
            e,
          );
          if (attempt === 1) {
            await new Promise((r) => setTimeout(r, 200));
          }
        }
      }
      if (lastErr) {
        // 真 fail-loud — 不再静默. UI 提示员工: 本会话历史下次可能找不回.
        console.error(
          "[BriefingTwoColumn] sessionSetTaskUid 2 次都失败 — 历史关联未建, 下次 advisor refresh 可能找不到本会话:",
          lastErr,
        );
        setAttachError(
          "⚠️ 会话关联未建成功, 下次刷新此 task 可能找不到当前对话历史. 详细看 console.",
        );
      }
      return created.id;
    } catch (e) {
      console.warn("[BriefingTwoColumn lazy] sessionCreate 失败, persist 跳过:", e);
      return null;
    }
  }, [model]);

  const taskChat = useTaskChat({
    model,
    buildSystemPrompt,
    ensureSessionId,
  });
  const { messages, isStreaming, send, cancel, loadHistory } = taskChat;

  const [input, setInput] = useState("");
  // P3.3.20 (6/11): in-memory attachments — image base64 / file preview,
  //   关掉再回来丢, state.db 只存占位 "[📎 N 张图 + 📄 M 份文档]" (跟工作台同款 MVP).
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [attachError, setAttachError] = useState<string | null>(null);
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [chatError, setChatError] = useState<string | null>(null);
  const [backendError, setBackendError] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // P3.3.43 (6/12 鸿波 "全部删掉"): revert P3.3.41 加的 selectedOptLabel /
  // selectedOpenError / selectedOpenedPath / handleOptionSelect — chat 才是 main UI.

  // P3.3.10 floating approval banner state (跟 ChatPanel 同款逻辑).
  //   hermes _gateway_approval 阻塞期间 chat completions 不 finalize → 立即弹 banner.
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [submittingChoice, setSubmittingChoice] = useState<
    "once" | "session" | "always" | "deny" | null
  >(null);
  const [secondsLeft, setSecondsLeft] = useState(60);

  // P3.3.19 C Phase 2d (6/11) + P3.5.10 (6/16 鸿波 lazy create):
  //   mount 只拿已有 sid + 历史, **不 mount 即建 session**.
  //
  //   旧路径 (P3.3.19): 没 sid 直接 sessionCreate + sessionSetTaskUid + setSessionId.
  //   副作用 (6/16 鸿波看到): 早安 tab 打开默认选中第一个 task, DetailPane mount
  //   立刻建空 session. advisor 每次 refresh tasks 数组变, defaultId 变,
  //   selectedId 切换, DetailPane 重 mount, 又建新空 session. 累积一堆 0 条
  //   session 污染工作台 sidebar — title 还是 task 名 ("加计扣除申报..." 等),
  //   员工迷惑.
  //
  //   新路径 (P3.5.10): mount 不 sessionCreate, 真建推迟到员工第一次发消息时
  //   (useTaskChat.send → ensureSessionId callback 内部建). 纯浏览看 task 详情
  //   不再污染 sidebar.
  //
  //   Phase 2e fallback (老 jsonl 仍读): db sid 没拿到时仍尝试 taskChat jsonl
  //   历史显示 read-only — 老历史能看, 但要回复就触发 lazy create.
  useEffect(() => {
    let cancelled = false;
    setHistoryLoading(true);
    setSessionId(null);
    void (async () => {
      try {
        // P3.5.91 (6/23 鸿波) C 路径: 解析 canonical taskUid.
        //   同 normalized title 第一次见 → cache LLM 给的, 返同样;
        //   再见 (advisor refresh 给新 uid) → ignore LLM 的, 用 cache 中 canonical;
        //   写入 ref, ensureSessionId / setTaskUid 都用 canonical.
        const canonical = await resolveCanonicalTaskUid(task.title, task.taskUid);
        if (cancelled) return;
        canonicalTaskUidRef.current = canonical;
        // 老 query 用 canonical (跟 cache 一致, advisor refresh 给新 uid 不影响)
        const sid = await sessionGetByTaskUid(canonical);
        let dbMessagesCount = 0;
        if (sid) {
          // 已有 session — 拉历史
          const detail = await getSession(sid).catch(() => null);
          if (detail && !cancelled) {
            const chatMessages = loadSessionMessagesAsChat(detail);
            dbMessagesCount = chatMessages.length;
            if (chatMessages.length > 0) {
              loadHistory(chatMessages);
            }
          }
        }
        // 注意: 没拿到 sid 不 sessionCreate. setSessionId(null) 保持, 等
        // ensureSessionId callback (员工发第一句时) 才真建. UI 仍可显示
        // jsonl fallback 历史 (read-only).
        if (cancelled) return;

        // Phase 2e fallback: db 空 + 老 jsonl 有 → 显 jsonl. Phase 4 一次性 migration.
        if (dbMessagesCount === 0) {
          let hist = await taskChatGet(chatKey, 200).catch(() => []);
          if (hist.length === 0 && chatKey !== task.title) {
            const oldHist = await taskChatGet(task.title, 200).catch(() => []);
            if (oldHist.length > 0) {
              console.log(
                `[BriefingTwoColumn] 老 title jsonl 命中 fallback (${oldHist.length} 条), uid='${chatKey}'`,
              );
              hist = oldHist;
            }
          }
          if (hist.length > 0 && !cancelled) {
            // jsonl → ChatMessage[] (跟 P3.3.11 load 逻辑同款)
            const all: ChatMessage[] = hist.map((m, i) => ({
              id: `hist-${i}-${m.ts}`,
              role: m.role as ChatMessage["role"],
              content: m.content,
              tool_calls: m.toolCalls,
              tool_call_id: m.toolCallId,
              ts: m.ts,
              status: "done",
            }));
            const toolResultByCallId = new Map<string, string>();
            for (const m of all) {
              if (m.role === "tool" && m.tool_call_id) {
                toolResultByCallId.set(m.tool_call_id, m.content);
              }
            }
            const joined = all.map((m) => {
              if (m.role !== "assistant" || !m.tool_calls?.length) return m;
              return {
                ...m,
                tool_calls: m.tool_calls.map((tc) => {
                  const r = toolResultByCallId.get(tc.id);
                  if (r === undefined) return tc;
                  return { ...tc, result: r, status: "done" as const };
                }),
              };
            });
            loadHistory(joined);
          }
        }

        // sid 可能是 null (没 session) — 不变成 ""空字符串, 保持 null 让 UI 能区分
        // "还没建过 session" vs "建过 session id=空字符串". ensureSessionId 看
        // sessionIdRef.current 为 null 时才真建.
        setSessionId(sid);
      } catch (e) {
        console.warn("[BriefingTwoColumn] mount session 链路失败:", e);
      } finally {
        if (!cancelled) setHistoryLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // P3.5.91 (6/23 鸿波) A 路径: 砍 task.title 依赖 — title 每次 advisor refresh
    // LLM 重写 (briefing_advisor.ts 注释), 但 title 变 ≠ 这是新 task. 之前 deps
    // 含 title → advisor 自动 refresh 30 min → title 改 → useEffect 重跑 → 重 fetch
    // session → 如果 taskUid 也变 (C 路径 cache 解 normalized title 一致) → 丢消息.
    // 现 deps 只看 chatKey + canonical taskUid 变化, title 跳变不再 trigger reload.
    //
    // chatKey 仍依赖 task.taskUid (P3.3.9 chatKey 公式), 但 canonical 由 C 路径
    // 兜底, normalize 后稳定. 真实"切到别的 task" 才会触发重 mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatKey, task.taskUid]);

  // 新消息进来自动滚到底
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  // P3.3.10: 监听 catfish:approval-pending event → 弹 banner.
  //   chat.ts streamChat 在解 hermes SSE 看到 status=approval_pending 时 dispatch.
  useEffect(() => {
    const handler = (e: Event) => {
      const ev = e as CustomEvent<PendingApproval>;
      if (ev.detail?.approval_session_key) {
        setPending(ev.detail);
        setSecondsLeft(60); // 重置倒计时
      }
    };
    window.addEventListener("catfish:approval-pending", handler as EventListener);
    return () => window.removeEventListener("catfish:approval-pending", handler as EventListener);
  }, []);

  // 60s countdown — hermes _gateway_approval 默认 timeout 60s, 自动消失
  useEffect(() => {
    if (!pending) return;
    const tick = setInterval(() => {
      setSecondsLeft((s) => {
        if (s <= 1) {
          clearInterval(tick);
          setPending(null);
          return 0;
        }
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(tick);
  }, [pending]);

  // P3.3.10: 监听 ChatToolCall approval inline button 触发的 send event.
  //   ChatToolCall → dispatch catfish:approval-send → 这里走 send (跟员工同款 path).
  useEffect(() => {
    const handler = (e: Event) => {
      const ev = e as CustomEvent<{ text: string }>;
      const text = ev.detail?.text;
      if (text && typeof text === "string") {
        void send(text);
      }
    };
    window.addEventListener("catfish:approval-send", handler as EventListener);
    return () => window.removeEventListener("catfish:approval-send", handler as EventListener);
  }, [send]);

  const handleApproval = async (choice: "once" | "session" | "always" | "deny") => {
    if (!pending || submittingChoice) return;
    setSubmittingChoice(choice);
    try {
      await toolBridgeChatApproval(pending.approval_session_key, choice);
    } catch (err) {
      console.warn("[BriefingTwoColumn] chat_approval RPC 失败:", err);
    }
    setSubmittingChoice(null);
    setPending(null);
  };

  const handleSend = async () => {
    const text = input.trim();
    // P3.3.20 (6/11): 纯文字或纯附件都允许发, 跟 useChat 同款.
    if ((!text && attachments.length === 0) || isStreaming) return;
    setChatError(null);
    setInput("");
    const atts = attachments;
    setAttachments([]);
    setAttachError(null);
    try {
      await send(text, atts);
    } catch (e) {
      setChatError(String(e));
    }
  };

  // P3.3.20 (6/11): 文件接收 — 📎 picker / 粘贴 / 拖入 共用入口.
  //   并行跑 fileToAttachment (图 base64 / 文档走 parse_file → preview / 音频走 whisper).
  //   超过 MAX_ATTACHMENTS 截断 + 提示; 单文件挂不阻塞其他.
  const ingestFiles = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return;
      setAttachError(null);
      const room = MAX_ATTACHMENTS - attachments.length;
      if (room <= 0) {
        setAttachError(`最多 ${MAX_ATTACHMENTS} 个附件, 先删几个`);
        return;
      }
      const accept = files.slice(0, room);
      if (files.length > room) {
        setAttachError(`只收了前 ${room} 个 (上限 ${MAX_ATTACHMENTS})`);
      }
      const results = await Promise.allSettled(accept.map((f) => fileToAttachment(f)));
      const ok: Attachment[] = [];
      const errs: string[] = [];
      results.forEach((r, i) => {
        if (r.status === "fulfilled") ok.push(r.value);
        else errs.push(`${accept[i].name}: ${String(r.reason).slice(0, 80)}`);
      });
      if (ok.length > 0) setAttachments((prev) => [...prev, ...ok]);
      if (errs.length > 0) setAttachError(errs.join(" · "));
    },
    [attachments.length],
  );

  const onPickFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    void ingestFiles(files);
    // reset 让同一文件能再选
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const onPasteInput = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files: File[] = [];
    for (const item of Array.from(e.clipboardData?.items ?? [])) {
      if (item.kind === "file") {
        const f = item.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length > 0) {
      e.preventDefault();
      void ingestFiles(files);
    }
  };

  const onDropInput = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDraggingOver(false);
    const files = Array.from(e.dataTransfer?.files ?? []);
    if (files.length > 0) void ingestFiles(files);
  };

  const removeAttachment = (idx: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== idx));
    setAttachError(null);
  };

  // P3.3.9: clear uid + 也 try clear 老 title file (兼容老 jsonl)
  // P3.5.218 (7/10 鸿波 catch '清空对话没反应'): 老 handleClearChat 只清了
  // Companion 侧 jsonl + UI React 状态, hermes 端 session 里的 messages 还在.
  // chat.ts:384-387 每次 send 传 X-Hermes-Session-Id 让 hermes 复用同一 session
  // → 拉出老 history 拼给 LLM → AI 继续沿用老套路 ('还是那 3 个选项').
  // 修: clear 后 reset sessionIdRef, 下次 send 走 ensureSessionId lazy 创建
  // **新 session**, 老 session 数据保留可 debug 但被丢弃, 新 chat 干净 fresh.
  const handleClearChat = async () => {
    if (!window.confirm("清掉这条待办的所有对话历史? 不可撤销.")) return;
    try {
      cancel();
      await taskChatClear(chatKey);
      if (chatKey !== task.title) {
        await taskChatClear(task.title).catch(() => undefined);
      }
      loadHistory([]);
      // P3.5.218: reset sessionId 让下次 send 起新 hermes session
      sessionIdRef.current = null;
      setSessionId(null);
    } catch (e) {
      console.warn("[BriefingTwoColumn] clear task chat 失败:", e);
    }
  };

  const handleStatusChange = async (s: TaskStatus | null) => {
    setBackendError(null);
    // P3.5.208-A: 走 setTaskManualStatus SSOT write helper. 内部同时写
    // taskChatSummaries[uid].manualStatus (新 SSOT) + 老 taskState (过渡期
    // 兼容, 直到 P3.5.209 删双写). 员工看不出差异, 但 briefing filter
    // 下次跑时读的是 taskChatSummaries.manualStatus (单源).
    try {
      await setTaskManualStatus(task.taskUid, task.title, s);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setBackendError(`状态没存 (Rust 后端没 build?): ${msg.slice(0, 100)}`);
    }
    // P3.3.52 (6/12 鸿波): decisions audit 留痕. 不阻塞 UI, 错只 warn.
    //   走 audit_chain → decisions.jsonl 含 sha256 防篡改.
    //   "cleared" = s===null (员工撤销之前的状态).
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("decision_record_status_change", {
        taskUid: task.taskUid,
        taskTitle: task.title,
        newStatus: s ?? "cleared",
      });
    } catch (e) {
      console.warn("[BriefingTwoColumn] P3.3.52 audit 留痕失败 (不阻塞):", e);
    }
    onStatusChange(s);
  };

  return (
    <div className="briefing-2col__detail-inner">
      <div className="briefing-2col__detail-header" style={{ borderLeftColor: accent }}>
        <div className="briefing-2col__detail-badges">
          <span style={{ color: accent, fontSize: 12, fontWeight: 600 }}>
            {task.urgency === "high" ? "急" : task.urgency === "medium" ? "中" : "低"}
          </span>
          {task.reason && (
            <span className="briefing-2col__detail-reason">{task.reason}</span>
          )}
          {wasSnoozedYesterday && (
            <span className="briefing-2col__badge-warn">⏰ 昨天推过</span>
          )}
          {/* P3.5.207: effective status. statusFromChat=true 时是 chat 语义推断的
              (员工没点按钮), 加"(chat)" 后缀让员工能区分, 想撤要回 chat 里说. */}
          {status === "resolved" && (
            <span className="briefing-2col__badge-done">
              已完成{statusFromChat ? " (chat)" : ""}
            </span>
          )}
          {status === "paused" && (
            <span className="briefing-2col__badge-warn">
              已推迟{statusFromChat ? " (chat)" : ""}
            </span>
          )}
        </div>
        <h3 className="briefing-2col__detail-title">{task.title}</h3>
      </div>

      {/* P3.3.43 (6/12 鸿波 "不要给建议的这个框，把这个代码全部删掉"):
       *  P3.3.41 在这里加了 options/合规/政治/历史 渲染框. 鸿波明确不要.
       *  catfish 哲学也支持 — chat 是员工跟 AI 直接对话的入口, 不需要 UI 预先 push
       *  建议/警告. AI 按需在 chat 里说就行. */}

      <div ref={scrollRef} className="briefing-2col__chat-thread">
        {historyLoading && (
          <div className="briefing-2col__chat-empty">
            <div style={{ opacity: 0.7 }}>加载历史对话…</div>
          </div>
        )}
        {!historyLoading && messages.length === 0 && (
          <div className="briefing-2col__chat-empty">
            <div>跟 AI 直接说这条待办 — 报进度 / 起草 / 问下一步.</div>
            <div className="briefing-2col__chat-empty-hint">
              AI 已经知道: 标题 · 紧急度 · 历史{task.complianceFlags.length > 0 ? " · 合规提示" : ""}
              {task.politicalFlags.length > 0 ? " · 关键关系" : ""}
              {task.options.length > 0 ? ` · ${task.options.length} 个早晨口径` : ""}.
              对话历史会保留, 关 Companion / 切 task 再回来都还在.
            </div>
          </div>
        )}
        {!historyLoading && messages.map((m) => (
          <ChatMsg key={m.id} msg={m} />
        ))}
        {chatError && (
          <div className="briefing-2col__chat-err">⚠️ {chatError}</div>
        )}
      </div>

      {/* P3.3.10: floating approval banner (跟工作台 ChatPanel 同款 UI) */}
      {pending && (
        <div className="approval-banner">
          <div className="approval-banner__header">
            {/* P3.5.73 (6/22 鸿波 catch): "hermes 等批准" → "等批准". SOUL.md
                L5 品牌红线 "对外永不说: 我是 Hermes". 后面 pattern chip 自带
                tool 名 (execute_code / write_file / etc), 不需要前缀做归属说明. */}
            <span>等批准</span>
            <code className="pattern">{pending.pattern_key}</code>
            <span className="countdown" aria-live="polite">{secondsLeft}s</span>
          </div>
          <div className="approval-banner__code">{pending.command}</div>
          <div className="approval-banner__actions">
            <button
              className="approval-banner__btn-primary"
              onClick={() => void handleApproval("once")}
              disabled={submittingChoice !== null}
              autoFocus
            >
              {submittingChoice === "once" && <span className="approval-banner__spinner" />}
              批准
            </button>
            <button
              className="approval-banner__btn-secondary"
              onClick={() => void handleApproval("session")}
              disabled={submittingChoice !== null}
            >
              本会话始终
            </button>
            <button
              className="approval-banner__btn-secondary"
              onClick={() => void handleApproval("always")}
              disabled={submittingChoice !== null}
            >
              永久
            </button>
            <button
              className="approval-banner__btn-danger"
              onClick={() => void handleApproval("deny")}
              disabled={submittingChoice !== null}
            >
              拒绝
            </button>
          </div>
        </div>
      )}

      {messages.length > 0 && (
        <div className="briefing-2col__chat-toolbar">
          <button
            type="button"
            className="briefing-2col__chat-clear-btn"
            onClick={() => void handleClearChat()}
            title="清掉这个待办的所有对话历史 (不可撤销)"
          >
            清空对话
          </button>
        </div>
      )}

      {/* P3.3.20 (6/11): 附件预览 — image 走 ThumbCard, file/audio 走 FileChip. */}
      {attachments.length > 0 && (
        <div className="briefing-2col__chat-attach-preview">
          {attachments.map((a, i) =>
            a.kind === "image" ? (
              <ThumbCard key={i} attachment={a} onRemove={() => removeAttachment(i)} />
            ) : (
              <FileChip key={i} attachment={a} onRemove={() => removeAttachment(i)} />
            ),
          )}
        </div>
      )}
      {attachError && (
        <div className="briefing-2col__chat-attach-error">{attachError}</div>
      )}

      <div
        className={`briefing-2col__chat-input-row${isDraggingOver ? " briefing-2col__chat-input-row--dragover" : ""}`}
        onDragOver={(e) => {
          if (Array.from(e.dataTransfer?.items ?? []).some((it) => it.kind === "file")) {
            e.preventDefault();
            setIsDraggingOver(true);
          }
        }}
        onDragLeave={() => setIsDraggingOver(false)}
        onDrop={onDropInput}
      >
        {/* P3.3.20 (6/11): 📎 文件 picker 按钮 + 隐藏 input. */}
        <button
          type="button"
          className="briefing-2col__chat-attach-btn"
          onClick={() => fileInputRef.current?.click()}
          disabled={isStreaming || attachments.length >= MAX_ATTACHMENTS}
          title={`加附件 (图片 / PDF / Excel / Word / CSV / TXT / MD / 音频, 最多 ${MAX_ATTACHMENTS} 个; 也可粘贴 / 拖入)`}
        >
          📎
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={`image/*,${SUPPORTED_FILE_EXTS.join(",")},${SUPPORTED_AUDIO_EXTS.join(",")}`}
          style={{ display: "none" }}
          onChange={onPickFiles}
        />
        <textarea
          className="briefing-2col__chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onPaste={onPasteInput}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void handleSend();
            }
          }}
          placeholder={
            isStreaming
              ? "AI 回答中…"
              : isDraggingOver
                ? "松开鼠标加附件"
                : "Enter 发送 · Shift+Enter 换行 · 粘贴 / 拖入加附件"
          }
          rows={1}
          disabled={isStreaming}
        />
        <button
          type="button"
          className="briefing-2col__chat-send"
          onClick={() => void handleSend()}
          disabled={(!input.trim() && attachments.length === 0) || isStreaming}
        >
          {isStreaming ? "…" : "发送"}
        </button>
      </div>

      <div className="briefing-2col__actions">
        {/* P3.5.207: 用 effective status. statusFromChat=true 时"撤销"其实清不掉
            chat 语义, 员工要撤要回 chat 里说. 按钮 title 显示提示. */}
        {status === "resolved" ? (
          <button
            type="button"
            className="briefing-2col__action-btn"
            onClick={() => void handleStatusChange(null)}
            title={
              statusFromChat
                ? "chat 里说过'办完了' → 撤销这里只是清卡片 marker; 想让 briefing 明天再推, 回 chat 里说 '再看看'"
                : "撤销标记完成"
            }
          >
            撤销完成{statusFromChat ? " (仅撤按钮标记)" : ""}
          </button>
        ) : status === "paused" ? (
          <button
            type="button"
            className="briefing-2col__action-btn"
            onClick={() => void handleStatusChange(null)}
            title={
              statusFromChat
                ? "chat 里说过'先放放' → 撤销这里只是清卡片 marker; 想让 briefing 明天再推, 回 chat 里说 '继续跟'"
                : "撤销推迟"
            }
          >
            撤销推迟{statusFromChat ? " (仅撤按钮标记)" : ""}
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

function ChatMsg({ msg }: { msg: ChatMessage }) {
  if (msg.role === "user") {
    // user bubble teal fill + 白字, Markdown 默认黑字会撞色 — user 消息走纯文本.
    // whiteSpace pre-wrap 保留换行.
    return (
      <div
        className="briefing-2col__msg briefing-2col__msg--user"
        style={{ whiteSpace: "pre-wrap" }}
      >
        {msg.content}
      </div>
    );
  }
  // tool 角色不直接渲染 — 已经通过 ChatToolCall 卡片在 assistant.tool_calls[i].result 里显
  if (msg.role === "tool" || msg.role === "system") return null;

  // assistant: markdown 文本 + tool_calls 卡片 (P3.3.10 fix 6/10: 用 Markdown 组件)
  const hasContent = (msg.content ?? "").length > 0;
  const hasToolCalls = (msg.tool_calls ?? []).length > 0;
  return (
    <div
      className={
        "briefing-2col__msg briefing-2col__msg--assistant" +
        (msg.status === "streaming" ? " briefing-2col__msg--streaming" : "") +
        (msg.status === "error" ? " briefing-2col__msg--error" : "")
      }
    >
      {hasContent && <Markdown text={msg.content} />}
      {!hasContent && !hasToolCalls && msg.status === "streaming" ? "…" : null}
      {hasToolCalls && (
        <div style={{ marginTop: hasContent ? 8 : 0 }}>
          {msg.tool_calls!.map((tc) => (
            <ChatToolCall key={tc.id} call={tc} />
          ))}
        </div>
      )}
    </div>
  );
}

// (P3.3.7 Phase 1: Section / OptionRow / ComplianceFlagInline / PoliticalFlagInline
//  砍掉, 因为 detail pane 改成 chat. flag / option 信息已经在 system prompt 里注入,
//  LLM 会主动用. 老 helper 在 git history 6/10 之前的 commit 找得回.)
