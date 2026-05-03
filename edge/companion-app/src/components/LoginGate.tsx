/** 登录闸 — 没登录时挡住整个 app, 让员工先点登录.
 *
 * 行为:
 *   - 加载中 (whoami 还没返) → 显示空白 (跟 startup 体验一致)
 *   - 已登录 → render children
 *   - 未登录 → 显示登录卡片 + dev_token warning (如果 env 有)
 *
 * 设计: 不替代整个 App, 包在 App 外面. App 内部各 tab 不需要 auth-aware,
 *       直接假设已登录, gate 由这层控制.
 */

import { useState } from "react";
import { useAuth } from "../hooks/useAuth";

interface LoginGateProps {
  children: React.ReactNode;
}

export default function LoginGate({ children }: LoginGateProps) {
  const { state, loading, error, login } = useAuth();
  const [logging, setLogging] = useState(false);

  if (loading) {
    return (
      <div
        style={{
          height: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--catfish-text-muted)",
        }}
      >
        加载中…
      </div>
    );
  }

  if (state.authenticated) {
    return <>{children}</>;
  }

  // 未登录
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
          width: "360px",
          textAlign: "center",
        }}
      >
        {/* 五一 sprint 5/3 BL-D11: 占位 🐟 emoji 换正式 mascot */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10, margin: "0 0 6px 0" }}>
          <img src="/catfish-mascot.svg" alt="" width={36} height={36} style={{ display: "block" }} />
          <h2 style={{ margin: 0 }}>鲶鱼 Companion</h2>
        </div>
        <p
          style={{
            color: "var(--catfish-text-muted)",
            fontSize: 13,
            margin: "0 0 24px 0",
          }}
        >
          员工 AI 副手 · 用公司账号登录开始使用
        </p>

        {error && (
          <div
            style={{
              color: "var(--status-err)",
              fontSize: 12,
              padding: "8px 12px",
              background: "var(--catfish-bg)",
              borderRadius: "var(--radius-sm)",
              marginBottom: "var(--space-3)",
              textAlign: "left",
            }}
          >
            {error}
          </div>
        )}

        <button
          onClick={async () => {
            setLogging(true);
            try {
              await login();
            } catch {
              // error 已经在 hook 里 setError
            } finally {
              setLogging(false);
            }
          }}
          disabled={logging}
          style={{
            width: "100%",
            padding: "11px",
            background: logging ? "var(--catfish-text-muted)" : "var(--catfish-cyan)",
            color: "var(--catfish-bg)",
            border: 0,
            borderRadius: "var(--radius-sm)",
            fontSize: 14,
            fontWeight: 600,
            cursor: logging ? "wait" : "pointer",
          }}
        >
          {logging ? "等浏览器登录…" : "登录"}
        </button>

        <p
          style={{
            color: "var(--catfish-text-muted)",
            fontSize: 11,
            marginTop: "var(--space-3)",
          }}
        >
          点击 → 浏览器打开 SSO 登录页 → 输公司账号 → 自动跳回这里
        </p>
      </div>
    </div>
  );
}
