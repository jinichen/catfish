/** 等批准横幅 —— 工作台 ChatPanel 和早安卡片 BriefingDetailPane 共用 (10/16)。
 *
 * 两边原来各抄了一份 (监听 + 倒计时 + 送回执 + 整段 JSX 约 85 行 ×2)。抄出来的两份已经不一样了:
 * 卡片那份用的 `approval-banner__btn-secondary` / `__btn-danger` 两个 class 在 globals.css 里
 * 根本不存在 (「始终允许」「拒绝」两个按钮一直没样式), 按钮上也没有 loading 转圈。
 * 10/16 要给两边同时加"失败有话说 / 切走有通知 / 超时留痕", 再抄一遍只会继续分叉, 所以抽成一个。
 *
 * 用法:
 *   const approval = useApprovalBanner();
 *   <ApprovalBanner {...approval} />     // 放在输入框上面
 */
import { useEffect, useState } from "react";

import {
  APPROVAL_TIMEOUT_NOTE, approvalErrorMessage, approvalNotification, shouldNotifyApproval,
} from "../lib/approvalOutcome";
import { asksEveryTime, everyTimeReason } from "../lib/approvalScope";
import type { PendingApprovalDetail } from "../lib/nativeApprovals";
import { sendNotification, toolBridgeChatApproval } from "../lib/tauri";

type Choice = "once" | "session" | "always" | "deny";

export interface ApprovalBannerState {
  pending: PendingApprovalDetail | null;
  secondsLeft: number;
  note: string | null;
  submitting: Choice | null;
  dismissNote: () => void;
  decide: (choice: Choice) => Promise<void>;
}

/** 监听 `catfish:approval-pending`、倒计时、送回执。两个界面各自调一次 (各管各的横幅)。 */
export function useApprovalBanner(): ApprovalBannerState {
  const [pending, setPending] = useState<PendingApprovalDetail | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(60);
  const [submitting, setSubmitting] = useState<Choice | null>(null);
  // 10/16: 批准失败 / 等超时都要留一句话在界面上 —— 原来只 console.warn + 收横幅, 员工以为批了
  const [note, setNote] = useState<string | null>(null);

  // chat.ts streamChat 解 hermes SSE 看到 approval.request 时 dispatch
  useEffect(() => {
    const handler = (e: Event) => {
      const ev = e as CustomEvent<PendingApprovalDetail>;
      if (!ev.detail?.approval_session_key) return;
      setPending(ev.detail);
      // 10/14 hermes v0.22 原生审批默认等 300 秒 (事件里带 timeout_s); 老插件那套是 60
      setSecondsLeft(ev.detail.timeout_s ?? 60);
      setNote(null);
      // 10/16: 横幅只在这个界面里, 人在别的标签页 / 别的 App 就完全不知道要批准,
      // 等满 5 分钟自动拒绝 (实况见 lib/approvalOutcome.ts 文件头)。窗口在眼前就不弹。
      if (shouldNotifyApproval(typeof document === "undefined" ? undefined : document)) {
        const n = approvalNotification(ev.detail.command ?? "");
        void sendNotification(n.title, n.body).catch(() => {});
      }
    };
    window.addEventListener("catfish:approval-pending", handler as EventListener);
    return () => window.removeEventListener("catfish:approval-pending", handler as EventListener);
  }, []);

  // 倒计时到 0 = hermes 那边也超时了 (自动拒绝), 横幅没意义, 换成一行说明留着
  useEffect(() => {
    if (!pending) return;
    const tick = setInterval(() => {
      setSecondsLeft((s) => {
        if (s <= 1) {
          clearInterval(tick);
          setNote(APPROVAL_TIMEOUT_NOTE);
          setPending(null);
          return 0;
        }
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(tick);
  }, [pending]);

  const decide = async (choice: Choice) => {
    if (!pending || submitting) return;
    setSubmitting(choice);
    try {
      await toolBridgeChatApproval(pending.approval_session_key, choice);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn("[approval] 送回执失败:", err);
      setNote(approvalErrorMessage(err));
    }
    setSubmitting(null);
    setPending(null);
  };

  return { pending, secondsLeft, note, submitting, dismissNote: () => setNote(null), decide };
}

export function ApprovalBanner({ pending, secondsLeft, note, submitting, dismissNote, decide }: ApprovalBannerState) {
  return (
    <>
      {/* 批准失败 / 等超时留下的一句话 —— 不点掉不消失, 免得又是"悄悄没了" */}
      {note && (
        <div className="approval-banner approval-banner--note" role="status">
          <div className="approval-banner__note">{note}</div>
          <div className="approval-banner__actions">
            <button className="approval-banner__btn-link" onClick={dismissNote}>知道了</button>
          </div>
        </div>
      )}
      {pending && (
        <div className="approval-banner">
          <div className="approval-banner__header">
            {/* P3.5.73 (6/22 鸿波 catch): "hermes 等批准" → "等批准". SOUL.md L5 品牌红线
                "对外永不说: 我是 Hermes". 后面 pattern chip 自带 tool 名, 不需要前缀。 */}
            <span>等批准</span>
            <code className="pattern">{pending.pattern_key}</code>
            <span className="countdown" aria-live="polite">{secondsLeft}s</span>
          </div>
          {asksEveryTime(pending.description) && (
            <div className="approval-banner__note">{everyTimeReason(pending.description)}</div>
          )}
          <div className="approval-banner__code">{pending.command}</div>
          {/* 10/15: 「本会话允许」去掉 (hermes 只管这一条消息, 跟字面意思不符);
              删除类每次都问, 不给「始终允许」—— 见 lib/approvalScope.ts */}
          <div className="approval-banner__actions">
            <button
              className="approval-banner__btn-primary"
              onClick={() => void decide("once")}
              disabled={submitting !== null}
              autoFocus
            >
              {submitting === "once" && <span className="approval-banner__spinner" />}
              批准
            </button>
            {!asksEveryTime(pending.description) && (
              <button
                className="approval-banner__btn-link"
                onClick={() => void decide("always")}
                disabled={submitting !== null}
                title="以后这类操作不再问 (删除等破坏性操作除外)"
              >
                {submitting === "always" && <span className="approval-banner__spinner" />}
                始终允许
              </button>
            )}
            <button
              className="approval-banner__btn-deny"
              onClick={() => void decide("deny")}
              disabled={submitting !== null}
            >
              {submitting === "deny" && <span className="approval-banner__spinner" />}
              拒绝
            </button>
          </div>
        </div>
      )}
    </>
  );
}
