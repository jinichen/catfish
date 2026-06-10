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
  advisorTaskStateClear,
  advisorTaskStateSet,
  type TaskStateFetch,
  type TaskStatus,
} from "../../../lib/advisor_cache";
import type {
  HandledSilentlyItem,
  MainTask,
} from "../../../lib/briefing_advisor";
import { taskChatAppend, taskChatClear, taskChatGet } from "../../../lib/task_chat";
import { toolBridgeChatApproval } from "../../../lib/tauri";
import type { ChatMessage } from "../../../types/chat";
import { useChatStore } from "../../../store/chat";
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
            key={selected.id}
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

// ─── 右侧详情 (Phase 1 6/10): task-scoped chat ────────────────────

function buildTaskSystemPrompt(task: MainTask): string {
  const lines: string[] = [
    "你是 catfish, 员工的工作参谋. 现在跟员工讨论一条具体待办.",
    "",
    "## 待办",
    `标题: ${task.title}`,
    `紧急度: ${task.urgency === "high" ? "急" : task.urgency === "medium" ? "中" : "低"}`,
  ];
  if (task.reason) {
    lines.push(`理由: ${task.reason}`);
  }
  if (task.contextRefs.length > 0) {
    lines.push("", "## 历史上下文");
    task.contextRefs.forEach((r) => lines.push(`- ${r}`));
  }
  if (task.complianceFlags.length > 0) {
    lines.push("", "## 合规提示");
    task.complianceFlags.forEach((f) => {
      lines.push(`- ${f.severity} 合规 (${f.type}): ${f.reason}${f.suggestion ? ` — 建议: ${f.suggestion}` : ""}`);
    });
  }
  if (task.politicalFlags.length > 0) {
    lines.push("", "## 关键关系");
    task.politicalFlags.forEach((f) => {
      lines.push(`- ${f.severity}: ${f.reason}`);
    });
  }
  if (task.options.length > 0) {
    lines.push("", "## 早晨 LLM 给的 3 个口径建议 (参考, 你可以反驳或调整)");
    task.options.forEach((o) => {
      lines.push(`${o.label} (${o.tone}): ${o.summary}${o.aiLean ? " [早晨 AI 倾向]" : ""}`);
    });
  }
  lines.push(
    "",
    "## 你的工作",
    "- 员工现在跟你直接说. 回答她关于这条待办的具体问题.",
    "- 起草内容 / 帮她做决策 / 给具体下一步.",
    "- 如果她说 '我准备做 A' / '已经做完' / '推迟' 之类的, 提醒她用底部按钮记录状态.",
    "- 简洁回答, 不要重复早晨已给过的建议.",
    "",
    "## 工具使用 (P3.3.10)",
    "- 你能调 tool (catfish_draft_email_reply / catfish_compose_followup_list /",
    "  catfish_check_compliance / catfish_political_sensitivity_scan /",
    "  execute_code / catfish_run_skill 等). 跟工作台 chat 同款.",
    "- 该调就调, 不要装看不到 tool. 起草邮件用 catfish_draft_email_reply, 跑数算用",
    "  execute_code, 写报告/PPT 用 catfish_run_skill.",
    "- 重要 tool (write_file / execute_code / send_email 等) 中央会拦下来弹批准框,",
    "  你只管调, 员工点 '批准' 就放行.",
  );
  return lines.join("\n");
}

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
  const accent =
    task.urgency === "high" ? "#c2410c" :
    task.urgency === "medium" ? "#a16207" : "#6b7280";

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
  // P3.3.11: onPersist 接完整 ChatMessage, 把 tool_calls / tool_call_id 也写 jsonl.
  //   user / assistant (含 tool_calls) / tool 各类 emit. system 不 emit (LLM
  //   每次 send 都重算 system prompt, 不需要持久化).
  const onPersist = useCallback((msg: ChatMessage) => {
    if (msg.role === "system") return;
    void taskChatAppend(
      chatKeyRef.current,
      msg.role as "user" | "assistant" | "tool",
      msg.content,
      {
        toolCalls: msg.tool_calls,
        toolCallId: msg.tool_call_id,
      },
    ).catch((e) => {
      console.warn(`[BriefingTwoColumn] append ${msg.role} 失败:`, e);
    });
  }, []);

  const taskChat = useTaskChat({ model, buildSystemPrompt, onPersist });
  const { messages, isStreaming, send, cancel, loadHistory } = taskChat;

  const [input, setInput] = useState("");
  const [chatError, setChatError] = useState<string | null>(null);
  const [backendError, setBackendError] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // P3.3.10 floating approval banner state (跟 ChatPanel 同款逻辑).
  //   hermes _gateway_approval 阻塞期间 chat completions 不 finalize → 立即弹 banner.
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [submittingChoice, setSubmittingChoice] = useState<
    "once" | "session" | "always" | "deny" | null
  >(null);
  const [secondsLeft, setSecondsLeft] = useState(60);

  // mount 时 load 历史. P3.3.9: 先 try uid, 没历史回退 title.
  useEffect(() => {
    let cancelled = false;
    setHistoryLoading(true);
    void (async () => {
      try {
        // P3.3.14: load 时只取最近 200 条防大 jsonl UI 卡. 老历史仍在文件里,
        //   advisor summary 拉时不传 limit 拿全部 (老脉络才有总结意义).
        let hist = await taskChatGet(chatKey, 200);
        if (hist.length === 0 && chatKey !== task.title) {
          // 兼容 P3.3.9 之前以 title 命名的老 jsonl
          const oldHist = await taskChatGet(task.title, 200).catch(() => []);
          if (oldHist.length > 0) {
            console.log(
              `[BriefingTwoColumn] 老 title jsonl 命中 (${oldHist.length} 条), uid='${chatKey}' title='${task.title}'`,
            );
            hist = oldHist;
          }
        }
        if (cancelled) return;
        // P3.3.11: load 时反序列化 tool_calls + tool_call_id, 并 join tool result
        //   回 assistant.tool_calls[i].result (跟工作台 P27.3 state.db load 同款).
        //   做法: 先全部转 ChatMessage, 然后扫 tool 角色 row 把 content 写回对应
        //   assistant.tool_calls[i].result. tool row 本身不进可见 messages
        //   (ChatMsg 函数会跳过 role === "tool", 跟工作台 ChatMessage 同款).
        const all: ChatMessage[] = hist.map((m, i) => ({
          id: `hist-${i}-${m.ts}`,
          role: m.role as ChatMessage["role"],
          content: m.content,
          tool_calls: m.toolCalls,
          tool_call_id: m.toolCallId,
          ts: m.ts,
          status: "done",
        }));
        // 建 tool_call_id → result content 索引
        const toolResultByCallId = new Map<string, string>();
        for (const m of all) {
          if (m.role === "tool" && m.tool_call_id) {
            toolResultByCallId.set(m.tool_call_id, m.content);
          }
        }
        // join result 回 assistant.tool_calls[i].result
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
      } catch (e) {
        console.warn("[BriefingTwoColumn] load task chat 历史失败:", e);
      } finally {
        if (!cancelled) setHistoryLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // 切 task (chatKey 变) 重 load. loadHistory 是稳定 useCallback ref.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatKey, task.title]);

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
    if (!text || isStreaming) return;
    setChatError(null);
    setInput("");
    try {
      await send(text);
    } catch (e) {
      setChatError(String(e));
    }
  };

  // P3.3.9: clear uid + 也 try clear 老 title file (兼容老 jsonl)
  const handleClearChat = async () => {
    if (!window.confirm("清掉这条待办的所有对话历史? 不可撤销.")) return;
    try {
      cancel();
      await taskChatClear(chatKey);
      if (chatKey !== task.title) {
        await taskChatClear(task.title).catch(() => undefined);
      }
      loadHistory([]);
    } catch (e) {
      console.warn("[BriefingTwoColumn] clear task chat 失败:", e);
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
          {status === "done" && <span className="briefing-2col__badge-done">已完成</span>}
          {status === "snoozed" && <span className="briefing-2col__badge-warn">已推迟</span>}
        </div>
        <h3 className="briefing-2col__detail-title">{task.title}</h3>
      </div>

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
            <span>hermes 等批准</span>
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

      <div className="briefing-2col__chat-input-row">
        <textarea
          className="briefing-2col__chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void handleSend();
            }
          }}
          placeholder={isStreaming ? "AI 回答中…" : "Enter 发送 · Shift+Enter 换行"}
          rows={2}
          disabled={isStreaming}
        />
        <button
          type="button"
          className="briefing-2col__chat-send"
          onClick={() => void handleSend()}
          disabled={!input.trim() || isStreaming}
        >
          {isStreaming ? "…" : "发送"}
        </button>
      </div>

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
