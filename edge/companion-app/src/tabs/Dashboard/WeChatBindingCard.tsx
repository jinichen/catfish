/** BL-WECHAT-CATFISH-BIND v1 (5/26 鸿波): WeChat ↔ catfish 员工 email 绑定卡.
 *
 * # 这卡是干嘛的
 *
 * 鲶鱼 (catfish-gateway) 是按 "员工 email" 切隔离的 — 同一个员工的对话 / 记忆 /
 * 配额 / 审计都挂在 ta 的 email 下. 但 WeChat / 飞书等 IM 平台发过来的消息只
 * 带 openid, 跟员工 email 没天然映射. 之前 (5/26 早上) ClawBot 把所有 WeChat
 * 用户都映射到默认员工身上 — 100 个微信用户共用一个员工的记忆 = P0 隐私违规.
 *
 * 修复后 (5/26 晚 BL-WECHAT-CATFISH-BIND v1):
 *   1. admin 用 `hermes pairing approve wechat <code> --email alice@company.com`
 *      把 openid 显式绑到真员工 → 该 WeChat 用户的消息走真员工身份.
 *   2. 没绑定的 → 走合成身份 <openid>@im.wechat (跟真员工隔离).
 *
 * 这张卡负责把"已绑定 / 未绑定"看见, 让员工 / admin 一眼知道哪些 WeChat 用户
 * 已经绑了哪个真员工 email. 卡本身是 read-only — 写要走 hermes CLI (因为绑定
 * 是 admin 操作, 不该任何打开 Companion 的人都能改).
 *
 * # 数据流
 *
 * Tauri command `wechat_binding_status` 读 ~/.hermes/platforms/pairing/<platform>-approved.json,
 * 一次返所有平台的绑定状态. Companion 跑在员工 mac, 读自己的 ~/.hermes 天然合规
 * (中央 0 字节红线只管中央 PG/disk, 不管员工本机).
 *
 * # 故意不做
 *
 * - 不在 UI 里直接改绑定: 绑定 = admin 操作, 走 CLI 留 shell history, 不在 web UI 放按钮.
 * - 不显示 openid 全文以外的 PII: openid 已经是 platform-internal id, user_name 是平台昵称,
 *   都不算隐私扩散.
 * - 不轮询: 绑定改动频率极低 (人肉运维), 用户点"刷新"按钮就够.
 */

import * as React from "react";
import { invoke } from "@tauri-apps/api/core";

interface BindingEntry {
  platform: string;
  user_id: string;
  user_name: string;
  catfish_email: string | null;
  approved_at: number;
  email_bound_at: number | null;
}

interface BindingStatus {
  entries: BindingEntry[];
  total_approved: number;
  total_bound: number;
  pairing_dir_exists: boolean;
}

