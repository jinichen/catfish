import { useState, type FormEvent, type MouseEvent } from "react";
import { useNavigate } from "react-router-dom";

import { Card } from "../components/Card";
import { logout } from "../lib/auth";
import { changeOwnPassword } from "../lib/me";
import { useAuthStore } from "../store/auth";

const fieldStyle = {
  width: "100%",
  boxSizing: "border-box" as const,
  padding: "9px 10px",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  background: "var(--bg-elev)",
  color: "var(--text)",
  fontSize: 13,
};

/** 兼容旧书签的独立地址；正常入口由 NavBar 直接打开弹窗。 */
export function PasswordPage() {
  const me = useAuthStore((s) => s.me);
  const navigate = useNavigate();

  if (!me) return null;

  return (
    <PasswordDialog
      forced={me.must_change_password}
      onClose={() => navigate("/", { replace: true })}
    />
  );
}

export function PasswordDialog({
  forced,
  onClose,
}: {
  forced: boolean;
  onClose: () => void;
}) {
  const me = useAuthStore((s) => s.me);
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);

  if (!me) return null;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    if (newPassword.length < 8) {
      setError("新密码至少需要 8 位。");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("两次输入的新密码不一致。");
      return;
    }
    if (oldPassword === newPassword) {
      setError("新密码不能与当前密码相同。");
      return;
    }

    setSaving(true);
    try {
      const result = await changeOwnPassword(oldPassword, newPassword);
      setSuccess(result.message || "密码已修改，请重新登录。");
      // Identity 会撤销 refresh token。主动退出，避免页面继续使用旧会话。
      await logout();
    } catch (e) {
      setError(e instanceof Error ? e.message : "密码修改失败，请稍后重试。");
      setSaving(false);
    }
  }

  function closeFromBackdrop(event: MouseEvent<HTMLDivElement>) {
    if (!forced && event.target === event.currentTarget && !saving) onClose();
  }

  return (
    <div
      role="presentation"
      onMouseDown={closeFromBackdrop}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "var(--space-4)",
        background: "rgba(0, 0, 0, 0.35)",
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="password-dialog-title"
        style={{ width: "min(100%, 460px)" }}
      >
        <Card
          title={
            <span id="password-dialog-title" style={{ fontSize: 18 }}>
              修改登录密码
            </span>
          }
          action={
            !forced && (
              <button
                type="button"
                aria-label="关闭"
                onClick={() => {
                  if (!saving) onClose();
                }}
                disabled={saving}
                style={{
                  border: 0,
                  background: "transparent",
                  color: "var(--text-muted)",
                  cursor: "pointer",
                  fontSize: 20,
                  lineHeight: 1,
                }}
              >
                ×
              </button>
            )
          }
        >
          <div style={{ color: "var(--text-muted)", fontSize: 13, lineHeight: 1.6 }}>
            当前账号：<b style={{ color: "var(--text)" }}>{me.email}</b>
          </div>

          {forced && (
            <div
              style={{
                marginTop: "var(--space-3)",
                padding: "var(--space-3)",
                border: "1px solid var(--status-warn)",
                borderRadius: "var(--radius-sm)",
                color: "var(--text)",
                background: "var(--bg)",
                fontSize: 13,
                lineHeight: 1.6,
              }}
            >
              这是首次登录或管理员重置后的临时密码，请先修改密码才能继续使用门户。
            </div>
          )}

          <form
            onSubmit={(event) => void submit(event)}
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-3)",
              marginTop: "var(--space-4)",
            }}
          >
            <PasswordField
              id="old-password"
              label="当前密码"
              value={oldPassword}
              onChange={setOldPassword}
              autoComplete="current-password"
              autoFocus
            />
            <PasswordField
              id="new-password"
              label="新密码"
              hint="至少 8 位"
              value={newPassword}
              onChange={setNewPassword}
              autoComplete="new-password"
            />
            <PasswordField
              id="confirm-password"
              label="确认新密码"
              value={confirmPassword}
              onChange={setConfirmPassword}
              autoComplete="new-password"
            />

            {error && (
              <div style={{ color: "var(--status-err)", fontSize: 13, whiteSpace: "pre-wrap" }}>
                {error}
              </div>
            )}
            {success && (
              <div style={{ color: "var(--status-ok)", fontSize: 13 }}>{success}</div>
            )}

            <button
              type="submit"
              disabled={saving}
              style={{
                alignSelf: "flex-start",
                border: 0,
                borderRadius: "var(--radius-sm)",
                padding: "8px 16px",
                background: "var(--accent)",
                color: "white",
                cursor: saving ? "wait" : "pointer",
                fontSize: 13,
                opacity: saving ? 0.65 : 1,
              }}
            >
              {saving ? "修改中…" : "修改密码并重新登录"}
            </button>
          </form>
        </Card>
      </div>
    </div>
  );
}

function PasswordField({
  id,
  label,
  hint,
  value,
  onChange,
  autoComplete,
  autoFocus,
}: {
  id: string;
  label: string;
  hint?: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: "current-password" | "new-password";
  autoFocus?: boolean;
}) {
  return (
    <label htmlFor={id} style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 13 }}>
      <span>
        {label}
        {hint && <span style={{ marginLeft: 8, color: "var(--text-muted)", fontSize: 11 }}>{hint}</span>}
      </span>
      <input
        id={id}
        type="password"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete={autoComplete}
        autoFocus={autoFocus}
        required
        style={fieldStyle}
      />
    </label>
  );
}
