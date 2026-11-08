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
 * - 11/07 派给同事的任务 (kind ctask), 不开对话:
 *     📋 「周报」过了截止，zhang、li 还没交 · 截止 今天 18:00   [催一下] [延期 ▾] [看任务] ✕
 *     📋 「周报」今天 18:00 截止，还没交回 · 周鸿 派的              [去交回] ✕
 * - 11/09 小鲶秘书:
 *     ✅ 「AI六巨头日报」做完了 · 一句话定性：… · 没发到微信：…      [看结果] ✕     (弹窗看全文, 不开对话)
 *     ⚠️ 「资质邮件周扫描」没做成 · 模型超时                     [让小鲶看看] [重跑] ✕
 *     📋 会务安排准备好了：周例会（11/10 10:00）                  [看结果] ✕
 *     🔁 「汇总周报」你每周一都会找我做 · 10/19、10/26、11/2 · 以后每周一 8:00 先替你做好？
 *                                            [好，交给小鲶] [改一改] [不用] ✕
 */
import { useEffect, useRef, useState } from "react";

import { extendOptions, snoozeOptions, type Reminder } from "../../lib/reminders";
import { ROUTINE_DECLINE_MS } from "../../lib/routines";
import { cronJobCreate, cronJobRun, taskMarkCompleted } from "../../lib/tauri";
import { taskExtend, taskRemind } from "../../lib/tauri_colleague_tasks";
import { useRemindersStore, useVisibleReminders } from "../../store/reminders";
import { useUIStore } from "../../store/ui";
import { useAgentStore } from "../../store/agent";
import CronResultDialog from "./CronResultDialog";

const ICONS: Record<Reminder["kind"], string> = {
  deadline: "⏰",
  briefing: "☀️",
  email: "📮",
  silence: "💬",
  focus: "💬",
  ctask: "📋",
  cron: "✅",
  routine: "🔁",
};

function iconFor(r: Reminder): string {
  if (r.kind === "cron" && r.cron?.status === "error") return "⚠️";
  if (r.kind === "cron" && r.cron?.prep) return "📋";
  return ICONS[r.kind];
}

