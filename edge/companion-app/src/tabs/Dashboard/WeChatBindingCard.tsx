/** BL-WECHAT-CATFISH-BIND v2 (5/26 鸿波): WeChat ↔ catfish 员工 email 绑定卡 (真 UI 版).
 *
 * # v1 → v2 改的原因
 *
 * v1 是 read-only "看一眼" 卡 + 折叠区贴了 4 行 CLI 命令. 鸿波 5/26 晚反馈
 * "这种方案一般人怎么会用". 一般员工不会:
 *   - 知道 hermes pairing list 是啥
 *   - 知道 ClawBot 是啥
 *   - 知道自己的 catfish email 怎么拼
 *
 * v2 把所有 admin 操作都做成 UI 按钮 — 因为 catfish 是单租户员工电脑工具,
 * Companion 本来只有员工自己能开, 员工给自己审批自己的 IM 接入 = 自己改自己
 * mac 上的文件, 不需要 admin 权限. 后端 (wechat_binding.rs) 加了 4 个写命令
 * 直接操 ~/.hermes/platforms/pairing/*.json.
 *
 * # UI 三态
 *
 * 1. 还没有 IM 数据 → 空状态引导 + "去工作台连微信"
 * 2. 有 pending (等审批) → 突出显示在最上面, 一键 ✅ 同意接入 + 绑到我自己
 * 3. 已审批列表 → 表格 + 改绑/解绑按钮
 *
 * 默认勾"绑到我自己 (autofill 当前员工 email)" — 单租户 99% case 是员工给
 * 自己的微信号绑自己, 不该让人每次都重选.
 */

import * as React from "react";
import { invoke } from "@tauri-apps/api/core";

import { fetchMe, type MeInfo } from "../../lib/me";
import WeChatArchiveCard from "./WeChatArchiveCard";
import WeChatQrLoginModal from "./WeChatQrLoginModal";

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

interface PendingEntry {
  platform: string;
  code: string;
  user_id: string;
  user_name: string;
  created_at: number;
  age_minutes: number;
}

function fmtPlatform(p: string): string {
  switch (p) {
    case "wechat":
      return "微信";
    case "feishu":
      return "飞书";
    case "telegram":
      return "Telegram";
    case "discord":
      return "Discord";
    case "whatsapp":
      return "WhatsApp";
    case "slack":
      return "Slack";
    default:
      return p;
  }
}

function fmtTime(unixSec: number): string {
  if (!unixSec || unixSec <= 0) return "—";
  return new Date(unixSec * 1000).toLocaleString();
}

function shortenId(id: string): string {
  if (id.length <= 12) return id;
  return id.slice(0, 6) + "…" + id.slice(-4);
}

