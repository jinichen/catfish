/** P28 (6/5 鸿波) — Dashboard 卡 — 改 gateway URL.
 *
 * 商用部署员工不会 vim ~/.catfish/*.yaml. 这卡读 ~/.catfish/companion.yaml +
 * memory_plugin.yaml 真**gateway.url 字段**, UI 改完写回去 + 提示 reload.
 *
 * 砍 Internal Token UI (6/5 audit fix): token 是 server admin 工具 (catfish gateway
 * 内部 dev token), 员工填了客户端也没用 — gateway validator 不认这值. 员工真正
 * 关心 3 类 token 都不在这里:
 *   - hermes-cli JWT: `catfish login` 自动写 ~/.hermes/config.yaml
 *   - dev token: server admin 配置 + .env 自动生成
 *   - provider keys: server 端配 (OpenAI/DeepSeek/...)
 *
 * 不 hot-reload: yaml 文件 plugin/Companion 启动时读一次. 改完要:
 *   - 重启 Companion (前端读 companion.yaml)
 *   - 重启 hermes gateway (plugin 读 memory_plugin.yaml)
 */

import { useEffect, useState } from "react";
import { readServerConfig, writeServerConfig, type ServerConfig } from "../../lib/tauri";

export default function ServerConfigCard() {
  const [cfg, setCfg] = useState<ServerConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [draftUrl, setDraftUrl] = useState("");
  const [draftIdentity, setDraftIdentity] = useState("");
  const [draftSecretBroker, setDraftSecretBroker] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // 初次加载
  useEffect(() => {
    let alive = true;
    readServerConfig()
      .then((c) => {
        if (!alive) return;
        setCfg(c);
        setDraftUrl(c.gateway_url);
        setDraftIdentity(c.identity_url);
        setDraftSecretBroker(c.secret_broker_url);
      })
      .catch((e) => {
        if (!alive) return;
        setErr(String(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const startEdit = () => {
    if (!cfg) return;
    setDraftUrl(cfg.gateway_url);
    setDraftIdentity(cfg.identity_url);
    setDraftSecretBroker(cfg.secret_broker_url);
    setEditing(true);
    setErr(null);
    setSaved(false);
  };

  const cancel = () => {
    setEditing(false);
    setErr(null);
  };

  const save = async () => {
    setSaving(true);
    setErr(null);
    try {
      const url = draftUrl.trim().replace(/\/+$/, "");
      if (!/^https?:\/\//.test(url)) {
        throw new Error("gateway URL 必须 http:// 或 https:// 开头");
      }
      const idUrl = draftIdentity.trim().replace(/\/+$/, "");
      if (idUrl && !/^https?:\/\//.test(idUrl)) {
        throw new Error("identity URL 必须 http:// 或 https:// 开头");
      }
      const sbUrl = draftSecretBroker.trim().replace(/\/+$/, "");
      if (sbUrl && !/^https?:\/\//.test(sbUrl)) {
        throw new Error("secret-broker URL 必须 http:// 或 https:// 开头");
      }
      const keepToken = cfg?.gateway_token || "";
      await writeServerConfig(url, keepToken, idUrl || undefined, sbUrl || undefined);
      const fresh = await readServerConfig();
      setCfg(fresh);
      setDraftUrl(fresh.gateway_url);
      setDraftIdentity(fresh.identity_url);
      setDraftSecretBroker(fresh.secret_broker_url);
      setEditing(false);
      setSaved(true);
      setTimeout(() => setSaved(false), 6000);
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div style={cardStyle}>
        <h3 style={titleStyle}>🌐 服务器配置</h3>
        <div style={{ color: "var(--catfish-text-muted)", fontSize: 12 }}>
          读取中…
        </div>
      </div>
    );
  }

  return (
    <div style={cardStyle}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <h3 style={titleStyle}>🌐 服务器配置</h3>
        {!editing && (
          <button onClick={startEdit} style={smallBtn}>
            改
          </button>
        )}
      </div>

      <div
        style={{
          fontSize: 11,
          color: "var(--catfish-text-muted)",
          marginBottom: 12,
        }}
      >
        客户端直连的 3 个 server. 改完重启 hermes-gateway + Companion 生效.
      </div>

      {/* gateway URL */}
      <Row label="Gateway URL">
        {editing ? (
          <input
            value={draftUrl}
            onChange={(e) => setDraftUrl(e.target.value)}
            placeholder="http://127.0.0.1:8999"
            style={inputStyle}
          />
        ) : (
          <code style={codeStyle}>{cfg?.gateway_url}</code>
        )}
      </Row>

      {/* P29: Identity (OIDC) URL — catfish login 走这 */}
      <Row label="Identity URL">
        {editing ? (
          <input
            value={draftIdentity}
            onChange={(e) => setDraftIdentity(e.target.value)}
            placeholder="http://127.0.0.1:8998"
            style={inputStyle}
          />
        ) : (
          <code style={codeStyle}>{cfg?.identity_url || "(默认)"}</code>
        )}
      </Row>

      {/* P29: Secret Broker URL — 员工 SSO 拿 secret */}
      <Row label="Secret Broker URL">
        {editing ? (
          <input
            value={draftSecretBroker}
            onChange={(e) => setDraftSecretBroker(e.target.value)}
            placeholder="http://127.0.0.1:8995"
            style={inputStyle}
          />
        ) : (
          <code style={codeStyle}>{cfg?.secret_broker_url || "(默认)"}</code>
        )}
      </Row>

      {/* action bar */}
      {editing && (
        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          <button onClick={save} disabled={saving} style={primaryBtn}>
            {saving ? "保存中…" : "保存"}
          </button>
          <button onClick={cancel} disabled={saving} style={smallBtn}>
            取消
          </button>
        </div>
      )}

      {err && (
        <div
          style={{
            marginTop: 8,
            color: "var(--status-err)",
            fontSize: 12,
          }}
        >
          ✗ {err}
        </div>
      )}

      {saved && (
        <div
          style={{
            marginTop: 12,
            padding: "8px 10px",
            background: "var(--catfish-bg-elevated, rgba(34, 197, 94, 0.08))",
            border: "1px solid var(--status-ok, #16a34a)",
            borderRadius: 4,
            fontSize: 12,
            color: "var(--catfish-text)",
          }}
        >
          ✓ 已保存到 ~/.catfish/companion.yaml + memory_plugin.yaml.
          <br />
          重启生效:
          <ol style={{ margin: "6px 0 0 18px", padding: 0 }}>
            <li>
              终端跑: <code style={inlineCode}>hermes gateway stop && hermes gateway start</code>
            </li>
            <li>关掉 Companion 重新打开 (前端 yaml 进程内只读一次)</li>
          </ol>
        </div>
      )}
    </div>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "baseline",
        gap: 12,
        marginBottom: 8,
        flexWrap: "wrap",
      }}
    >
      <span
        style={{
          fontSize: 11,
          color: "var(--catfish-text-muted)",
          minWidth: 100,
          fontWeight: 500,
        }}
      >
        {label}
      </span>
      <div style={{ flex: 1, minWidth: 200 }}>{children}</div>
    </div>
  );
}

const cardStyle: React.CSSProperties = {
  padding: "var(--space-4)",
  border: "1px solid var(--catfish-border)",
  borderRadius: "var(--radius-md, 6px)",
  background: "var(--catfish-bg)",
};

const titleStyle: React.CSSProperties = {
  margin: "0 0 4px",
  fontSize: 14,
  fontWeight: 600,
  color: "var(--catfish-text)",
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "5px 8px",
  border: "1px solid var(--catfish-border)",
  borderRadius: 4,
  fontSize: 12,
  background: "var(--catfish-bg)",
  color: "var(--catfish-text)",
  boxSizing: "border-box",
};

const codeStyle: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  background: "var(--catfish-bg-elevated, rgba(0,0,0,0.04))",
  padding: "2px 6px",
  borderRadius: 3,
  color: "var(--catfish-text)",
  wordBreak: "break-all",
};

const inlineCode: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  background: "rgba(0,0,0,0.06)",
  padding: "1px 4px",
  borderRadius: 2,
};


const smallBtn: React.CSSProperties = {
  background: "transparent",
  border: "1px solid var(--catfish-border)",
  color: "var(--catfish-text)",
  borderRadius: 4,
  padding: "3px 10px",
  fontSize: 11,
  cursor: "pointer",
};

const primaryBtn: React.CSSProperties = {
  background: "var(--catfish-teal, #0d9488)",
  border: "1px solid var(--catfish-teal, #0d9488)",
  color: "#fff",
  borderRadius: 4,
  padding: "5px 14px",
  fontSize: 12,
  cursor: "pointer",
  fontWeight: 500,
};