export default function WeChatBindingCard() {
  const [status, setStatus] = React.useState<BindingStatus | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);

  const reload = React.useCallback(async () => {
    setLoading(true);
    try {
      const r = await invoke<BindingStatus>("wechat_binding_status");
      setStatus(r);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void reload();
  }, [reload]);

  const unboundCount = status ? status.total_approved - status.total_bound : 0;

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        gridColumn: "1 / -1",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          marginBottom: "var(--space-3)",
          flexWrap: "wrap",
        }}
      >
        <h3 style={{ margin: 0 }}>💬 IM 平台 ↔ 员工绑定</h3>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          WeChat / 飞书 等用户 ↔ catfish 真员工 email 映射 · 隔离记忆 / 配额 / 审计
        </span>
        <button
          type="button"
          onClick={() => void reload()}
          disabled={loading}
          style={{
            marginLeft: "auto",
            background: "transparent",
            border: "1px solid var(--catfish-border)",
            borderRadius: 4,
            padding: "2px 8px",
            cursor: loading ? "default" : "pointer",
            fontSize: 12,
            opacity: loading ? 0.6 : 1,
          }}
        >
          {loading ? "…" : "🔄 刷新"}
        </button>
      </div>

      {error && (
        <div style={{ fontSize: 12, color: "var(--status-err, #c93a3a)", marginBottom: 8 }}>
          读取失败: {error}
        </div>
      )}

      {status === null && !error && <div style={{ fontSize: 13 }}>加载中…</div>}

      {status !== null && !status.pairing_dir_exists && (
        <div style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>
          <p style={{ margin: 0 }}>
            还没跑过 hermes pairing 流程 — ~/.hermes/platforms/pairing/ 不存在.
          </p>
          <p style={{ margin: "6px 0 0 0", fontSize: 12 }}>
            想用 IM 平台 (WeChat / 飞书) 接 catfish? 先跑 <code>hermes setup</code> 选平台,
            ClawBot 把 IM 消息送进 hermes 后, 这里就会出现待审批用户.
          </p>
        </div>
      )}

      {status !== null && status.pairing_dir_exists && status.entries.length === 0 && (
        <div style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>
          目录在, 但还没有任何已审批用户. 在 IM 上对 bot 发一句话 → 跑{" "}
          <code>hermes pairing list</code> 看 pending → <code>hermes pairing approve</code>{" "}
          批准 (可选 <code>--email alice@company.com</code> 直接绑真员工).
        </div>
      )}

      {status !== null && status.entries.length > 0 && (
        <>
          <div
            style={{
              display: "flex",
              gap: 16,
              fontSize: 12,
              color: "var(--catfish-text-muted)",
              marginBottom: 8,
              flexWrap: "wrap",
            }}
          >
            <span>
              已审批 <strong>{status.total_approved}</strong> 人
            </span>
            <span>
              已绑真员工 <strong style={{ color: "var(--status-ok, #2a8b3f)" }}>{status.total_bound}</strong>
            </span>
            {unboundCount > 0 && (
              <span>
                走合成身份{" "}
                <strong style={{ color: "var(--status-warn, #c98b00)" }}>{unboundCount}</strong>
                <span style={{ marginLeft: 4, opacity: 0.8 }}>
                  (走 &lt;openid&gt;@im.&lt;platform&gt;, 跟真员工隔离)
                </span>
              </span>
            )}
          </div>

          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--catfish-text-muted)" }}>
                <th style={{ padding: "4px 8px 4px 0", fontWeight: 400 }}>平台</th>
                <th style={{ padding: "4px 8px", fontWeight: 400 }}>平台用户 ID</th>
                <th style={{ padding: "4px 8px", fontWeight: 400 }}>昵称</th>
                <th style={{ padding: "4px 8px", fontWeight: 400 }}>绑定的真员工 email</th>
                <th style={{ padding: "4px 0", fontWeight: 400 }}>审批时间</th>
              </tr>
            </thead>
            <tbody>
              {status.entries.map((e) => (
                <tr
                  key={`${e.platform}:${e.user_id}`}
                  style={{ borderTop: "1px solid var(--catfish-border)" }}
                >
                  <td style={{ padding: "6px 8px 6px 0", fontFamily: "monospace" }}>{e.platform}</td>
                  <td
                    style={{
                      padding: "6px 8px",
                      fontFamily: "monospace",
                      maxWidth: 240,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={e.user_id}
                  >
                    {e.user_id}
                  </td>
                  <td style={{ padding: "6px 8px", color: "var(--catfish-text-muted)" }}>
                    {e.user_name || "—"}
                  </td>
                  <td style={{ padding: "6px 8px" }}>
                    {e.catfish_email ? (
                      <span style={{ color: "var(--status-ok, #2a8b3f)" }}>
                        ✓ {e.catfish_email}
                      </span>
                    ) : (
                      <span style={{ color: "var(--status-warn, #c98b00)" }}>
                        未绑定 · 走 <code style={{ fontSize: 11 }}>{e.user_id}@im.{e.platform}</code>
                      </span>
                    )}
                  </td>
                  <td style={{ padding: "6px 0", color: "var(--catfish-text-muted)" }}>
                    {fmtTime(e.approved_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <details style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: 12 }}>
        <summary style={{ cursor: "pointer" }}>怎么绑定 / 解绑?</summary>
        <pre
          style={{
            fontSize: 11,
            background: "var(--catfish-bg-base, #0f1216)",
            padding: 8,
            borderRadius: 4,
            margin: "6px 0 0 0",
            whiteSpace: "pre-wrap",
            wordBreak: "break-all",
          }}
        >
{`# 1. 看待审批
hermes pairing list

# 2. 审批 + 直接绑真员工 (推荐 — 一步到位)
hermes pairing approve wechat ABCD1234 --email alice@company.com

# 3. 给已审批用户后补绑定 / 改绑
hermes pairing bind-email wechat <openid> alice@company.com

# 4. 解绑 (整个移出 approved 表)
hermes pairing revoke wechat <openid>
`}
        </pre>
        <p style={{ margin: "6px 0 0 0" }}>
          绑定 = admin 操作, 故意不放 UI 按钮 — 走 CLI 留 shell history 便于审计.
        </p>
      </details>
    </div>
  );
}

function fmtTime(unixSec: number): string {
  if (!unixSec || unixSec <= 0) return "—";
  const d = new Date(unixSec * 1000);
  return d.toLocaleString();
}
