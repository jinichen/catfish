/** 早安播报 — 主菜 WorkplanView (周/日 × 内容/事件/建议), 副菜 EventsDetail + TodosDetail. */

import { useEffect, useState } from "react";

import {
  fetchLlmJournalTodos,
  fetchMergedBriefing,
} from "../../lib/briefing";
import {
  calendarTodayFetch,
  calendarWeekFetch,
  emailClassifyNow,
  emailDigestFetch,
  journalTodosFetch,
  type CalendarEvent,
  type EmailDigestItem,
  type JournalTodo,
} from "../../lib/tauri";
import { useAgentStore } from "../../store/agent";
import { useChatStore } from "../../store/chat";
import { useEmailStore } from "../../store/email";

import { EventsDetailSection } from "../Briefing/components/EventsDetail";
import TodosDetailSection from "../Briefing/components/TodosDetail";
import WorkplanView from "../Briefing/components/WorkplanView";
import { fetchWorkplan, type Workplan } from "../../lib/briefing_workplan";
import { getGreeting } from "../Briefing/components/helpers";

// 30 分钟刷一次未读数 (跟 ProactiveCard 同步, 不主动打扰但保新鲜)
const REFRESH_MS = 30 * 60 * 1000;

export default function BriefingCard() {
  // 5/21 Phase 6: 4 行总览删后, emails / unreadCount / *Error 只 setter 不 read.
  // state 仍保留是因为 loadAll 内调用 setter 给 React 重渲染 + future ProactiveCard 可能用.
  const [, setEmails] = useState<EmailDigestItem[] | null>(null);
  const [, setUnreadCount] = useState<number | null>(null);
  // BL-COMPANION-EMAIL-DIGEST-STEP5 (5/20): urgencyMap 从 useEmailStore 拉, 传给 fetchWorkplan
  const urgencyMap = useEmailStore((s) => s.urgencyMap);
  const reconcileUrgency = useEmailStore((s) => s.reconcileFromRust);
  const setUrgencyMap = useEmailStore((s) => s.setUrgencyMap);
  const [, setEmailError] = useState<string | null>(null);
  // BL-CALENDAR-INTEGRATION (5/20): 真日历数据
  const [calEvents, setCalEvents] = useState<CalendarEvent[] | null>(null);
  const [, setCalError] = useState<string | null>(null);
  // BL-CALENDAR-WEEK (5/20): 未来 7 天 events. 拉了暂未渲染 (workplan LLM 暂只用今日).
  const [, setWeekEvents] = useState<CalendarEvent[] | null>(null);
  // BL-JOURNAL-TODO-EXTRACT (5/20)
  const [todos, setTodos] = useState<JournalTodo[] | null>(null);
  const [, setTodoError] = useState<string | null>(null);
  // BL-BRIEFING-LLM-RANK step2 (5/20) — 5/21 Phase 6 已废弃 suggestion 字符串路径,
  // 仅保留 setLlmLoading 给 useProactiveScheduler 兼容. LLM 推 todos 走 fetchMergedBriefing.
  const [, setLlmLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [lastSync, setLastSync] = useState<Date | null>(null);

  const model = useChatStore((s) => s.model);
  const personality = useAgentStore((s) => s.personality);

  // 5/21 Phase 6: LLM Workplan (周/日双层 × 内容/事件/建议 6 块).
  // 异步 10-20s 跑. 跑出来主渲染 WorkplanView. 没跑出来 → 不渲染该段 (4 行总览 + 详情段仍在).
  const [workplan, setWorkplan] = useState<Workplan | null>(null);
  const [workplanLoading, setWorkplanLoading] = useState(false);

  // forceRefresh=true → ⟳ 按钮主动刷, 跳 calendar Rust 缓存. 自动 interval 走缓存.
  const loadAll = async (forceRefresh = false) => {
    setLoading(true);
    setEmailError(null);
    setCalError(null);
    setTodoError(null);
    // 4 源并发拉, 互不阻塞 (一个挂了另三个仍显)
    const [emailRes, calRes, weekRes, todoRes] = await Promise.allSettled([
      emailDigestFetch(50),
      calendarTodayFetch(forceRefresh),
      calendarWeekFetch(forceRefresh),
      journalTodosFetch(),
    ]);

    // ── email ──
    if (emailRes.status === "fulfilled") {
      try {
        const arr = JSON.parse(emailRes.value);
        if (Array.isArray(arr)) {
          setEmails(arr as EmailDigestItem[]);
          setUnreadCount(arr.length);
        } else {
          setEmails([]);
          setUnreadCount(0);
        }
      } catch {
        setEmails([]);
        setUnreadCount(0);
      }
    } else {
      const e = emailRes.reason;
      setEmailError(e instanceof Error ? e.message : String(e));
      setEmails(null);
      setUnreadCount(null);
    }

    // ── calendar (今日) ──
    if (calRes.status === "fulfilled") {
      try {
        const arr = JSON.parse(calRes.value);
        setCalEvents(Array.isArray(arr) ? (arr as CalendarEvent[]) : []);
      } catch {
        setCalEvents([]);
      }
    } else {
      const e = calRes.reason;
      setCalError(e instanceof Error ? e.message : String(e));
      setCalEvents(null);
    }

    // ── calendar (本周) — 失败不挡卡 ──
    if (weekRes.status === "fulfilled") {
      try {
        const arr = JSON.parse(weekRes.value);
        setWeekEvents(Array.isArray(arr) ? (arr as CalendarEvent[]) : []);
      } catch {
        setWeekEvents([]);
      }
    } else {
      setWeekEvents(null);
    }

    // ── journal TODO ──
    if (todoRes.status === "fulfilled") {
      try {
        const arr = JSON.parse(todoRes.value);
        setTodos(Array.isArray(arr) ? (arr as JournalTodo[]) : []);
      } catch {
        setTodos([]);
      }
    } else {
      const e = todoRes.reason;
      setTodoError(e instanceof Error ? e.message : String(e));
      setTodos(null);
    }

    setLastSync(new Date());
    setLoading(false);

    // ── LLM 邮件评级 (BL-COMPANION-EMAIL-DIGEST-STEP5 5/20 走 store) ──
    if (emailRes.status === "fulfilled") {
      try {
        const arr = JSON.parse(emailRes.value);
        if (Array.isArray(arr) && arr.length > 0) {
          const cached = await reconcileUrgency();
          const unrated = (arr as EmailDigestItem[]).filter((m) => !cached[m.id]);
          if (unrated.length > 0) {
            void emailClassifyNow(unrated).then((newMap) => setUrgencyMap(newMap)).catch(() => {});
          }
        }
      } catch {
        /* ignore */
      }
    }

    // ── LLM 合并建议 (BL-BRIEFING-LLM-MERGE 5/20) ──
    setLlmLoading(true);
    const unread = emailRes.status === "fulfilled"
      ? (() => { try { const a = JSON.parse(emailRes.value); return Array.isArray(a) ? a.length : 0; } catch { return 0; } })()
      : 0;
    const evts = calRes.status === "fulfilled"
      ? (() => { try { const a = JSON.parse(calRes.value); return Array.isArray(a) ? (a as CalendarEvent[]) : []; } catch { return []; } })()
      : [];
    const regexTodos = todoRes.status === "fulfilled"
      ? (() => { try { const a = JSON.parse(todoRes.value); return Array.isArray(a) ? (a as JournalTodo[]) : []; } catch { return []; } })()
      : [];
    const localEmails = emailRes.status === "fulfilled"
      ? (() => { try { const a = JSON.parse(emailRes.value); return Array.isArray(a) ? (a as EmailDigestItem[]) : []; } catch { return []; } })()
      : [];
    const urgentHints = localEmails
      .filter((m) => {
        const u = urgencyMap[m.id];
        return u === "急" || u?.toLowerCase() === "urgent";
      })
      .map((m) => ({ subject: m.subject, sender: m.sender }));

    // 5/21 Phase 6 大清: 不再读 suggestion 字符串, 只要 llmTodos 推断结果合并到 regex todos.
    // fetchBriefingSuggestion 路径在 useProactiveScheduler 仍用 (proactive 浮窗), BriefingCard 不调.
    let llmTodos: JournalTodo[] = [];
    const merged = await fetchMergedBriefing(unread, evts, regexTodos, model, urgentHints, personality);
    if (merged) {
      llmTodos = merged.todos;
      console.log("[BriefingCard] merged LLM ✅", { todoCount: llmTodos.length });
    } else {
      console.log("[BriefingCard] merged LLM 挂, fallback fetchLlmJournalTodos");
      try {
        llmTodos = await fetchLlmJournalTodos(regexTodos, model, personality);
      } catch (e) {
        console.warn("[BriefingCard] fetchLlmJournalTodos 也挂:", e);
      }
    }

    // TODO 合并: regex (确切) + LLM (推断), 按 text 去重
    if (llmTodos.length > 0) {
      const seen = new Set(regexTodos.map((t) => t.text.trim().toLowerCase()));
      const newOnes = llmTodos.filter((t) => !seen.has(t.text.trim().toLowerCase()));
      if (newOnes.length > 0) {
        setTodos([...regexTodos, ...newOnes]);
      }
    }

    setLlmLoading(false);

    // 5/21 Phase 6: 异步跑 LLM Workplan (周/日双层综合判断).
    // 不阻塞 loadAll. 失败保持 workplan=null → 只显 4 行总览 + 详情段, 不渲染 WorkplanView.
    setWorkplanLoading(true);
    const finalEmails = localEmails;
    const finalEvents = evts;
    const finalTodos = llmTodos.length > 0
      ? [...regexTodos, ...llmTodos.filter((t) =>
          !regexTodos.some((r) => r.text.trim().toLowerCase() === t.text.trim().toLowerCase())
        )]
      : regexTodos;
    void fetchWorkplan(finalEmails, finalEvents, finalTodos, urgencyMap, model)
      .then((w) => setWorkplan(w))
      .catch((e) => {
        console.warn("[BriefingCard] workplan 异常:", e);
        setWorkplan(null);
      })
      .finally(() => setWorkplanLoading(false));
  };

  useEffect(() => {
    void loadAll();
    const t = window.setInterval(() => void loadAll(), REFRESH_MS);
    return () => window.clearInterval(t);
  }, []);

  const greeting = getGreeting();
  const today = new Date().toLocaleDateString("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "long",
  });

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        alignSelf: "start",
      }}
    >
      {/* Header: 问候 + 日期 + 刷新按钮.
          5/21 L3 化简: 删 ☀️ emoji, 刷新按钮文字化 ('刷新' 替代 '⟳') */}
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          marginBottom: "var(--space-3)",
        }}
      >
        <strong style={{ fontSize: 15, color: "var(--catfish-text)" }}>{greeting}</strong>
        <span style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>{today}</span>
        <button
          type="button"
          onClick={() => void loadAll(true)}
          disabled={loading}
          title="刷新今日总览"
          style={{
            marginLeft: "auto",
            background: "transparent",
            border: "1px solid var(--catfish-border)",
            borderRadius: "var(--radius-sm)",
            color: "var(--catfish-text-muted)",
            cursor: loading ? "wait" : "pointer",
            fontSize: 12,
            padding: "3px 10px",
            fontFamily: "inherit",
          }}
        >
          {loading ? "同步中…" : "刷新"}
        </button>
      </header>

      {/* 周/日双层 Workplan — LLM 综合 7 数据源.
          跑出来 → WorkplanView 渲染; loading → 占位卡; 失败 → 不显, 走下面详情段. */}
      {workplan ? (
        <WorkplanView workplan={workplan} />
      ) : workplanLoading ? (
        <div
          style={{
            marginTop: "var(--space-4)",
            padding: "16px 18px",
            color: "var(--catfish-text-muted)",
            fontSize: 13,
            fontStyle: "italic",
            textAlign: "center",
            background: "var(--catfish-bg)",
            borderRadius: "var(--radius-sm)",
            border: "1px dashed var(--catfish-border)",
          }}
        >
          小鲶在综合判断你本周和今天的工作…
        </div>
      ) : null}

      {/* 详情区: 日历 ≤ 3 件直接展开 (鸿波: '1 件 1 句话没用'); 工作计划默认收起, 可标完成/删除. */}
      {(calEvents?.length ?? 0) > 0 && (
        <EventsDetailSection
          events={calEvents!}
          defaultOpen={(calEvents?.length ?? 0) <= 3}
        />
      )}
      {(todos?.length ?? 0) > 0 && (
        <TodosDetailSection todos={todos!} onChanged={() => void loadAll(true)} />
      )}

      {/* footer: 同步时间 */}
      {lastSync && !loading && (
        <div
          style={{
            fontSize: 10,
            color: "var(--catfish-text-muted)",
            opacity: 0.6,
            textAlign: "right",
            marginTop: "var(--space-2)",
          }}
        >
          上次同步 {lastSync.toLocaleTimeString("zh-CN", {
            hour: "2-digit",
            minute: "2-digit",
            hour12: false,
          })}
        </div>
      )}
    </div>
  );
}
