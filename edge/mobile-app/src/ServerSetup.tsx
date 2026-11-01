/** 第一次打开: 填公司服务器地址 (Companion「设置 → 隐私 / 本机数据 → 手机」里写着)。 */
import { useState } from "react";

import { normalizeServer } from "./platform_ios";

export function ServerSetup({ onDone }: { onDone: (url: string) => void }) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const check = async () => {
    const url = normalizeServer(text);
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`${url}/.well-known/openid-configuration`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      onDone(url);
    } catch (e) {
      setError(`连不上 ${url}: ${e instanceof Error ? e.message : e}。看看地址对不对、手机能不能连到公司网络 (在外面要先连 VPN)。`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ padding: 24, maxWidth: 480, margin: "0 auto" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "24px 0" }}>
        <img src="/catfish-mascot.svg" alt="" width={44} height={44} />
        <b style={{ fontSize: 22 }}>小鲶</b>
      </div>
      <p>在手机上跟你电脑上的小鲶对话、批准、看早安简报。</p>
      <label style={{ display: "block", margin: "16px 0 6px" }}>公司服务器地址</label>
      <input value={text} onChange={(e) => setText(e.target.value)} placeholder="例如 https://10.10.40.50"
        autoCapitalize="none" autoCorrect="off" inputMode="url"
        style={{ width: "100%", boxSizing: "border-box", fontSize: 16, padding: 10, borderRadius: 8,
          border: "1px solid var(--border)" }} />
      <p style={{ color: "var(--text-muted)", fontSize: 13 }}>
        不知道填什么: 在电脑上打开鲶鱼 Companion → 设置 → 隐私 / 本机数据 → 手机, 那里写着。
      </p>
      {error && <p style={{ color: "var(--status-err)", fontSize: 14 }}>{error}</p>}
      <button onClick={() => void check()} disabled={busy || !text.trim()}
        style={{ width: "100%", padding: 12, fontSize: 16, borderRadius: 8, border: "none",
          background: "var(--accent)", color: "#fff", marginTop: 8 }}>
        {busy ? "检查中…" : "下一步"}
      </button>
    </div>
  );
}
