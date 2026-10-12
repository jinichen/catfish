/** 提醒同步到微信 (10/12, tool-bridge wechat_push.py)。
 *
 * Windows 上小鲶的提醒存在 Catfish 自己的任务库, 手机看不到。员工绑定过微信后, 本机的小鲶
 * 就是他的微信机器人: 打开开关, 闹钟到点时除了系统通知再推一条到微信。只推标题 + 时间。
 * mac 不显示 —— mac 的提醒写进系统「提醒事项」, 已经通过 iCloud 到 iPhone。
 */
import { useEffect, useState } from "react";

import { currentEmailPlatform } from "../../lib/emailPlatformHints";
import { notifyPrefsGet, notifyPrefsSet, notifyWechatTest, type NotifyPrefs } from "../../lib/tauri_notify";

const card: React.CSSProperties = {
  background: "var(--catfish-bg-elevated)",
  border: "1px solid var(--catfish-border)",
  borderRadius: "var(--radius-md)",
  padding: "var(--space-4)",
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-2)",
};

function when(ts?: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return `${d.getMonth() + 1}-${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export default function WeChatReminderPushCard() {
  const [prefs, setPrefs] = useState<NotifyPrefs | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = () => notifyPrefsGet().then(setPrefs).catch((e) => setMsg({ ok: false, text: String(e) }));
  useEffect(() => {
    void load();
  }, []);

  if (currentEmailPlatform() === "macos") return null;
  if (!prefs) return null;

  async function toggle(on: boolean) {
    setBusy(true);
    setMsg(null);
    try {
      await notifyPrefsSet(on);
      await load();
    } catch (e) {
      setMsg({ ok: false, text: String(e) });
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await notifyWechatTest();
      setMsg(r.ok ? { ok: true, text: "已发出, 看看手机微信有没有收到" } : { ok: false, text: r.error || "发送失败" });
      await load();
    } catch (e) {
      setMsg({ ok: false, text: String(e) });
    } finally {
      setBusy(false);
    }
  }

  const last = prefs.last;
  return (
    <div style={card}>
      <h3 style={{ margin: 0 }}>提醒同步到微信</h3>
      <div style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>
        小鲶的提醒到点时, 除了电脑右下角的通知, 再推一条到你绑定的微信, 不在电脑前也能收到。只发提醒标题和时间。
      </div>
      {!prefs.wechat_bound ? (
        <div style={{ fontSize: 13 }}>先在上面「微信账号绑定」里扫码连上小鲶, 这里才能打开。</div>
      ) : (
        <>
          <label style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", fontSize: 14, cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={prefs.wechat_reminders}
              disabled={busy}
              onChange={(e) => void toggle(e.target.checked)}
            />
            提醒同时推送到微信
          </label>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap" }}>
            <button type="button" className="chat-composer__secondary-button" disabled={busy} onClick={() => void test()}>
              {busy ? "发送中…" : "发一条测试"}
            </button>
            {last?.last_at && (
              <span style={{ fontSize: 12, color: last.ok ? "var(--status-ok)" : "var(--status-err)" }}>
                上次推送 {when(last.last_at)}: {last.ok ? "成功" : last.error}
              </span>
            )}
          </div>
          <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>
            微信那边要求最近跟小鲶说过话; 很久没聊时推送可能失败, 在微信里给小鲶发一句话就恢复。
          </div>
        </>
      )}
      {msg && (
        <div style={{ fontSize: 13, color: msg.ok ? "var(--status-ok)" : "var(--status-err)" }}>{msg.text}</div>
      )}
    </div>
  );
}
