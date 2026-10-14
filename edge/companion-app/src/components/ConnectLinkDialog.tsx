/** 点了 IT 发的 catfish://connect 接入链接 → 确认后一键接入 (10/14)。
 *
 * 链接谁都能造, 所以**一定先让员工确认**是连到哪台服务器, 不静默改配置。确认后:
 *   1. https 门户 + 接入码 → 自动取公司证书并核对 (对不上就停, 不改任何配置)
 *   2. 写服务器配置 (三个地址)
 *   3. 刷新 —— 换了服务器的话 LoginGate 会让员工重新登录
 * 放在 LoginGate 外面: 新装还没登录的机器正是最需要它的时候。
 */
import { useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";

import {
  CONNECT_LINK_EVENT,
  needsPairing,
  pairServerCertificate,
  takeConnectLink,
  type ConnectLink,
} from "../lib/serverPairing";
import { readServerConfig, writeServerConfig } from "../lib/tauri";

export default function ConnectLinkDialog() {
  const [link, setLink] = useState<ConnectLink | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    takeConnectLink().then((l) => { if (alive && l) setLink(l); }).catch(() => undefined);
    const un = listen<ConnectLink>(CONNECT_LINK_EVENT, (e) => {
      setError(null);
      setLink(e.payload);
      void takeConnectLink().catch(() => undefined); // 已经拿到了, 清掉待处理的那份
    });
    return () => { alive = false; void un.then((f) => f()); };
  }, []);

  if (!link) return null;

  const connect = async () => {
    setBusy(true);
    setError(null);
    try {
      if (link.code && needsPairing(link.webUrl)) await pairServerCertificate(link.webUrl, link.code);
      const cur = await readServerConfig().catch(() => null);
      await writeServerConfig(link.gatewayUrl, cur?.gateway_token ?? "", link.identityUrl, link.webUrl);
      window.location.reload();
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  };

  const row = (label: string, value: string) => (
    <div style={{ display: "flex", gap: 8, fontSize: 13, marginBottom: 4 }}>
      <span style={{ width: 84, color: "var(--catfish-text-muted)" }}>{label}</span>
      <code>{value}</code>
    </div>
  );

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)", display: "flex",
      alignItems: "center", justifyContent: "center", zIndex: 200 }}>
      <div role="dialog" aria-label="连接公司服务器" style={{ background: "var(--catfish-bg-elevated)",
        padding: "var(--space-6)", borderRadius: "var(--radius-md)", width: 460, boxShadow: "var(--shadow-md)" }}>
        <h3 style={{ marginTop: 0 }}>连接公司服务器</h3>
        <p style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>
          你点开了一个鲶鱼接入链接。请确认是公司 IT 发的, 再点连接。
        </p>
        {row("门户地址", link.webUrl)}
        {row("网关", link.gatewayUrl)}
        {row("认证服务", link.identityUrl)}
        {link.code && row("接入码", link.code)}
        {needsPairing(link.webUrl) && !link.code && (
          <p style={{ fontSize: 12, color: "var(--status-warn, #c97e1c)" }}>
            链接里没有接入码: 只会填地址, 公司证书要另外装 (向 IT 要接入码)。
          </p>
        )}
        {error && <p role="alert" style={{ fontSize: 12, color: "var(--status-err)" }}>没有连接: {error}</p>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: "var(--space-4)" }}>
          <button type="button" onClick={() => setLink(null)} disabled={busy}>取消</button>
          <button type="button" onClick={connect} disabled={busy} style={{ background: "var(--catfish-teal, #0d9488)",
            border: "1px solid var(--catfish-teal, #0d9488)", color: "#fff", borderRadius: 4, padding: "5px 14px" }}>
            {busy ? "连接中…" : "连接"}
          </button>
        </div>
      </div>
    </div>
  );
}
