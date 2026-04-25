/** 模型 catalog —— 调 gateway /v1/catalog，3 状态显示 */

import { useCatalog } from "../../hooks/useCatalog";
import StatusDot from "../../components/StatusDot";
import type { CatalogModel } from "../../types/catalog";

type StatusKind = "ok" | "warn" | "idle" | "err";

function modelStatus(m: CatalogModel): { kind: StatusKind; label: string } {
  if (!m.api_key_configured) {
    return { kind: "idle", label: "未配 key" };
  }
  if (m.is_reachable === true) {
    return { kind: "ok", label: m.tier };
  }
  if (m.is_reachable === false) {
    return { kind: "warn", label: "不可达" };
  }
  // null = gateway 还没探完
  return { kind: "idle", label: "探测中" };
}

export default function CatalogCard() {
  const { catalog, error } = useCatalog();

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
      }}
    >
      <h3 style={{ marginBottom: "var(--space-3)" }}>可用模型</h3>
      {error && (
        <div style={{ color: "var(--status-err)", fontSize: 12 }}>
          gateway 不可达 —— {error}
        </div>
      )}
      {!catalog && !error && <div>加载中…</div>}
      {catalog && (
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {catalog.models.map((m) => {
            const status = modelStatus(m);
            return (
              <li
                key={m.id}
                title={m.status_reason || undefined}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: "var(--space-3)",
                  padding: "var(--space-2) 0",
                  borderBottom: "1px solid var(--catfish-border)",
                  fontSize: 13,
                }}
              >
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    flex: 1,
                  }}
                >
                  {m.display_name}
                </span>
                <StatusDot status={status.kind} label={status.label} />
              </li>
            );
          })}
          {catalog.default && (
            <li
              style={{
                fontSize: 11,
                color: "var(--catfish-text-muted)",
                paddingTop: "var(--space-2)",
              }}
            >
              默认：{catalog.default}
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
