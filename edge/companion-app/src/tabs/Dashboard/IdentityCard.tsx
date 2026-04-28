/** 当前身份 —— 系统用户 / SOUL 来源 / 运行时主题 / 活动会话 */

import { useIdentity } from "../../hooks/useIdentity";

export default function IdentityCard() {
  const { identity, error } = useIdentity();

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
