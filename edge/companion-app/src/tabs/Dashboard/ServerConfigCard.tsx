/** P28 (6/5 鸿波) — Dashboard 卡 — 改 gateway URL.
 *
 * 商用部署员工不会 vim ~/.catfish/*.yaml. 这卡读 ~/.catfish/companion.yaml +
 * memory_plugin.yaml gateway.url 字段, UI 改完写回去 + 提示 reload.
 *
 * 砍 Internal Token UI (6/5 audit fix): token 是 server admin 工具 (catfish gateway
 * 内部 dev token), 员工填了客户端也没用 — gateway validator 不认这值. 员工真正
 * 关心 3 类 token 都不在这里:
 *   - hermes-cli JWT: `catfish login` 自动写 ~/.hermes/config.yaml
 *   - dev token: server admin 配置 + .env 自动生成
 *   - provider keys: server 端配 (OpenAI/DeepSeek/...)
 *
 * 生效范围 (7/29 起):
 *   - Companion 侧**当场生效** —— write_server_config 保存后调
 *     services::endpoints::reload() 换掉进程内缓存. 原来是 OnceLock 冻结,
 *     只能靠重启, 那句提示不是设计而是实现限制.
 *   - hermes 跑在另一个进程里, 仍要重启才会读到新的 ~/.hermes/.env.
 *     只有"聊天走本机 hermes"的机器需要关心; 员工机默认直连 gateway, 不受影响.
 *
 * BL-HERMES-ENV-SYNC (7/18): write_server_config 已加写 ~/.hermes/.env CATFISH_GATEWAY_URL.
 * 之前 comment 说 "plugin 读 memory_plugin.yaml" 是 stale (plugin 早已 refactor 用 env),
 * 面板改 IP 后 hermes plugin 用老 env / default 127 · memory/role 走错服务器.
 */

import { useEffect, useState } from "react";
import { readServerConfig, writeServerConfig, type ServerConfig } from "../../lib/tauri";

export default function ServerConfigCard() {
  const [cfg, setCfg] = useState<ServerConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [draftUrl, setDraftUrl] = useState("");
  const [draftIdentity, setDraftIdentity] = useState("");
  // P3.5.80 (7/28): 门户 URL — 见文件头说明, 这是第三个独立配置
  const [draftWeb, setDraftWeb] = useState("");
  // P3.4.1: secret-broker 服务删, 不再让员工配 broker URL
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
        setDraftWeb(c.web_url ?? "");
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
    setDraftWeb(cfg.web_url ?? "");
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
      // P3.5.80 (7/28): 门户 URL 允许留空 —— 它跟"能不能登录"无关,
      // 不该拿它挡住员工保存 gateway/identity. 留空时下面会显式提示后果.
      const webUrl = draftWeb.trim().replace(/\/+$/, "");
      if (webUrl && !/^https?:\/\//.test(webUrl)) {
        throw new Error("门户 URL 必须 http:// 或 https:// 开头");
      }
      const keepToken = cfg?.gateway_token || "";
      await writeServerConfig(url, keepToken, idUrl || undefined, webUrl || undefined);
      const fresh = await readServerConfig();
      setCfg(fresh);
      setDraftUrl(fresh.gateway_url);
      setDraftIdentity(fresh.identity_url);
      setDraftWeb(fresh.web_url ?? "");
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
        公司服务器的 3 个地址，由 IT 提供。三项各管各的，改一项不会自动带上其它两项。
        <br />
        保存后<strong>立即生效</strong>，不用重开。
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

      {/* P3.5.80 (7/28 达华现场): 中央门户 URL.
          顶部那排门户链接 (资源市场 / 部门 / 审计 / Admin / 系统管理) 走的是
          endpoints.web_url, 跟上面两个是**三个独立配置**. 这一栏原来根本不存在,
          于是不管上面改成什么 IP, 门户链接都落到兜底的 http://127.0.0.1:5173,
          点了打开员工自己的机器. */}
      <Row label="门户 URL">
        {editing ? (
          <input
            value={draftWeb}
            onChange={(e) => setDraftWeb(e.target.value)}
            placeholder="https://192.168.31.199 (HTTPS 部署) 或 http://IP:5173"
            style={inputStyle}
          />
        ) : cfg?.web_url ? (
          <code style={codeStyle}>{cfg.web_url}</code>
        ) : (
          // 不显示"(默认)" —— 这里没有默认值可言, 未配置就是链接会坏.
          // 显示成默认值会变成又一个"看着配好了实际没配"的坑.
          <span style={{ fontSize: 11, color: "var(--status-warn, #c97e1c)" }}>
            未配置 · 上方门户链接会指向 http://127.0.0.1:5173（员工自己的机器，点了打不开）
          </span>
        )}
      </Row>

      {/* P3.4.1 (6/13 hb): 砍 Secret Broker URL 行 — 中央服务已删, OAuth token
          改 Companion 本机存 (~/.catfish/mcp/oauth-tokens/). 员工不再需要配
          broker URL. */}

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
          ✓ 已保存
          <br />
          <strong>关掉鲶鱼重新打开</strong>后新地址才生效。
          {/* P3.5.80 (7/28 鸿波 catch "这上面的说明这么写, 给最终用户干嘛"):
              hermes 那条确实必要 (网关地址同步进了它的配置), 但员工既看不懂
              也没有可点的按钮 —— restartService() 对 hermes 直接 return,
              hermesKill 也没有 UI 入口. 所以降级成给 IT 的次要提示,
              不再摆在员工面前当第一步. */}
          <div
            style={{
              marginTop: 8,
              paddingTop: 6,
              borderTop: "1px solid var(--catfish-border)",
              fontSize: 11,
              color: "var(--catfish-text-muted)",
            }}
          >
            给 IT：网关地址同时写进了 hermes 的配置，需要
            <code style={inlineCode}>hermes gateway stop &amp;&amp; hermes gateway start</code>
            才会被用上。
          </div>
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
