/** 提醒条 (11/07) —— 输入框上面一条, 放主动提醒。
 *
 * 不进消息流、不写进任何会话、不切页面 (原来的 startProactiveChat 三样全做了, 见 lib/reminders.ts 头注释)。
 *
 *   ⏰ 待办「11/9 材料闭环」明天 18:00 到期 · 来自 资质集采二期
 *                [让小鲶看看还差啥]  [稍后提醒 ▾]  [已完成]  ✕
 *
 * - 主按钮: 新开一段对话, 第一句用户消息写清是哪条提醒、什么事实 (ui.startReminderChat → ChatTab)
 * - 稍后提醒: 2 小时后 / 明天上午 9 点 (持久化, 重启也算数)
 * - 已完成: 写回任务库 (catfish_create_task status=completed), 显「已标完成」1 秒后收起
 * - ✕: 今天不再提醒这条, 并让触发器静默 30 分钟
 * - 同时挂着几条: 只显示最急的, 「还有 N 条」逐条翻
 * - 窄: 字一行、按钮折到第二行; 很窄: 只留主按钮 + 「…」菜单 + ✕ (reminders.css 容器查询)
 */
import { useEffect, useRef, useState } from "react";

import { snoozeOptions, type Reminder } from "../../lib/reminders";
import { taskMarkCompleted } from "../../lib/tauri";
import { useRemindersStore, useVisibleReminders } from "../../store/reminders";
import { useUIStore } from "../../store/ui";

const ICONS: Record<Reminder["kind"], string> = {
  deadline: "⏰",
  briefing: "☀️",
  email: "📮",
  silence: "💬",
  focus: "💬",
};

/** 标完成后「已标完成」停留多久再收起 */
export const DONE_LINGER_MS = 1000;

type DoneState =
  | { phase: "saving"; reminder: Reminder }
  | { phase: "done"; reminder: Reminder }
  | { phase: "error"; reminder: Reminder; message: string };

/** 点外面 / 按 Esc 关菜单 */
function useDismissable(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open, close]);
  return ref;
}

