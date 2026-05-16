/** 个人配额 —— 三维 quota (用户分钟 / 用户日 / 部门日)
 *
 * 数据来源: gateway GET /api/quota/me (五一 sprint 5/3, BL-D9).
 * limit=0 → 不限 (内网员工常态), 不画进度条.
 */

import { useEffect, useState } from "react";

import { formatTokens } from "../../lib/format";
import { fetchQuotaMe, type QuotaMe } from "../../lib/quota";

export default function QuotaCard() {
  const [data, setData] = useState<QuotaMe | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const q = await fetchQuotaMe();
        if (!cancelled) {
          setData(q);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    // 30s 刷一次, 仪表盘不需要更频繁
    const t = window.setInterval(() => void load(), 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, []);

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

      {loading && !data && <SkeletonRow />}
      {error && !data && (
        <div style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>
          quota 服务未就绪 ({error})
        </div>
      )}
      {data && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
          {/* BL-QUOTA-CARD-SLIM (5/16): 砍"近 1 分钟" (没人在意秒级 burst) + 砍"部门今日"
              (跟员工本人无关, 是 admin 关注的). 只留"今日滑动 24h" — 员工真在意的就这条. */}
          <QuotaRow label="今日 (滑动 24h)" w={data.day} primary />
        </div>
      )}

      <div
        style={{
          marginTop: "var(--space-3)",
          fontSize: 11,
          color: "var(--catfish-text-muted)",
        }}
      >
        实时取自 gateway · /api/quota/me · 30s 刷新 · limit 0 = 不限
      </div>
    </div>
  );
}

function QuotaRow({
  label,
  w,
  primary,
  dim,
}: {
  label: string;
  w: { used: number; limit: number };
  primary?: boolean;
  dim?: boolean;
}) {
  const unlimited = w.limit === 0;
  const pct = unlimited ? 0 : Math.min((w.used / w.limit) * 100, 100);
  const valueFontSize = primary ? 22 : 15;
  return (
    <div style={{ opacity: dim ? 0.85 : 1 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          marginBottom: 4,
        }}
      >
        <span style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>{label}</span>
        <span
          style={{
            fontSize: valueFontSize,
            fontWeight: primary ? 600 : 500,
            fontFamily: "var(--font-mono)",
          }}
        >
          {formatTokens(w.used)}
          <span
            style={{
              fontSize: 12,
              color: "var(--catfish-text-muted)",
              fontWeight: 400,
            }}
          >
            {" "}
            / {unlimited ? "不限" : `${formatTokens(w.limit)} tok`}
          </span>
        </span>
      </div>
      {!unlimited && (
        <div
          style={{
            height: primary ? 6 : 4,
            background: "var(--catfish-border)",
            borderRadius: 3,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              width: `${pct}%`,
              height: "100%",
              background:
                pct > 90
                  ? "var(--catfish-danger, #d33)"
                  : pct > 70
                    ? "var(--catfish-warning, #d70)"
                    : "var(--catfish-cyan)",
            }}
          />
        </div>
      )}
    </div>
  );
}

function SkeletonRow() {
  return (
    <div
      style={{
        height: 50,
        background: "var(--catfish-border)",
        borderRadius: 4,
        opacity: 0.4,
      }}
    />
  );
}
