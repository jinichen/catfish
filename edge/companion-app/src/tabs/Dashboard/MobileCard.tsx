/** 设置 → 隐私 / 本机数据 → 手机 (docs/MOBILE-CLIENT-DESIGN.md)。
 *
 * 开关 (默认关): 开了这台电脑才连中央中转站, 手机才能连过来。
 * 下面列配过的手机, 可以撤销 (手机丢了就在这里点)。手机能做什么写清楚, 不让员工猜。
 */
import * as React from "react";
import { listen } from "@tauri-apps/api/event";

import {
  MOBILE_CHANGED_EVENT,
  mobileRevoke,
  mobileSetEnabled,
  mobileStatus,
  mobileStatusText,
  type MobileStatus,
} from "../../lib/mobile";

const muted: React.CSSProperties = { fontSize: 11, color: "var(--catfish-text-muted)" };

export default function MobileCard() {
  const [status, setStatus] = React.useState<MobileStatus | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const refresh = React.useCallback(() => {
    mobileStatus().then(setStatus).catch((e) => setError(String(e)));
  }, []);

  React.useEffect(() => {
    refresh();
    const un = listen(MOBILE_CHANGED_EVENT, refresh);
    // 连接是后台慢慢连上的, 开着这页时隔几秒看一次
    const t = setInterval(refresh, 5000);
    return () => {
      clearInterval(t);
      void un.then((f) => f());
    };
  }, [refresh]);

  const toggle = async (on: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await mobileSetEnabled(on);
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (device: string, name: string) => {
    if (!window.confirm(`撤销「${name}」? 撤销后这台手机马上连不上, 要用得重新配对。`)) return;
    try {
      await mobileRevoke(device);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  if (!status) {
    return <div style={{ padding: 12, fontSize: 12 }}>{error ?? "读取中…"}</div>;
  }

  const limit = status.maxPhones;
  return (
    <div style={{ padding: 12, fontSize: 12, display: "grid", gap: 12 }}>
      <div>
        <h3 style={{ margin: 0, fontSize: 15 }}>📱 手机</h3>
        <p style={{ ...muted, margin: "4px 0 0" }}>
          在手机上跟这台电脑上的小鲶对话、批准小鲶要做的事 (只能单次批准)、看早安简报。
          内容加密后经中央转发, 中央看不到、不保存; 电脑关机或休眠时手机连不上。
        </p>
      </div>

      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
        <input type="checkbox" checked={status.enabled} disabled={busy}
          onChange={(e) => void toggle(e.target.checked)} />
        允许手机连这台电脑
      </label>
      <div aria-live="polite" style={{ fontSize: 12, color: status.connected ? "var(--status-ok)" : undefined }}>
        {mobileStatusText(status)}
      </div>

      {status.enabled && (
        <div>
          <h4 style={{ margin: "4px 0", fontSize: 13 }}>怎么连</h4>
          <ol style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
            <li>手机浏览器打开 <code>{status.mobileUrl}</code>, 用单位账号登录 (在公司外面要先连 VPN, 看单位怎么开的)。</li>
            <li>选这台电脑, 点「连接」。这里会弹出验证码, 跟手机上的一样就点允许。</li>
            <li>想放到手机桌面: 浏览器菜单里选「添加到主屏幕」。</li>
          </ol>
        </div>
      )}

      <div>
        <h4 style={{ margin: "4px 0", fontSize: 13 }}>
          配过的手机 {status.phones.length} 台{limit != null ? ` (单位上限 ${limit} 台)` : ""}
        </h4>
        {status.phones.length === 0 ? (
          <div style={muted}>还没有</div>
        ) : (
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 6 }}>
            {status.phones.map((p) => (
              <li key={p.device} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ flex: 1 }}>
                  {p.name} <span style={muted}>· {new Date(p.pairedAt).toLocaleString()} 配对</span>
                </span>
                <button type="button" onClick={() => void revoke(p.device, p.name)}>撤销</button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {error && <div role="alert" style={{ color: "var(--status-err)" }}>{error}</div>}
    </div>
  );
}
