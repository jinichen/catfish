/** BL-SERVER-REACHABILITY-CHECK (7/16): LoginGate 阶段的服务器配置卡.
 *
 * 员工首启 Companion 时, 若中央 URL 不通 (员工没配 / 公司 URL 跟默认不同),
 * LoginGate 显示这张卡, 让员工填正确 URL. 保存后自动重测, 通了 LoginGate
 * 切换到"登录"按钮.
 *
 * 跟 Dashboard/ServerConfigCard 的区别:
 *   - Dashboard 那个是登录**后**改配置 (改完 restart 生效)
 *   - 这个是登录**前** · 员工没登录也能配, 是登录的必经步骤
 *   - UI 更简单 (只填 URL, 不管 token · dev_token 是 admin 场景, 员工用不到)
 */

import { useState, useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { ServerReachableState } from "../hooks/useServerReachable";

interface ServerSetupCardProps {
  state: ServerReachableState;
  onRetest: () => void;
  onSaved: () => void;   // 保存成功后触发 · LoginGate 会再 check_now()
}

export default function ServerSetupCard({ state, onRetest, onSaved }: ServerSetupCardProps) {
  const [gatewayUrl, setGatewayUrl] = useState(state.gatewayUrl);
  const [identityUrl, setIdentityUrl] = useState(state.identityUrl);
  // P3.5.80 (7/28 达华现场): 中央门户 URL — 第三个独立配置.
  //
  // 为什么这张卡也要有: 新员工首启只会见到本卡, 从不打开 Dashboard 的
  // 服务器配置. 只在那边补字段的话, 新装的机器照样是门户链接全指向
  // http://127.0.0.1:5173 打不开.
  //
  // 不从 useServerReachable 的 state 拿 (那个 hook 只关心"通不通",
  // 门户地址跟连通性检测无关), 自己读一次 read_server_config 即可.
  const [webUrl, setWebUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // 若 state 里 URL 变了 (config 被外部改过), 同步到表单
  useEffect(() => {
    setGatewayUrl(state.gatewayUrl);
    setIdentityUrl(state.identityUrl);
  }, [state.gatewayUrl, state.identityUrl]);

  // 回填已有的 web_url (IT 预推过 companion.yaml 的场景, 别让员工看到空框
  // 以为没配过, 又手填一个不一样的进去).
  useEffect(() => {
    let alive = true;
    invoke<{ web_url?: string }>("read_server_config")
      .then((c) => {
        if (alive && c?.web_url) setWebUrl(c.web_url);
      })
      .catch(() => {
        /* 读不到就留空 — 本卡本来就是"配置还没就绪"时出现的 */
      });
    return () => {
      alive = false;
    };
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      // 写 companion.yaml
      // P3.5.80 (7/28): webUrl 留空时传 null —— Rust 侧语义是"不动 yaml 里
      // 已有的值", 不会把 IT 预推的配置擦掉.
      const web = webUrl.trim().replace(/\/+$/, "");
      if (web && !isValidUrl(web)) {
        throw new Error("门户地址必须 http:// 或 https:// 开头");
      }
      await invoke("write_server_config", {
        gatewayUrl: gatewayUrl.trim(),
        gatewayToken: "",   // 员工无 dev_token, 走 SSO
        identityUrl: identityUrl.trim() || null,
        webUrl: web || null,
      });
      onSaved();
    } catch (e) {
      setSaveError(String(e));
    } finally {
      setSaving(false);
    }
  };

  // URL 简单合法性
  const isValidUrl = (u: string) => {
    try {
      const p = new URL(u);
      return p.protocol === "http:" || p.protocol === "https:";
    } catch {
      return false;
    }
  };

  const canSave =
    isValidUrl(gatewayUrl) && isValidUrl(identityUrl) && !saving;

  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--catfish-bg)",
      }}
    >
      <div
        style={{
          background: "var(--catfish-bg-elevated)",
          border: "1px solid var(--catfish-border)",
          borderRadius: "var(--radius-md)",
          padding: "32px",
          width: "440px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
          <img src="/catfish-mascot.svg" alt="" width={36} height={36} />
          <h2 style={{ margin: 0 }}>连接你公司的服务器</h2>
        </div>
        <p
          style={{
            color: "var(--catfish-text-muted)",
            fontSize: 13,
            margin: "0 0 24px 0",
          }}
        >
          鲶鱼 Companion 需要先连上公司的中央服务器才能登录. 找 IT 部门要下面 2 个地址.
        </p>

        {/* 服务状态提示 */}
        <div
          style={{
            marginBottom: 20,
            padding: "10px 12px",
            background: "var(--catfish-bg)",
            borderRadius: "var(--radius-sm)",
            fontSize: 12,
            color: "var(--catfish-text-muted)",
          }}
        >
          <div style={{ marginBottom: 6 }}>
            {state.checking ? (
              <span>⏳ 正在检测服务器连通性…</span>
            ) : (
              <>
                <span style={{ marginRight: 12 }}>
                  {state.identityReachable ? "🟢" : "🔴"} 认证服务
                </span>
                <span>{state.gatewayReachable ? "🟢" : "🔴"} 网关</span>
              </>
            )}
          </div>
          {state.lastError && !state.checking && (
            <div style={{ color: "var(--status-err)", fontSize: 11, marginTop: 4 }}>
              {state.lastError}
            </div>
          )}
        </div>

        {/* Gateway URL */}
        <label style={{ display: "block", fontSize: 13, marginBottom: 6 }}>
          网关地址 (Gateway URL)
        </label>
        <input
          type="url"
          value={gatewayUrl}
          onChange={(e) => setGatewayUrl(e.target.value)}
          placeholder="http://catfish.yourcompany.com:8999 或 http://10.0.5.20:8999"
          style={{
            width: "100%",
            padding: "8px 12px",
            border: "1px solid var(--catfish-border)",
            borderRadius: "var(--radius-sm)",
            fontSize: 13,
            marginBottom: 16,
            background: "var(--catfish-bg)",
            color: "var(--catfish-text)",
            boxSizing: "border-box",
          }}
        />

        {/* Identity URL */}
        <label style={{ display: "block", fontSize: 13, marginBottom: 6 }}>
          认证服务地址 (Identity URL)
        </label>
        <input
          type="url"
          value={identityUrl}
          onChange={(e) => setIdentityUrl(e.target.value)}
          placeholder="http://catfish.yourcompany.com:8998 或 http://10.0.5.20:8998"
          style={{
            width: "100%",
            padding: "8px 12px",
            border: "1px solid var(--catfish-border)",
            borderRadius: "var(--radius-sm)",
            fontSize: 13,
            marginBottom: 20,
            background: "var(--catfish-bg)",
            color: "var(--catfish-text)",
            boxSizing: "border-box",
          }}
        />

        {/* 中央门户 URL (P3.5.80 7/28) — 选填, 不挡登录.
            登录只需要 gateway + identity; 门户地址是登录**之后**点顶部那排
            链接才用到的. 拿它当必填会把员工挡在登录外面, 得不偿失.
            但留空的后果要说清楚, 否则又是"以为配好了". */}
        <label style={{ display: "block", fontSize: 13, marginBottom: 6 }}>
          中央门户地址 (选填)
        </label>
        <input
          type="url"
          value={webUrl}
          onChange={(e) => setWebUrl(e.target.value)}
          placeholder="https://10.0.5.20 (HTTPS 部署) 或 http://10.0.5.20:5173"
          style={{
            width: "100%",
            padding: "8px 12px",
            border: "1px solid var(--catfish-border)",
            borderRadius: "var(--radius-sm)",
            fontSize: 13,
            marginBottom: 6,
            background: "var(--catfish-bg)",
            color: "var(--catfish-text)",
            boxSizing: "border-box",
          }}
        />
        <div
          style={{
            fontSize: 11,
            color: webUrl.trim()
              ? "var(--catfish-text-muted)"
              : "var(--status-warn, #c97e1c)",
            marginBottom: 20,
          }}
        >
          {webUrl.trim()
            ? "登录后仪表盘顶部的门户链接会跳这里。"
            : "留空的话，仪表盘顶部的门户链接会指向 http://127.0.0.1:5173（你自己的机器），点了打不开。"}
        </div>

        {saveError && (
          <div
            style={{
              color: "var(--status-err)",
              fontSize: 12,
              padding: "8px 12px",
              background: "var(--catfish-bg)",
              borderRadius: "var(--radius-sm)",
              marginBottom: "var(--space-3)",
            }}
          >
            保存失败: {saveError}
          </div>
        )}

        <div style={{ display: "flex", gap: 8 }}>
          <button
            onClick={handleSave}
            disabled={!canSave}
            style={{
              flex: 1,
              padding: "10px 16px",
              background: canSave ? "var(--catfish-accent)" : "var(--catfish-bg)",
              color: canSave ? "white" : "var(--catfish-text-muted)",
              border: "1px solid var(--catfish-border)",
              borderRadius: "var(--radius-sm)",
              fontSize: 14,
              fontWeight: 500,
              cursor: canSave ? "pointer" : "not-allowed",
            }}
          >
            {saving ? "保存中…" : "保存并测试"}
          </button>
          <button
            onClick={onRetest}
            disabled={state.checking}
            style={{
              padding: "10px 16px",
              background: "var(--catfish-bg)",
              color: "var(--catfish-text)",
              border: "1px solid var(--catfish-border)",
              borderRadius: "var(--radius-sm)",
              fontSize: 14,
              cursor: state.checking ? "not-allowed" : "pointer",
            }}
          >
            {state.checking ? "…" : "重测"}
          </button>
        </div>

        <p style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: 16, marginBottom: 0 }}>
          💡 单机测试: 网关 <code>http://127.0.0.1:8999</code>, 认证服务{" "}
          <code>http://127.0.0.1:8998</code>
        </p>
      </div>
    </div>
  );
}