export default function WeChatBindingCard() {
  const [status, setStatus] = React.useState<BindingStatus | null>(null);
  const [pending, setPending] = React.useState<PendingEntry[] | null>(null);
  const [me, setMe] = React.useState<MeInfo | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null); // key of row being mutated
  const [editingEmailFor, setEditingEmailFor] = React.useState<string | null>(null);
  const [editEmailValue, setEditEmailValue] = React.useState("");
  /** BL-WECHAT-CATFISH-BIND v3 (5/26): 微信扫码登录 modal */
  const [qrModalOpen, setQrModalOpen] = React.useState(false);

  const reload = React.useCallback(async () => {
    try {
      const [s, p] = await Promise.all([
        invoke<BindingStatus>("wechat_binding_status"),
        invoke<PendingEntry[]>("wechat_binding_pending_list"),
      ]);
      setStatus(s);
      setPending(p);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  React.useEffect(() => {
    void reload();
    // 拿一次员工 email 作 autofill
    fetchMe()
      .then(setMe)
      .catch(() => {
        // 拿不到不致命 — 审批时改邮箱输入框默认空, 员工手填
      });
  }, [reload]);

  // ========== 操作 handlers ==========

  const handleApprove = async (
    p: PendingEntry,
    bindToMe: boolean,
    customEmail: string,
  ) => {
    const key = `pending:${p.platform}:${p.code}`;
    setBusy(key);
    setError(null);
    try {
      const email = bindToMe ? (me?.email ?? "") : customEmail.trim();
      await invoke("wechat_binding_approve", {
        platform: p.platform,
        code: p.code,
        catfishEmail: email || null,
      });
      await reload();
    } catch (e) {
      setError(`审批失败: ${e}`);
    } finally {
      setBusy(null);
    }
  };

  const handleReject = async (p: PendingEntry) => {
    const key = `reject:${p.platform}:${p.code}`;
    setBusy(key);
    setError(null);
    try {
      await invoke("wechat_binding_reject", { platform: p.platform, code: p.code });
      await reload();
    } catch (e) {
      setError(`拒绝失败: ${e}`);
    } finally {
      setBusy(null);
    }
  };

  const handleSetEmail = async (e: BindingEntry, newEmail: string) => {
    const key = `setemail:${e.platform}:${e.user_id}`;
    setBusy(key);
    setError(null);
    try {
      await invoke("wechat_binding_set_email", {
        platform: e.platform,
        userId: e.user_id,
        catfishEmail: newEmail.trim(),
      });
      setEditingEmailFor(null);
      setEditEmailValue("");
      await reload();
    } catch (err) {
      setError(`改绑失败: ${err}`);
    } finally {
      setBusy(null);
    }
  };

  const handleRevoke = async (e: BindingEntry) => {
    const ok = window.confirm(
      `解绑 ${fmtPlatform(e.platform)} 用户 ${e.user_name || shortenId(e.user_id)}?\n\n` +
        `Ta 下次再发消息会重新出现在"待审批"里, 不是真删数据.`,
    );
    if (!ok) return;
    const key = `revoke:${e.platform}:${e.user_id}`;
    setBusy(key);
    setError(null);
    try {
      await invoke("wechat_binding_revoke", { platform: e.platform, userId: e.user_id });
      await reload();
    } catch (err) {
      setError(`解绑失败: ${err}`);
    } finally {
      setBusy(null);
    }
  };

  const hasPending = (pending?.length ?? 0) > 0;
  const hasApproved = (status?.entries.length ?? 0) > 0;
  const isLoading = status === null && pending === null;

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
        <h3 style={{ margin: 0 }}>💬 微信接入</h3>
        {/* BL-WECHAT-CATFISH-BIND v3 (5/26): 一键扫码绑自己微信. 后端走 hermes
            /api/platforms/wechat/qr_login/start → ilink. 替代 hermes setup CLI. */}
        <button
          type="button"
          onClick={() => setQrModalOpen(true)}
          style={btnPrimary(false)}
          title="弹出微信扫码登录"
        >
          📱 扫码绑微信
        </button>
        {/* 6/1 鸿波: 删副标题 + 🔄 刷新按钮.
            副标题: 跟按钮重复, "共享记忆/配额池" 反向暗示让员工联想.
            刷新: 所有写操作 (扫码 / 审批 / 拒绝 / 解绑 / 改 email) 自动 reload,
                  没 setInterval poll = 没操作时数据不会变 = 按钮是 dead. */}
      </div>

      {qrModalOpen && (
        <WeChatQrLoginModal
          onClose={() => setQrModalOpen(false)}
          onConfirmed={() => {
            // 登录成功 → 等几秒 (用户读完成功提示) 再 reload, ClawBot 重启后
            // pending/approved 表会出新数据.
            window.setTimeout(() => void reload(), 1500);
          }}
        />
      )}

      {error && (
        <div
          style={{
            fontSize: 12,
            color: "var(--status-err, #c93a3a)",
            marginBottom: 10,
            padding: 6,
            background: "rgba(201, 58, 58, 0.08)",
            borderRadius: 4,
          }}
        >
          {error}
        </div>
      )}

      {isLoading && <div style={{ fontSize: 13 }}>加载中…</div>}

      {/* ========== 状态 1: 空状态引导 (6/1 鸿波: 简化, 去 3 步 CLI 过时流程) ========== */}
      {!isLoading && !hasPending && !hasApproved && (
        <div style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>
          <p style={{ margin: 0, lineHeight: 1.7 }}>
            点上面 <strong>📱 扫码绑微信</strong> 按钮, 跟着提示扫一下就好.
          </p>
        </div>
      )}

      {/* ========== 状态 2: pending 待审批 (最显眼) ========== */}
      {hasPending && (
        <div style={{ marginBottom: hasApproved ? 16 : 0 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 8,
            }}
          >
            <strong style={{ fontSize: 13, color: "var(--status-warn, #c98b00)" }}>
              🔔 等你审批 ({pending!.length})
            </strong>
            <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
              点同意 = 这个人下次发消息能直通鲶鱼; 点拒绝 = 静悄悄不处理
            </span>
          </div>
          {pending!.map((p) => (
            <PendingRow
              key={`${p.platform}:${p.code}`}
              p={p}
              myEmail={me?.email ?? ""}
              busy={busy}
              onApprove={handleApprove}
              onReject={handleReject}
            />
          ))}
        </div>
      )}

      {/* ========== 状态 3: 已审批列表 ========== */}
      {hasApproved && (
        <div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 8,
              fontSize: 13,
            }}
          >
            <strong>🔗 已接入 ({status!.entries.length})</strong>
            <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
              {status!.total_bound} 个绑了真员工 ·{" "}
              {status!.total_approved - status!.total_bound} 个走合成身份 (跟真员工隔离)
            </span>
          </div>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--catfish-text-muted)" }}>
                <th style={th()}>平台</th>
                <th style={th()}>谁</th>
                <th style={th()}>绑到的员工身份</th>
                <th style={th()}>接入时间</th>
                <th style={{ ...th(), textAlign: "right" }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {status!.entries.map((e) => {
                const rowKey = `${e.platform}:${e.user_id}`;
                const setEmailBusy = busy === `setemail:${e.platform}:${e.user_id}`;
                const revokeBusy = busy === `revoke:${e.platform}:${e.user_id}`;
                const isEditing = editingEmailFor === rowKey;
                return (
                  <tr key={rowKey} style={{ borderTop: "1px solid var(--catfish-border)" }}>
                    <td style={td()}>{fmtPlatform(e.platform)}</td>
                    <td style={td()}>
                      <div style={{ fontWeight: 500 }}>{e.user_name || "(没昵称)"}</div>
                      <div
                        style={{
                          fontFamily: "monospace",
                          fontSize: 10,
                          color: "var(--catfish-text-muted)",
                        }}
                        title={e.user_id}
                      >
                        {shortenId(e.user_id)}
                      </div>
                    </td>
                    <td style={td()}>
                      {isEditing ? (
                        <div style={{ display: "flex", gap: 4 }}>
                          <input
                            type="email"
                            value={editEmailValue}
                            onChange={(ev) => setEditEmailValue(ev.target.value)}
                            placeholder="alice@company.com"
                            style={{
                              flex: 1,
                              padding: "2px 6px",
                              fontSize: 12,
                              background: "var(--catfish-bg-base, #1a1d23)",
                              border: "1px solid var(--catfish-border)",
                              color: "inherit",
                              borderRadius: 3,
                            }}
                            autoFocus
                          />
                          <button
                            type="button"
                            onClick={() => void handleSetEmail(e, editEmailValue)}
                            disabled={setEmailBusy || !editEmailValue.trim()}
                            style={btnPrimary(setEmailBusy)}
                          >
                            ✓
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setEditingEmailFor(null);
                              setEditEmailValue("");
                            }}
                            style={btnGhost(false)}
                          >
                            取消
                          </button>
                        </div>
                      ) : e.catfish_email ? (
                        <span style={{ color: "var(--status-ok, #2a8b3f)" }}>
                          ✓ {e.catfish_email}
                        </span>
                      ) : (
                        <span style={{ color: "var(--status-warn, #c98b00)" }}>
                          ⚠ 未绑 · 走合成{" "}
                          <code style={{ fontSize: 10 }}>
                            {e.user_id}@im.{e.platform}
                          </code>
                        </span>
                      )}
                    </td>
                    <td style={{ ...td(), color: "var(--catfish-text-muted)" }}>
                      {fmtTime(e.approved_at)}
                    </td>
                    <td style={{ ...td(), textAlign: "right", whiteSpace: "nowrap" }}>
                      {!isEditing && (
                        <>
                          {!e.catfish_email && me?.email && (
                            <button
                              type="button"
                              onClick={() => void handleSetEmail(e, me.email)}
                              disabled={setEmailBusy}
                              title={`绑到我自己 (${me.email})`}
                              style={btnPrimary(setEmailBusy)}
                            >
                              绑到我
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => {
                              setEditingEmailFor(rowKey);
                              setEditEmailValue(e.catfish_email ?? me?.email ?? "");
                            }}
                            style={{ ...btnGhost(false), marginLeft: 4 }}
                          >
                            改绑
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleRevoke(e)}
                            disabled={revokeBusy}
                            style={{
                              ...btnGhost(revokeBusy),
                              marginLeft: 4,
                              color: "var(--status-err, #c93a3a)",
                            }}
                          >
                            {revokeBusy ? "…" : "解绑"}
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <WeChatArchiveCard />

      {/* 6/1 鸿波: 删底部 "~/.hermes/platforms/pairing + hermes pairing list" 技术细节,
          跟隐私卡同原则 — 不让员工联想, 也不教 CLI. 真员工 IT 自查走另外的 catfish
          privacy-audit CLI. */}
    </div>
  );
}

// ============== sub-components ==============

interface PendingRowProps {
  p: PendingEntry;
  myEmail: string;
  busy: string | null;
  onApprove: (p: PendingEntry, bindToMe: boolean, customEmail: string) => Promise<void>;
  onReject: (p: PendingEntry) => Promise<void>;
}

function PendingRow({ p, myEmail, busy, onApprove, onReject }: PendingRowProps) {
  const [bindToMe, setBindToMe] = React.useState(true);
  const [customEmail, setCustomEmail] = React.useState("");
  const approveKey = `pending:${p.platform}:${p.code}`;
  const rejectKey = `reject:${p.platform}:${p.code}`;
  const isApproving = busy === approveKey;
  const isRejecting = busy === rejectKey;

  return (
    <div
      style={{
        border: "1px solid var(--status-warn, #c98b00)",
        borderRadius: 6,
        padding: 10,
        marginBottom: 6,
        background: "rgba(201, 139, 0, 0.06)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 8,
          flexWrap: "wrap",
          marginBottom: 6,
        }}
      >
        <strong style={{ fontSize: 13 }}>
          {fmtPlatform(p.platform)} · {p.user_name || "(没昵称)"}
        </strong>
        <span
          style={{
            fontSize: 10,
            fontFamily: "monospace",
            color: "var(--catfish-text-muted)",
          }}
          title={p.user_id}
        >
          {shortenId(p.user_id)}
        </span>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          {p.age_minutes < 1 ? "刚刚" : `${p.age_minutes} 分钟前`}发起接入请求
        </span>
      </div>

      <div
        style={{
          display: "flex",
          gap: 8,
          alignItems: "center",
          flexWrap: "wrap",
          fontSize: 12,
          marginBottom: 6,
        }}
      >
        <label style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={bindToMe}
            onChange={(e) => setBindToMe(e.target.checked)}
            disabled={!myEmail}
          />
          <span>
            绑到我自己{" "}
            {myEmail ? (
              <code style={{ fontSize: 11 }}>({myEmail})</code>
            ) : (
              <span style={{ color: "var(--status-warn, #c98b00)" }}>
                — 还没拿到你的 email, 请在下面手填
              </span>
            )}
          </span>
        </label>
        {!bindToMe && (
          <input
            type="email"
            placeholder="或填别人的 email (代审批)"
            value={customEmail}
            onChange={(e) => setCustomEmail(e.target.value)}
            style={{
              flex: 1,
              minWidth: 200,
              padding: "2px 6px",
              fontSize: 12,
              background: "var(--catfish-bg-base, #1a1d23)",
              border: "1px solid var(--catfish-border)",
              color: "inherit",
              borderRadius: 3,
            }}
          />
        )}
      </div>

      <div style={{ display: "flex", gap: 6 }}>
        <button
          type="button"
          onClick={() => void onApprove(p, bindToMe, customEmail)}
          disabled={
            isApproving ||
            isRejecting ||
            (bindToMe && !myEmail) ||
            (!bindToMe && !customEmail.trim())
          }
          style={{
            ...btnPrimary(isApproving),
            padding: "4px 12px",
          }}
        >
          {isApproving ? "处理中…" : "✅ 同意接入"}
        </button>
        <button
          type="button"
          onClick={() => void onReject(p)}
          disabled={isApproving || isRejecting}
          style={{
            ...btnGhost(isRejecting),
            padding: "4px 12px",
            color: "var(--status-err, #c93a3a)",
          }}
        >
          {isRejecting ? "…" : "❌ 拒绝"}
        </button>
      </div>
    </div>
  );
}

// ============== styles ==============

function th(): React.CSSProperties {
  return { padding: "4px 8px 4px 0", fontWeight: 400 };
}

function td(): React.CSSProperties {
  return { padding: "8px 8px 8px 0", verticalAlign: "top" };
}

function btnGhost(disabled: boolean): React.CSSProperties {
  return {
    background: "transparent",
    border: "1px solid var(--catfish-border)",
    borderRadius: 4,
    padding: "2px 8px",
    cursor: disabled ? "default" : "pointer",
    fontSize: 12,
    opacity: disabled ? 0.6 : 1,
    color: "inherit",
  };
}

function btnPrimary(busy: boolean): React.CSSProperties {
  return {
    background: "var(--catfish-accent, #2a7fbb)",
    border: "1px solid var(--catfish-accent, #2a7fbb)",
    borderRadius: 4,
    padding: "2px 8px",
    cursor: busy ? "default" : "pointer",
    fontSize: 12,
    color: "#fff",
    opacity: busy ? 0.6 : 1,
  };
}
