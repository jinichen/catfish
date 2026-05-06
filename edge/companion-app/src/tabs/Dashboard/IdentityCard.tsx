/** 当前身份 —— 系统用户 / SOUL 来源 / 运行时主题 / 活动会话 / 鲶鱼版本 */

import { useEffect, useState } from "react";
import { getVersion } from "@tauri-apps/api/app";

import { useIdentity } from "../../hooks/useIdentity";

export default function IdentityCard() {
  const { identity, error } = useIdentity();
  // 5/5 鸿波: 版本号去硬编码, 走 Tauri getVersion API 运行时读 tauri.conf.json.
  // 发版时只改 tauri.conf.json (build 真源头) + Cargo.toml + package.json 三处,
  // UI 跟着自动变, 不再 IdentityCard 里单独维护一份.
  const [version, setVersion] = useState<string>("…");
  useEffect(() => {
    getVersion()
      .then((v) => setVersion(`v${v}`))
      .catch((e) => {
        console.warn("getVersion 失败:", e);
        setVersion("(未知)");
      });
  }, []);

  return (
    <Card title="身份">
      {error && (
        <div style={{ color: "var(--status-err)", fontSize: 12 }}>
          读取失败：{error}
        </div>
      )}
      {!identity && !error && <div>加载中…</div>}
      {identity && (
        <>
          <Row label="员工" value={identity.systemUser} />
          <Row
            label="SOUL"
            value={identity.soulSource}
            sub={identity.soulTarget}
          />
          <Row label="皮肤" value={identity.skin} />
          {identity.defaultModel && (
            <Row label="默认模型" value={identity.defaultModel} mono />
          )}
          {identity.activeSessionId ? (
            <Row
              label="活动会话"
              value={`· ${identity.activeSessionId.slice(-6)}`}
              sub={identity.activeSessionModel ?? undefined}
              mono
            />
          ) : (
            <Row label="活动会话" value="(无)" />
          )}
          <Row label="鲶鱼版本" value={version} mono />
        </>
      )}
    </Card>
  );
}

function Card({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
      }}
    >
      <h3 style={{ marginBottom: "var(--space-3)" }}>{title}</h3>
      {children}
    </div>
  );
}

function Row({
  label,
  value,
  sub,
  mono,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
  mono?: boolean;
}) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-start",
        marginBottom: "var(--space-2)",
        fontSize: 13,
        gap: "var(--space-3)",
      }}
    >
      <span style={{ color: "var(--catfish-text-muted)", flexShrink: 0 }}>
        {label}
      </span>
      <span
        style={{
          textAlign: "right",
          minWidth: 0,
          fontFamily: mono ? "var(--font-mono)" : "inherit",
        }}
      >
        <div>{value}</div>
        {sub && (
          <div
            style={{
              fontSize: 11,
              color: "var(--catfish-text-muted)",
              fontFamily: "var(--font-mono)",
              marginTop: 2,
              wordBreak: "break-all",
            }}
            title={sub}
          >
            {sub.length > 40 ? `…${sub.slice(-38)}` : sub}
          </div>
        )}
      </span>
    </div>
  );
}
