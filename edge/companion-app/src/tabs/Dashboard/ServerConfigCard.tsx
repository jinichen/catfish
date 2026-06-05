/** P28 (6/5 鸿波) — Dashboard 卡 — 改 gateway URL/token.
 *
 * 商用部署员工不会 vim ~/.catfish/*.yaml. 这卡读 ~/.catfish/companion.yaml +
 * memory_plugin.yaml 真**gateway 字段**, UI 改完写回去 + 提示 reload.
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
  const [draftToken, setDraftToken] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [showToken, setShowToken] = useState(false);

  // 初次加载
  useEffect(() => {
    let alive = true;
    readServerConfig()
      .then((c) => {
        if (!alive) return;
        setCfg(c);
        setDraftUrl(c.gateway_url);
        setDraftToken(c.token_source === "env" ? "" : c.gateway_token);
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
    // env 来源的 token 不预填 (不让 yaml 覆盖 env), 留空表示"不动"
    setDraftToken(cfg.token_source === "env" ? "" : cfg.gateway_token);
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
      // 空 token + env 来源 → 保持 env (不写 yaml), 否则用 draft
      const tokenToWrite =
        cfg?.token_source === "env" && !draftToken.trim()
          ? cfg.gateway_token  // 把 env 真值写进 yaml 当 fallback (env 没了也 work)
          : draftToken.trim();
      await writeServerConfig(url, tokenToWrite);
      // 重读, 状态对齐
      const fresh = await readServerConfig();
      setCfg(fresh);
      setDraftUrl(fresh.gateway_url);
      setDraftToken(fresh.token_source === "env" ? "" : fresh.gateway_token);
      setEditing(false);
      setSaved(true);
      // 3 秒后清 saved 提示
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
        gateway 地址 + 内部 token. 改完重启 hermes-gateway + Companion 生效.
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

      {/* token */}
      <Row label="Internal Token">
        {editing ? (
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input
              type={showToken ? "text" : "password"}
              value={draftToken}
              onChange={(e) => setDraftToken(e.target.value)}
              placeholder={
                cfg?.token_source === "env"
                  ? "(env 已设, 留空保持; 填则覆盖)"
                  : cfg?.token_source === "yaml"
                    ? "(已存)"
                    : "粘贴 CATFISH_INTERNAL_DEV_TOKEN"
              }
              style={{ ...inputStyle, fontFamily: "var(--font-mono)" }}
            />
            <button
              onClick={() => setShowToken(!showToken)}
              style={{ ...smallBtn, padding: "3px 8px" }}
            >
              {showToken ? "藏" : "看"}
            </button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <code style={codeStyle}>
              {cfg?.gateway_token
                ? showToken
                  ? cfg.gateway_token
                  : "•••••••• (" + cfg.gateway_token.length + " 字符)"
                : "(空)"}
            </code>
            {cfg?.gateway_token && (
              <button
                onClick={() => setShowToken(!showToken)}
                style={{ ...smallBtn, padding: "2px 6px", fontSize: 10 }}
              >
                {showToken ? "藏" : "看"}
              </button>
            )}
            <span
              style={{
                fontSize: 10,
                color: "var(--catfish-text-muted)",
              }}
            >
              来源: {tokenSourceLabel(cfg?.token_source)}
            </span>
          </div>
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

function tokenSourceLabel(src?: string): string {
  switch (src) {
    case "yaml":
      return "memory_plugin.yaml";
    case "env":
      return "env CATFISH_INTERNAL_DEV_TOKEN";
    default:
      return "未设 (LLM 调用会跳)";
  }
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