export default function ReminderBar() {
  const visible = useVisibleReminders();
  const [index, setIndex] = useState(0);
  const [menu, setMenu] = useState<null | "snooze" | "more">(null);
  const [done, setDone] = useState<DoneState | null>(null);
  const closeMenu = () => setMenu(null);
  const snoozeRef = useDismissable(menu === "snooze", closeMenu);
  const moreRef = useDismissable(menu === "more", closeMenu);
  const lingerTimer = useRef<number | null>(null);

  useEffect(() => () => {
    if (lingerTimer.current) window.clearTimeout(lingerTimer.current);
  }, []);

  // 标完成过程中显示的是那一条 (它可能已经不在 visible 里了)
  const current: Reminder | undefined = done?.reminder ?? visible[index % Math.max(visible.length, 1)];
  if (!current) return null;
  const others = visible.filter((r) => r.id !== current.id).length;

  const store = useRemindersStore.getState();
  const now = Date.now();
  const snoozes = current.kind === "deadline" ? snoozeOptions(current, now) : [];
  const canDone = current.kind === "deadline" && !!current.task?.taskId;
  const hasSecondary = snoozes.length > 0 || canDone;
  const busy = done?.phase === "saving" || done?.phase === "done";

  const startChat = () => {
    useUIStore.getState().startReminderChat(current.prompt, current.chatTitle);
    store.markStarted(current.id);
    setIndex(0);
  };
  const snooze = (until: number) => {
    store.snooze(current.id, until);
    setMenu(null);
    setIndex(0);
  };
  const dismiss = () => {
    store.dismiss(current.id);
    setDone(null);
    setIndex(0);
  };
  const markDone = async () => {
    const task = current.task;
    if (!task?.taskId) return;
    setMenu(null);
    const reminder = current;
    setDone({ phase: "saving", reminder });
    try {
      await taskMarkCompleted(task.taskId, task.title);
    } catch (e) {
      setDone({ phase: "error", reminder, message: e instanceof Error ? e.message : String(e) });
      return;
    }
    setDone({ phase: "done", reminder });
    lingerTimer.current = window.setTimeout(() => {
      useRemindersStore.getState().markDone(reminder.id);
      setDone(null);
      setIndex(0);
    }, DONE_LINGER_MS);
  };

  if (done?.phase === "done") {
    return (
      <div className="reminder-bar-slot">
        <section className="reminder-bar reminder-bar--done" data-kind={current.kind} aria-label="提醒" role="status">
          <p className="reminder-bar__text">
            <span className="reminder-bar__icon" aria-hidden="true">✓</span>
            <span className="reminder-bar__main">已标完成</span>
            <span className="reminder-bar__meta"> · {current.task?.title}</span>
          </p>
        </section>
      </div>
    );
  }

  const fullText = current.meta ? `${current.text} · ${current.meta}` : current.text;

  return (
    <div className="reminder-bar-slot">
      <section className="reminder-bar" data-kind={current.kind} aria-label="提醒">
        {/* ✕ 钉在右上角, 不跟按钮一起折行 (窄的时候会单独掉到第三行) */}
        <button
          type="button"
          className="reminder-bar__close"
          onClick={dismiss}
          aria-label="关掉这条提醒"
          title="关掉 (今天不再提醒这条)"
          disabled={busy}
        >
          ✕
        </button>
        <p className="reminder-bar__text" title={fullText}>
          <span className="reminder-bar__icon" aria-hidden="true">{ICONS[current.kind]}</span>
          <span className="reminder-bar__main">{current.text}</span>
          {current.meta && <span className="reminder-bar__meta"> · {current.meta}</span>}
          {done?.phase === "error" && (
            <span className="reminder-bar__error" role="alert"> · 没标上：{done.message}</span>
          )}
        </p>
        <div className="reminder-bar__actions">
          {others > 0 && (
            <button
              type="button"
              className="reminder-bar__step"
              onClick={() => {
                setMenu(null);
                setDone(null);
                setIndex((i) => (i + 1) % visible.length);
              }}
              title="看下一条提醒"
            >
              还有 {others} 条 ›
            </button>
          )}
          <button type="button" className="reminder-bar__primary" onClick={startChat} disabled={busy}>
            {current.actionLabel}
          </button>
          {snoozes.length > 0 && (
            <div className="reminder-bar__anchor reminder-bar__wide-only" ref={snoozeRef}>
              <button
                type="button"
                className="reminder-bar__secondary"
                aria-haspopup="menu"
                aria-expanded={menu === "snooze"}
                onClick={() => setMenu(menu === "snooze" ? null : "snooze")}
                disabled={busy}
              >
                稍后提醒 <span aria-hidden="true">▾</span>
              </button>
              {menu === "snooze" && (
                <div className="reminder-bar__menu" role="menu">
                  {snoozes.map((o) => (
                    <button key={o.label} type="button" role="menuitem" onClick={() => snooze(o.until)}>
                      {o.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {canDone && (
            <button
              type="button"
              className="reminder-bar__secondary reminder-bar__wide-only"
              onClick={() => void markDone()}
              disabled={busy}
              title="在任务库里把这条标成已完成"
            >
              {done?.phase === "saving" ? "标记中…" : "已完成"}
            </button>
          )}
          {hasSecondary && (
            <div className="reminder-bar__anchor reminder-bar__narrow-only" ref={moreRef}>
              <button
                type="button"
                className="reminder-bar__secondary reminder-bar__more"
                aria-label="更多操作"
                aria-haspopup="menu"
                aria-expanded={menu === "more"}
                onClick={() => setMenu(menu === "more" ? null : "more")}
                disabled={busy}
              >
                …
              </button>
              {menu === "more" && (
                <div className="reminder-bar__menu" role="menu">
                  {snoozes.map((o) => (
                    <button key={o.label} type="button" role="menuitem" onClick={() => snooze(o.until)}>
                      {o.label}再提醒
                    </button>
                  ))}
                  {canDone && (
                    <button type="button" role="menuitem" onClick={() => void markDone()}>
                      已完成
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
