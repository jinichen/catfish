/** 个人配额 —— 本月已用 / 上限 / 进度条 */

import { formatTokens } from "../../lib/format";

// MVP 阶段先用 placeholder 数据，等中央 quota 服务接通后替换
const MOCK = { used: 1_240_000, limit: 5_000_000 };

export default function QuotaCard() {
  const pct = (MOCK.used / MOCK.limit) * 100;
  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
      }}
    >
      <h3 style={{ marginBottom: "var(--space-3)" }}>本月配额</h3>
      <div
        style={{
          fontSize: 24,
          fontWeight: 600,
          fontFamily: "var(--font-mono)",
        }}
      >
        {formatTokens(MOCK.used)}
        <span
          style={{
            fontSize: 14,
            color: "var(--catfish-text-muted)",
            fontWeight: 400,
          }}
        >
          {" "}
          / {formatTokens(MOCK.limit)} tok
        </span>
      </div>
      <div
        style={{
          marginTop: "var(--space-3)",
          height: 6,
          background: "var(--catfish-border)",
          borderRadius: 3,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: "var(--catfish-cyan)",
          }}
        />
      </div>
      <div
        style={{
          marginTop: "var(--space-2)",
          fontSize: 11,
          color: "var(--catfish-text-muted)",
        }}
      >
        TODO: 接入中央 quota 服务，目前为 placeholder
      </div>
    </div>
  );
}
