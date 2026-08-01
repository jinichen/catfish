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
      {/* 8/1: gateway 连不上时后端不再返 Err, 改成"降级 catalog" —— 只留本机
          Codex 模型, 原因塞在 catalog.gateway_error 里。但那个字段之前**全前端
          没有一个消费者**(只有 types/catalog.ts 里一行类型声明), 于是上面那个
          error 分支从此永远不触发, 这句「gateway 不可达」成了死代码。

          净效果: 中央端证书没配好 / gateway 真挂了的时候, 员工看到的是"公司
          模型突然少了几个", 而不是"连不上 gateway"。这跟后台那几处刚修过的
          "显示 0 个 user / 0 家供应商, 拿假数字冒充真相"是同一个形状。 */}
      {catalog?.gateway_error && (
        <div
          style={{
            color: "var(--status-err)",
            fontSize: 12,
            marginBottom: "var(--space-2)",
            lineHeight: 1.6,
          }}
        >
          连不上公司 gateway —— {catalog.gateway_error}
          <div style={{ color: "var(--catfish-text-muted)", marginTop: 2 }}>
            下面只列出了本机可用的模型，<b>公司模型没有消失</b>，是这台机器现在
            取不到列表。
          </div>
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
