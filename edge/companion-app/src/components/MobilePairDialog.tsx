/** 有手机请求连接这台电脑 → 员工核对验证码后允许 (docs/MOBILE-CLIENT-DESIGN.md §4)。
 *
 * 验证码是两边各自用「手机公钥 + 电脑公钥」算出来的: 中转站要是换了钥匙, 两边就对不上。
 * 所以**一定要员工看着手机核对**, 不能自动允许。挂在 App 顶层, 在哪个页面都能弹。
 */
import { useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";

import {
  MOBILE_PAIR_FAILED_EVENT,
  MOBILE_PAIR_REQUEST_EVENT,
  isStale,
  mobilePairDecide,
  mobileStatus,
  replaceOrder,
  type PairedPhone,
  type PendingPair,
} from "../lib/mobile";

export default function MobilePairDialog() {
  const [queue, setQueue] = useState<PendingPair[]>([]);
  const [failed, setFailed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 到上限时: 已配的手机 (最久没用的在前) 和选中要替换的那台
  const [phones, setPhones] = useState<PairedPhone[]>([]);
  const [replace, setReplace] = useState<string>("");

  useEffect(() => {
    let alive = true;
    // 窗口没开着时来的请求: 打开后补上
    mobileStatus().then((s) => { if (alive && s.pending.length) setQueue(s.pending); }).catch(() => undefined);
    const un1 = listen<PendingPair>(MOBILE_PAIR_REQUEST_EVENT, (e) => {
      setQueue((q) => [...q.filter((p) => p.device !== e.payload.device), e.payload]);
    });
    const un2 = listen<{ reason: string }>(MOBILE_PAIR_FAILED_EVENT, (e) => setFailed(e.payload.reason));
    return () => {
      alive = false;
      void un1.then((f) => f());
      void un2.then((f) => f());
    };
  }, []);

  const current = queue[0];
  const overLimit = !!current?.overLimit;
  useEffect(() => {
    if (!overLimit) return;
    mobileStatus().then((s) => {
      const list = replaceOrder(s.phones);
      setPhones(list);
      setReplace(list[0]?.device ?? "");
    }).catch(() => undefined);
  }, [overLimit, current?.device]);

  if (!current && !failed) return null;

  const decide = async (allow: boolean) => {
    if (!current) return;
    setBusy(true);
    setError(null);
    try {
      await mobilePairDecide(current.device, allow, allow && overLimit ? replace : undefined);
      setQueue((q) => q.slice(1));
    } catch (e) {
      setError(String(e));
      setQueue((q) => q.slice(1));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)", display: "flex",
      alignItems: "center", justifyContent: "center", zIndex: 210 }}>
      <div role="dialog" aria-label="手机请求连接" style={{ background: "var(--catfish-bg-elevated)",
        padding: "var(--space-6)", borderRadius: "var(--radius-md)", width: 420, boxShadow: "var(--shadow-md)" }}>
        {current ? (
          <>
            <h3 style={{ marginTop: 0 }}>📱 有一台手机请求连接</h3>
            <p style={{ fontSize: 13 }}>
              <b>{current.name}</b> 想连这台电脑上的小鲶。连上以后, 它能跟小鲶对话、批准小鲶要做的事 (只能单次)、看早安简报。
            </p>
            <p style={{ fontSize: 13, color: "var(--catfish-text-muted)", marginBottom: 4 }}>
              看一下手机上显示的验证码, 跟下面一样才点允许:
            </p>
            <div aria-label="验证码" style={{ fontSize: 34, letterSpacing: 8, fontWeight: 600, textAlign: "center",
              fontFamily: "var(--font-mono, monospace)", margin: "8px 0 12px" }}>
              {current.code}
            </div>
            <p style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>
              不是你自己的手机、或者数字对不上, 就点拒绝。以后可以在「设置 → 隐私 / 本机数据 → 手机」里撤销。
            </p>
            {overLimit && (
              <div role="group" aria-label="替换哪一台" style={{ fontSize: 13, margin: "8px 0", padding: 8,
                border: "1px solid var(--catfish-border, #ddd)", borderRadius: "var(--radius-md)" }}>
                <div style={{ marginBottom: 6 }}>
                  已经配了 {current.maxPhones ?? phones.length} 台手机 (单位上限)。允许这台, 要替换掉一台旧的:
                </div>
                {phones.map((p) => (
                  <label key={p.device} style={{ display: "flex", gap: 6, alignItems: "center", padding: "2px 0" }}>
                    <input type="radio" name="replace" checked={replace === p.device} onChange={() => setReplace(p.device)} />
                    {p.name}
                    <span style={{ color: "var(--catfish-text-muted)", fontSize: 12 }}>
                      · {p.lastSeen ? `最后用 ${new Date(p.lastSeen).toLocaleDateString()}` : "没连过"}
                      {isStale(p) ? " · 很久没用" : ""}
                    </span>
                  </label>
                ))}
                {phones.length === 0 && (
                  <div style={{ color: "var(--catfish-text-muted)" }}>
                    这台电脑上没有配过的手机 (名额被别的电脑占了), 请到那台电脑上撤销一台。
                  </div>
                )}
              </div>
            )}
            {error && <p role="alert" style={{ fontSize: 12, color: "var(--status-err)" }}>{error}</p>}
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: "var(--space-4)" }}>
              <button type="button" onClick={() => void decide(false)} disabled={busy}>拒绝</button>
              <button type="button" className="primary" onClick={() => void decide(true)}
                disabled={busy || (overLimit && !replace)}>
                {overLimit ? "数字一样, 替换并允许" : "数字一样, 允许"}
              </button>
            </div>
          </>
        ) : (
          <>
            <h3 style={{ marginTop: 0 }}>手机没有配上</h3>
            <p role="alert" style={{ fontSize: 13 }}>{failed}</p>
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <button type="button" onClick={() => setFailed(null)}>知道了</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
