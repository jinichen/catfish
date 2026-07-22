/** Dashboard 卡 — "账号安全" (BL-SELF-CHANGE-PASSWORD 7/20 鸿波 catch)
 *
 * 员工自主修改密码入口. 之前只有 admin 能 reset (users_admin API) ·
 * 员工装完首用 admin 临时密码 · 无法自主改 · 安全漏.
 *
 * 本卡:
 *   - 显示当前员工邮箱 (只读)
 *   - 显示 must_change_password 状态 (若 true · 顶部红标 · 强制引导员工改)
 *   - "修改密码" 按钮 → 弹 Modal · 3 输入框 · 校验强度 · 调 POST /me/password
 */

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { invoke } from "@tauri-apps/api/core";

// identity URL 从 companion.yaml oidc.issuer 读
async function getIdentityUrl(): Promise<string> {
  try {
    const cfg = await invoke<{ identity_url: string }>("read_server_config");
    return cfg.identity_url || "http://127.0.0.1:8998";
  } catch {
    return "http://127.0.0.1:8998";
  }
}

async function getAccessToken(): Promise<string | null> {
  try {
    return await invoke<string | null>("auth_get_access_token");
  } catch {
    return null;
  }
}

interface AuthState {
  authenticated: boolean;
  email: string;
  name: string;
  auth_method: string;
}

async function getAuthState(): Promise<AuthState | null> {
  try {
    return await invoke<AuthState>("auth_whoami");
  } catch {
    return null;
  }
}

/** BL-PW-STRENGTH (7/20 UI 迭代 · 鸿波 catch 输入框太空): 简单三段强度评估.
 * 弱 (0/1) / 中 (2) / 强 (3+). 判分点: 长度 12+, 有小写+大写, 有数字, 有符号.
 * 不做 haveibeenpwned 之类联网 lookup · 政企内网可能不通. */
function pwStrength(p: string): { score: 0 | 1 | 2 | 3; label: string; color: string } {
  if (!p) return { score: 0, label: "", color: "var(--catfish-border)" };
  let s = 0;
  if (p.length >= 12) s += 1;
  if (/[a-z]/.test(p) && /[A-Z]/.test(p)) s += 1;
  if (/[0-9]/.test(p)) s += 1;
  if (/[^A-Za-z0-9]/.test(p)) s += 1;
  if (p.length < 8) return { score: 0, label: "太短", color: "var(--status-err)" };
  if (s <= 1) return { score: 1, label: "弱", color: "var(--status-err)" };
  if (s === 2) return { score: 2, label: "中", color: "var(--status-warn)" };
  return { score: 3, label: "强", color: "var(--status-ok)" };
}

/** 眼睛 icon toggle. inline SVG lucide-style stroke · 12px · muted 色.
 * 之前用 emoji 👁 · macOS 默认渲染 22px+ 巨大 · 与主题不搭 · 换 SVG. */
function EyeButton({ shown, onToggle }: { shown: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      tabIndex={-1}
      aria-label={shown ? "隐藏密码" : "显示密码"}
      title={shown ? "隐藏密码" : "显示密码"}
      style={{
        position: "absolute",
        right: 8,
        top: "50%",
        transform: "translateY(-50%)",
        background: "transparent",
        border: "none",
        cursor: "pointer",
        padding: 2,
        color: "var(--catfish-text-muted)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        lineHeight: 0,
        borderRadius: 3,
      }}
    >
      {shown ? (
        // eye-off · lucide 24px viewBox
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M9.88 9.88a3 3 0 1 0 4.24 4.24" />
          <path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68" />
          <path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61" />
          <line x1="2" y1="2" x2="22" y2="22" />
        </svg>
      ) : (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      )}
    </button>
  );
}