/** 「下次 11/16 8:00」—— 建好托管任务后那句话用 */
function nextRunLabel(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `，第一次 ${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** 标完成后「已标完成」停留多久再收起 */
export const DONE_LINGER_MS = 1000;

type DoneState =
  | { phase: "saving"; reminder: Reminder }
  | { phase: "done"; reminder: Reminder; text?: string }
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
  /** 「看结果」弹窗开着的那条 (打开时已经从提醒条上收掉了, 所以单独存) */
  const [viewing, setViewing] = useState<Reminder | null>(null);
  const agentName = useAgentStore((s) => s.name) || "小鲶";
  const closeMenu = () => setMenu(null);
  const snoozeRef = useDismissable(menu === "snooze", closeMenu);
  const moreRef = useDismissable(menu === "more", closeMenu);
  const lingerTimer = useRef<number | null>(null);

  useEffect(() => () => {
    if (lingerTimer.current) window.clearTimeout(lingerTimer.current);
  }, []);

  // 标完成过程中显示的是那一条 (它可能已经不在 visible 里了)
  const current: Reminder | undefined = done?.reminder ?? visible[index % Math.max(visible.length, 1)];
  const dialog = viewing ? (
    <CronResultDialog
      reminder={viewing}
      agentName={agentName}
      onClose={() => setViewing(null)}
      onContinue={() => {
        useUIStore.getState().startReminderChat(viewing.prompt, viewing.chatTitle);
        setViewing(null);
      }}
    />
  ) : null;
  if (!current) return dialog;
  const others = visible.filter((r) => r.id !== current.id).length;

  const store = useRemindersStore.getState();
  const now = Date.now();
  const snoozes = current.kind === "deadline" ? snoozeOptions(current, now) : [];
  const canDone = current.kind === "deadline" && !!current.task?.taskId;
  const owner = current.ctask?.role === "owner" ? current.ctask : null;
  const extends_ = owner ? extendOptions(new Date(now)) : [];
  const cronFailed = current.kind === "cron" && current.cron?.status === "error" ? current.cron : null;
  const routine = current.routine ?? null;
  const hasSecondary = snoozes.length > 0 || canDone || !!owner || !!cronFailed || !!routine;
  const busy = done?.phase === "saving" || done?.phase === "done";

  const startChat = () => {
    // 定时任务做完了: 弹窗看结果全文, 不开对话 (要接着处理在弹窗里点)
    if (current.kind === "cron" && current.cron?.status !== "error" && current.cron?.outputTs) {
      setViewing(current);
      store.markStarted(current.id);
      setIndex(0);
      return;
    }
    // 托管提议: 直接建定时任务, 显示「建好了」1 秒后收起
    if (current.routine) {
      const r = current.routine;
      void act(() =>
        cronJobCreate({ name: r.jobName, schedule: r.schedule, prompt: r.jobPrompt })
          .then((job) => `建好了：${r.scheduleLabel} 先替你做好${nextRunLabel(job.next_run_at)}`),
      );
      return;
    }
    if (current.ctask) {
      if (current.ctask.role === "owner") void act(() => taskRemind(current.ctask!.taskId).then((r) => `催了 ${r.reminded.length} 人`));
      else openTask();
      return;
    }
    useUIStore.getState().startReminderChat(current.prompt, current.chatTitle);
    store.markStarted(current.id);
    setIndex(0);
  };
  const openTask = () => {
    if (!current.ctask) return;
    setMenu(null);
    useUIStore.getState().openColleagueTask(current.ctask.taskId, current.ctask.role);
    store.markStarted(current.id);
    setIndex(0);
  };
  /** 同事任务上的「催一下」「延期」: 做完显示一句结果, 1 秒后收起 (今天不再提醒这条) */
  const act = async (fn: () => Promise<string>) => {
    setMenu(null);
    const reminder = current;
    setDone({ phase: "saving", reminder });
    let text: string;
    try {
      text = await fn();
    } catch (e) {
      setDone({ phase: "error", reminder, message: e instanceof Error ? e.message : String(e) });
      return;
    }
    setDone({ phase: "done", reminder, text });
    lingerTimer.current = window.setTimeout(() => {
      useRemindersStore.getState().markStarted(reminder.id);
      setDone(null);
      setIndex(0);
    }, DONE_LINGER_MS);
  };
  const rerun = () => {
    if (!cronFailed) return;
    void act(() => cronJobRun(cronFailed.jobId).then(() => "开始重跑了，跑完再告诉你"));
  };
  const tweakRoutine = () => {
    setMenu(null);
    useUIStore.getState().startReminderChat(current.prompt, current.chatTitle);
    store.markStarted(current.id);
    setIndex(0);
  };
  const declineRoutine = () => {
    setMenu(null);
    store.decline(current.id, Date.now() + ROUTINE_DECLINE_MS);
    setDone(null);
    setIndex(0);
  };
  const extend = (o: { label: string; deadline: string }) =>
    void act(() => taskExtend(owner!.taskId, o.deadline).then((r) => `截止改到${o.label}，通知了 ${r.sent.length} 人`));
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
            <span className="reminder-bar__main">{done.text ?? "已标完成"}</span>
            {!done.text && <span className="reminder-bar__meta"> · {current.task?.title}</span>}
          </p>
        </section>
      </div>
    );
  }

  const fullText = current.meta ? `${current.text} · ${current.meta}` : current.text;

  return (
    <>
    {dialog}
    <div className="reminder-bar-slot">
      <section className="reminder-bar" data-kind={current.kind} data-status={current.cron?.status} aria-label="提醒">
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
          <span className="reminder-bar__icon" aria-hidden="true">{iconFor(current)}</span>
          <span className="reminder-bar__main">{current.text}</span>
          {current.meta && <span className="reminder-bar__meta"> · {current.meta}</span>}
          {done?.phase === "error" && (
            <span className="reminder-bar__error" role="alert"> · {current.ctask || current.cron || current.routine ? "没成功" : "没标上"}：{done.message}</span>
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
            {done?.phase === "saving" && (current.ctask || current.cron || current.routine) ? "处理中…" : current.actionLabel}
          </button>
          {cronFailed && (
            <button type="button" className="reminder-bar__secondary reminder-bar__wide-only" onClick={rerun} disabled={busy}
              title="马上再跑一次 (不改排期)">
              重跑
            </button>
          )}
          {routine && (
            <button type="button" className="reminder-bar__secondary reminder-bar__wide-only" onClick={tweakRoutine} disabled={busy}
              title="先跟小鲶商量时间、范围、格式, 再建">
              改一改
            </button>
          )}
          {routine && (
            <button type="button" className="reminder-bar__secondary reminder-bar__wide-only" onClick={declineRoutine} disabled={busy}
              title="这件事 60 天内不再提">
              不用
            </button>
          )}
          {owner && (
            <div className="reminder-bar__anchor reminder-bar__wide-only" ref={snoozeRef}>
              <button
                type="button"
                className="reminder-bar__secondary"
                aria-haspopup="menu"
                aria-expanded={menu === "snooze"}
                onClick={() => setMenu(menu === "snooze" ? null : "snooze")}
                disabled={busy}
                title="只改截止时间, 对方不用重新同意"
              >
                延期 <span aria-hidden="true">▾</span>
              </button>
              {menu === "snooze" && (
                <div className="reminder-bar__menu" role="menu">
                  {extends_.map((o) => (
                    <button key={o.label} type="button" role="menuitem" onClick={() => extend(o)}>
                      延到{o.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {owner && (
            <button type="button" className="reminder-bar__secondary reminder-bar__wide-only" onClick={openTask} disabled={busy}>
              看任务
            </button>
          )}
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
                  {extends_.map((o) => (
                    <button key={o.label} type="button" role="menuitem" onClick={() => extend(o)}>
                      延到{o.label}
                    </button>
                  ))}
                  {owner && (
                    <button type="button" role="menuitem" onClick={openTask}>
                      看任务
                    </button>
                  )}
                  {cronFailed && (
                    <button type="button" role="menuitem" onClick={() => { setMenu(null); rerun(); }}>
                      重跑
                    </button>
                  )}
                  {routine && (
                    <button type="button" role="menuitem" onClick={tweakRoutine}>
                      改一改
                    </button>
                  )}
                  {routine && (
                    <button type="button" role="menuitem" onClick={declineRoutine}>
                      不用
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </section>
    </div>
    </>
  );
}
