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
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // 若 state 里 URL 变了 (config 被外部改过), 同步到表单
  useEffect(() => {
    setGatewayUrl(state.gatewayUrl);
    setIdentityUrl(state.identityUrl);
  }, [state.gatewayUrl, state.identityUrl]);

  const handleSave = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      // 写 companion.yaml
      await invoke("write_server_config", {
        gatewayUrl: gatewayUrl.trim(),
        gatewayToken: "",   // 员工无 dev_token, 走 SSO
        identityUrl: identityUrl.trim() || null,
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
