/** 等批准横幅 —— 工作台 ChatPanel 和早安卡片 BriefingDetailPane 共用 (10/16)。
 *
 * 两边原来各抄了一份 (监听 + 倒计时 + 送回执 + 整段 JSX 约 85 行 ×2)。抄出来的两份已经不一样了:
 * 卡片那份用的 `approval-banner__btn-secondary` / `__btn-danger` 两个 class 在 globals.css 里
 * 根本不存在 (「始终允许」「拒绝」两个按钮一直没样式), 按钮上也没有 loading 转圈。
 * 10/16 要给两边同时加"失败有话说 / 切走有通知 / 超时留痕", 再抄一遍只会继续分叉, 所以抽成一个。
 *
 * 10/26: 数据搬去 store/approvals.ts (全局一份, 带会话); 这里只按会话显示。
 *
 * 用法:
 *   <ApprovalBanner sessionId={当前会话} onRetry={(text) => 发进这段对话} />   // 放在输入框上面
 *   <ApprovalElsewhereBanner />                                                // 其它页面顶上
 */
import { useEffect, useState } from "react";

import { asksEveryTime, everyTimeReason } from "../lib/approvalScope";
import {
  belongsTo, decideApproval, dismissApprovalNote, retryApprovalNote, useApprovalsStore, type ApprovalItem,
} from "../store/approvals";
import { useUIStore } from "../store/ui";

/** 倒计时: 一分钟以上显「29:59」, 以下显「59s」。 */
function countdownText(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** 有条目时每秒刷一次 (倒计时) */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

function PendingCard({ item, now, submitting }: { item: ApprovalItem; now: number; submitting?: string }) {
  const secondsLeft = Math.max(0, Math.ceil((item.deadline - now) / 1000));
  const busy = submitting !== undefined;
  return (
    <div className="approval-banner">
      <div className="approval-banner__header">
        {/* P3.5.73 (6/22 鸿波 catch): "hermes 等批准" → "等批准". SOUL.md L5 品牌红线
            "对外永不说: 我是 Hermes". 后面 pattern chip 自带 tool 名, 不需要前缀。 */}
        <span>等批准</span>
        <code className="pattern">{item.patternKey}</code>
        <span className="countdown" aria-live="off">{countdownText(secondsLeft)}</span>
      </div>
      {asksEveryTime(item.description) && (
        <div className="approval-banner__note">{everyTimeReason(item.description)}</div>
      )}
      <div className="approval-banner__code">{item.command}</div>
      {/* 10/15: 「本会话允许」去掉 (hermes 只管这一条消息, 跟字面意思不符);
          删除类每次都问, 不给「始终允许」—— 见 lib/approvalScope.ts */}
      <div className="approval-banner__actions">
        <button
          className="approval-banner__btn-primary"
          onClick={() => void decideApproval(item.key, "once")}
          disabled={busy}
          autoFocus
        >
          {submitting === "once" && <span className="approval-banner__spinner" />}
          批准
        </button>
        {!asksEveryTime(item.description) && (
          <button
            className="approval-banner__btn-link"
            onClick={() => void decideApproval(item.key, "always")}
            disabled={busy}
            title="以后这类操作不再问 (删除等破坏性操作除外)"
          >
            {submitting === "always" && <span className="approval-banner__spinner" />}
            始终允许
          </button>
        )}
        <button
          className="approval-banner__btn-deny"
          onClick={() => void decideApproval(item.key, "deny")}
          disabled={busy}
        >
          {submitting === "deny" && <span className="approval-banner__spinner" />}
          拒绝
        </button>
      </div>
    </div>
  );
}

/** 别的对话有要批准 / 作废了的: 提一句, 点了切过去 (那边的横幅才是它的)。 */
function ElsewhereLine({ current }: { current: string | null }) {
  const items = useApprovalsStore((s) => s.items);
  const notes = useApprovalsStore((s) => s.notes);
  const openChatSession = useUIStore((s) => s.openChatSession);
  const waiting = items.filter((i) => i.sessionId !== null && i.sessionId !== current);
  const lapsed = notes.filter((n) => n.sessionId !== null && n.sessionId !== current && n.command !== null);
  const target = waiting[0]?.sessionId ?? lapsed[0]?.sessionId;
  if (!target) return null;
  const text = waiting.length > 0
    ? `另外有 ${waiting.length} 步在别的对话里等你批准`
    : "别的对话里有一步等批准超时作废了";
  return (
    <div className="approval-banner approval-banner--note" role="status">
      <div className="approval-banner__note">{text}</div>
      <div className="approval-banner__actions">
        <button className="approval-banner__btn-primary" onClick={() => openChatSession(target)}>去看看</button>
      </div>
    </div>
  );
}

export function ApprovalBanner({ sessionId, onRetry }: { sessionId: string | null; onRetry?: (text: string) => void }) {
  const items = useApprovalsStore((s) => s.items);
  const notes = useApprovalsStore((s) => s.notes);
  const submitting = useApprovalsStore((s) => s.submitting);
  const mine = items.filter((i) => belongsTo(i.sessionId, sessionId));
  const myNotes = notes.filter((n) => belongsTo(n.sessionId, sessionId));
  const now = useNow(mine.length > 0);

  // 「批准并继续」只在作废的那段对话里给 (myNotes 已按会话筛过), 发进的就是这段对话
  const retry = (id: string) => {
    const prompt = retryApprovalNote(id);
    if (prompt && onRetry) onRetry(prompt);
  };

  return (
    <>
      {/* 批准失败 / 等超时留下的一句话 —— 不点掉不消失, 免得又是"悄悄没了" */}
      {myNotes.map((n) => (
        <div key={n.id} className="approval-banner approval-banner--note" role="status">
          <div className="approval-banner__note">{n.text}</div>
          {n.command && <div className="approval-banner__code">{n.command}</div>}
          <div className="approval-banner__actions">
            {n.command !== null && onRetry && (
              <button
                className="approval-banner__btn-primary"
                onClick={() => retry(n.id)}
                title="让小鲶重做刚才那一步; 它重做时弹出的同一条命令直接算你批准了 (只这一次)"
              >
                批准并继续
              </button>
            )}
            <button className="approval-banner__btn-link" onClick={() => dismissApprovalNote(n.id)}>知道了</button>
          </div>
        </div>
      ))}
      {mine.map((item) => (
        <PendingCard key={item.key} item={item} now={now} submitting={submitting[item.key]} />
      ))}
      <ElsewhereLine current={sessionId} />
    </>
  );
}

/** 工作台 / 早安以外的页面: 有要批准的就在顶上说一声 (原来人在别的页面就完全看不到)。 */
export function ApprovalElsewhereBanner() {
  const items = useApprovalsStore((s) => s.items);
  const openChatSession = useUIStore((s) => s.openChatSession);
  const setActiveTab = useUIStore((s) => s.setActiveTab);
  if (items.length === 0) return null;
  const first = items[0];
  const preview = first.command.length > 60 ? `${first.command.slice(0, 60)}…` : first.command;
  return (
    <div className="approval-banner approval-banner--note" role="status">
      <div className="approval-banner__note">
        小鲶有 {items.length} 步在等你批准{preview ? `: ${preview}` : ""}
      </div>
      <div className="approval-banner__actions">
        <button
          className="approval-banner__btn-primary"
          onClick={() => (first.sessionId ? openChatSession(first.sessionId) : setActiveTab("chat"))}
        >
          去处理
        </button>
      </div>
    </div>
  );
}
