/** 设置 → 服务 / 配额 → Claude Code 钥匙 (11/19)。
 *
 * 鸿波「加一个长期有效的钥匙给 Claude Code 用」。员工自己生成, 不自动发 —— 用的人少,
 * 自动给每人发等于平白多出一堆不过期的钥匙。钥匙按本人算权限和配额, 但只能用模型, 不带管理权限;
 * 原文只在生成时显示一次; 不用了 / 怀疑泄露就在这里吊销, 一分钟内失效。
 */
import * as React from "react";

import { confirmDialog } from "../../components/dialogs";
import { claudeCodeEnv, createApiKey, listApiKeys, revokeApiKey, type ApiKeyInfo, type NewApiKey } from "../../lib/apiKeys";

const muted: React.CSSProperties = { fontSize: 11, color: "var(--catfish-text-muted)" };
const pre: React.CSSProperties = {
  margin: 0, padding: 8, fontSize: 11, lineHeight: 1.6, whiteSpace: "pre-wrap", wordBreak: "break-all",
  background: "var(--catfish-bg)", border: "1px solid var(--catfish-border)", borderRadius: 4, color: "var(--catfish-text)",
};
const day = (s: number | null) => (s ? new Date(s * 1000).toLocaleString() : "还没用过");

export default function ApiKeysCard() {
  const [keys, setKeys] = React.useState<ApiKeyInfo[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [name, setName] = React.useState("");
  const [fresh, setFresh] = React.useState<NewApiKey | null>(null);
  const [copied, setCopied] = React.useState(false);
  const [busy, setBusy] = React.useState(false);

  const refresh = React.useCallback(() => {
    listApiKeys().then((k) => { setKeys(k); setError(null); }).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  React.useEffect(refresh, [refresh]);

  const create = async () => {
    setBusy(true); setError(null); setCopied(false);
    try {
      setFresh(await createApiKey(name.trim() || "Claude Code"));
      setName("");
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (k: ApiKeyInfo) => {
    if (!(await confirmDialog(`吊销「${k.name}」(${k.hint})? 用这把钥匙的工具一分钟内就连不上中央了。`, "吊销钥匙"))) return;
    try {
      await revokeApiKey(k.id);
      if (fresh?.id === k.id) setFresh(null);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const copy = async (text: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(true); } catch { setCopied(false); }
  };

  return (
    <div style={{ padding: 12, fontSize: 12, display: "grid", gap: 12 }}>
      <div>
        <h3 style={{ margin: 0, fontSize: 15 }}>🔑 Claude Code 钥匙</h3>
        <p style={{ ...muted, margin: "4px 0 0", lineHeight: 1.6 }}>
          让 Claude Code 这类工具直接用中央的模型。钥匙长期有效、算你本人的权限和配额, 但只能用模型, 不能做管理操作。
          只在生成时显示一次, 请马上复制保存; 不用了或怀疑泄露就吊销, 一分钟内失效。
        </p>
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="用在哪 (如: 笔记本上的 Claude Code)" aria-label="钥匙名称"
          style={{ flex: 1, minWidth: 0, padding: "4px 8px", fontSize: 12, border: "1px solid var(--catfish-border)", borderRadius: 4,
            background: "var(--catfish-bg)", color: "var(--catfish-text)", fontFamily: "inherit" }} />
        <button type="button" disabled={busy} onClick={() => void create()}>{busy ? "生成中…" : "生成钥匙"}</button>
      </div>

      {fresh && (
        <div role="status" style={{ display: "grid", gap: 6, padding: 8, borderRadius: 6, border: "1px solid var(--catfish-cyan)" }}>
          <strong style={{ fontSize: 12 }}>新钥匙「{fresh.name}」—— 只显示这一次</strong>
          <pre style={pre} aria-label="新钥匙">{fresh.key}</pre>
          <div style={muted}>Claude Code 的设置 (加到 ~/.zshrc 之类, 或运行前粘贴到终端):</div>
          <pre style={pre} aria-label="Claude Code 设置">{claudeCodeEnv(fresh.key)}</pre>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button type="button" onClick={() => void copy(fresh.key)}>复制钥匙</button>
            <button type="button" onClick={() => void copy(claudeCodeEnv(fresh.key))}>复制设置</button>
            {copied && <span style={muted}>已复制</span>}
            <span style={{ flex: 1 }} />
            <button type="button" onClick={() => setFresh(null)}>我已保存</button>
          </div>
        </div>
      )}

      <div>
        <h4 style={{ margin: "4px 0", fontSize: 13 }}>我的钥匙{keys ? ` ${keys.length} 把` : ""}</h4>
        {keys === null ? (
          <div style={muted}>{error ? "" : "读取中…"}</div>
        ) : keys.length === 0 ? (
          <div style={muted}>还没有</div>
        ) : (
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 6 }}>
            {keys.map((k) => (
              <li key={k.id} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ flex: 1, minWidth: 0 }}>
                  {k.name} <code style={{ fontSize: 11 }}>{k.hint}</code>
                  <span style={muted}> · {new Date(k.created_at * 1000).toLocaleDateString()} 生成 · 最后使用 {day(k.last_used_at)}</span>
                </span>
                <button type="button" onClick={() => void revoke(k)}>吊销</button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {error && <div role="alert" style={{ color: "var(--status-err)" }}>{error}</div>}
    </div>
  );
}