export default function AccountSecurityCard() {
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [old, setOld] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  // 每输入框独立 shown state · 用户可分别切 (旧密码可能确认输对了但新密码想验证)
  const [showOld, setShowOld] = useState(false);
  const [showNext, setShowNext] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  // CapsLock 检测 · Bailian POC 常见坑: 大写锁定输错 · 员工不知
  const [capsOn, setCapsOn] = useState(false);

  useEffect(() => {
    void getAuthState().then(setAuth);
  }, []);

  // Modal 打开时监听 keydown/keyup · 侦测 CapsLock. 关闭时 cleanup.
  useEffect(() => {
    if (!showModal) return;
    const handler = (e: KeyboardEvent) => {
      if (typeof e.getModifierState === "function") {
        setCapsOn(e.getModifierState("CapsLock"));
      }
    };
    window.addEventListener("keydown", handler);
    window.addEventListener("keyup", handler);
    return () => {
      window.removeEventListener("keydown", handler);
      window.removeEventListener("keyup", handler);
    };
  }, [showModal]);

  const strength = pwStrength(next);
  const confirmMismatch = confirm.length > 0 && confirm !== next;

  const email = auth?.email || "";
  const authenticated = auth?.authenticated === true;
  const isDevToken = auth?.auth_method === "dev_token";

  const closeModal = () => {
    setShowModal(false);
    setOld("");
    setNext("");
    setConfirm("");
    setMsg(null);
  };

  const submit = async () => {
    setMsg(null);
    // 前端强度校验 · 8+ 位
    if (next.length < 8) {
      setMsg({ kind: "err", text: "新密码至少 8 位" });
      return;
    }
    if (next === old) {
      setMsg({ kind: "err", text: "新密码不能与旧密码相同" });
      return;
    }
    if (next !== confirm) {
      setMsg({ kind: "err", text: "两次输入的新密码不一致" });
      return;
    }

    setBusy(true);
    try {
      const identityUrl = await getIdentityUrl();
      const token = await getAccessToken();
      if (!token) {
        setMsg({ kind: "err", text: "未登录 · 请先 SSO 登录" });
        return;
      }
      const resp = await fetch(`${identityUrl.replace(/\/+$/, "")}/me/password`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ old_password: old, new_password: next }),
      });
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try {
          const body = (await resp.json()) as { detail?: string };
          if (body?.detail) detail = body.detail;
        } catch {
          /* ignore parse */
        }
        setMsg({ kind: "err", text: detail });
        return;
      }
      // 军规 7/20 鸿波 catch "改完密码不重新登录":
      // 后端已 revoke 该 user 所有 refresh_token · 前端也要立即 logout ·
      // 清 Keychain access/refresh_token · useAuth 60s poll 会检测未登录 →
      // LoginGate 立即弹登录 · 员工用新密码 SSO 重登.
      // 不主动 logout 的话 · access_token TTL 内 (~1h) 还能继续用 · 与"改密"
      // 语义违背.
      setMsg({ kind: "ok", text: "密码已修改 · 3 秒后自动登出 · 请用新密码重登" });
      setTimeout(async () => {
        try {
          await invoke("auth_logout");
        } catch (e) {
          console.warn("[AccountSecurity] auth_logout 失败:", e);
        }
        closeModal();
        // 触发 useAuth 立即重查 (不等 60s poll). LoginGate 见 authenticated=false
        // 弹登录 modal · 员工输新密码走 SSO.
        window.location.reload();
      }, 3000);
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-2)", marginBottom: "var(--space-3)" }}>
        <h3 style={{ margin: 0 }}>🔐 账号安全</h3>
      </div>

      <div style={{ fontSize: 13, color: "var(--catfish-text-muted)", marginBottom: "var(--space-3)" }}>
        邮箱 · <span style={{ color: "var(--catfish-text)" }}>{email || "未登录"}</span>
        {isDevToken && (
          <span style={{ marginLeft: 8, fontSize: 11, color: "var(--status-warn)", border: "1px solid var(--status-warn)", borderRadius: 3, padding: "0 4px" }}>
            dev_token 模式
          </span>
        )}
      </div>

      {!authenticated && (
        <div style={{ fontSize: 12, color: "var(--status-err)", marginBottom: "var(--space-3)" }}>
          未登录 · 请先在其他 Card (如 · 服务器配置) 完成 SSO 登录 · 再来改密.
        </div>
      )}

      {authenticated && isDevToken && (
        <div style={{ fontSize: 12, color: "var(--status-warn)", marginBottom: "var(--space-3)" }}>
          dev_token 模式无密码 · 修改密码不适用. 生产 SSO 才能改.
        </div>
      )}

      <button
        onClick={() => setShowModal(true)}
        disabled={!authenticated || isDevToken}
        style={{
          fontSize: 13,
          padding: "6px 12px",
          background: "var(--catfish-cyan)",
          color: "white",
          border: "none",
          borderRadius: "var(--radius-sm)",
          cursor: (authenticated && !isDevToken) ? "pointer" : "not-allowed",
          opacity: (authenticated && !isDevToken) ? 1 : 0.5,
        }}
      >
        修改密码
      </button>

      {showModal && createPortal(
        // BL-PW-MODAL-PORTAL (7/20 鸿波 catch "modal 挤边"): portal 到 body ·
        // 逃 CollapsibleSection / Tabs 祖先 · 保证 fixed inset:0 是相对 viewport ·
        // 不被 ancestor transform / contain / filter 破坏 · 且不受父容器 padding 挤位.
        // 复用项目 install-dialog CSS · 与 SkillsMcpCard modal 视觉一致.
        <div
          className="install-dialog__backdrop"
          onClick={() => { if (!busy) closeModal(); }}
        >
          <div
            className="install-dialog"
            role="dialog"
            aria-modal="true"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="install-dialog__header">
              <h4>修改密码</h4>
              <button
                className="install-dialog__close"
                onClick={closeModal}
                disabled={busy}
                aria-label="关闭"
              >
                ×
              </button>
            </div>
            <div className="install-dialog__body">
              <div className="install-dialog__hint">
                新密码至少 8 位 · 建议含大小写 / 数字 / 符号
              </div>

              {capsOn && (
                <div className="install-dialog__warn" style={{ color: "var(--status-warn)", fontStyle: "normal", marginBottom: 8 }}>
                  CapsLock 已开启 · 注意大小写
                </div>
              )}

              <label className="install-dialog__label">旧密码</label>
              <div style={{ position: "relative" }}>
                <input
                  className="install-dialog__input"
                  type={showOld ? "text" : "password"}
                  value={old}
                  onChange={(e) => setOld(e.target.value)}
                  autoFocus
                  autoComplete="current-password"
                  style={{ paddingRight: 32, fontFamily: "inherit" }}
                />
                <EyeButton shown={showOld} onToggle={() => setShowOld((v) => !v)} />
              </div>

              <label className="install-dialog__label" style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span>新密码</span>
                {strength.label && (
                  <span style={{ fontSize: 10, color: strength.color, textTransform: "none", letterSpacing: 0, fontWeight: 500 }}>
                    强度 · {strength.label}
                  </span>
                )}
              </label>
              <div style={{ position: "relative" }}>
                <input
                  className="install-dialog__input"
                  type={showNext ? "text" : "password"}
                  value={next}
                  onChange={(e) => setNext(e.target.value)}
                  autoComplete="new-password"
                  style={{ paddingRight: 32, fontFamily: "inherit" }}
                />
                <EyeButton shown={showNext} onToggle={() => setShowNext((v) => !v)} />
              </div>
              {next && (
                <div style={{ display: "flex", gap: 3, marginTop: 6 }}>
                  {[0, 1, 2].map((i) => (
                    <div
                      key={i}
                      style={{
                        flex: 1,
                        height: 3,
                        borderRadius: 2,
                        background: i < strength.score ? strength.color : "var(--catfish-border)",
                        transition: "background 150ms ease",
                      }}
                    />
                  ))}
                </div>
              )}

              <label className="install-dialog__label" style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span>确认新密码</span>
                {confirmMismatch && (
                  <span style={{ fontSize: 10, color: "var(--status-err)", textTransform: "none", letterSpacing: 0, fontWeight: 500 }}>
                    两次输入不一致
                  </span>
                )}
                {confirm && !confirmMismatch && (
                  <span style={{ fontSize: 10, color: "var(--status-ok)", textTransform: "none", letterSpacing: 0, fontWeight: 500 }}>
                    ✓ 一致
                  </span>
                )}
              </label>
              <div style={{ position: "relative" }}>
                <input
                  className="install-dialog__input"
                  type={showConfirm ? "text" : "password"}
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  autoComplete="new-password"
                  style={{
                    paddingRight: 32,
                    fontFamily: "inherit",
                    borderColor: confirmMismatch ? "var(--status-err)" : undefined,
                  }}
                />
                <EyeButton shown={showConfirm} onToggle={() => setShowConfirm((v) => !v)} />
              </div>

              {msg && (
                <div
                  className="install-dialog__warn"
                  style={{
                    color: msg.kind === "ok" ? "var(--status-ok)" : "var(--status-err)",
                    fontStyle: "normal",
                    marginTop: 12,
                  }}
                >
                  {msg.text}
                </div>
              )}

              <div className="install-dialog__actions">
                <button
                  className="approval-banner__btn-link"
                  onClick={closeModal}
                  disabled={busy}
                >
                  取消
                </button>
                <button
                  className="approval-banner__btn-primary"
                  onClick={() => void submit()}
                  disabled={busy || !old || !next || !confirm || confirmMismatch || strength.score === 0}
                >
                  {busy ? "提交中..." : "确认修改"}
                </button>
              </div>
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
