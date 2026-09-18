import { useEffect, useState } from "react";

import {
  clearImapCredential,
  getImapStatus,
  guessImapHost,
  saveImapCredential,
  type ImapStatus,
} from "../../../lib/tauri_imap";

/**
 * IMAP 配置表单。
 *
 * 9/18: 两条"读客户端本地数据"的路都被厂商堵死了 —— Foxmail 7.2 把邮件文件
 * 加密了, 新版 Outlook 既无 COM 也无本地邮件。IMAP 是唯一跟客户端无关的路径。
 *
 * 密码输进来就送进系统凭据库, **组件自己不留**: 保存成功立刻清空 state。
 * 也没有任何"显示密码"的开关 —— 存进去之后连我们自己都读不出来 (Rust 侧
 * 刻意没有读密码的 command)。
 */
export default function ImapSetup({ onConfigured }: { onConfigured?: () => void }) {
  const [status, setStatus] = useState<ImapStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [user, setUser] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("993");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getImapStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  // 邮箱地址填完自动猜服务器, 但**用户改过就不再覆盖** —— 猜测是省事,
  // 不是替用户做主。
  const [hostTouched, setHostTouched] = useState(false);
  useEffect(() => {
    if (hostTouched) return;
    const guess = guessImapHost(user);
    if (guess) setHost(guess);
  }, [user, hostTouched]);

  async function handleSave() {
    setBusy(true);
    setError(null);
    try {
      const next = await saveImapCredential({
        host: host.trim(),
        user: user.trim(),
        password,
        port: Number(port) || 993,
      });
      setStatus(next);
      setPassword(""); // 存完立刻从内存里抹掉
      setOpen(false);
      onConfigured?.();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleClear() {
    setBusy(true);
    setError(null);
    try {
      setStatus(await clearImapCredential());
      setPassword("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const box: React.CSSProperties = {
    margin: "8px 12px",
    padding: "10px 12px",
    border: "1px solid var(--catfish-border)",
    borderRadius: 8,
    background: "var(--catfish-bg)",
    fontSize: 12,
    lineHeight: 1.6,
  };
  const field: React.CSSProperties = { display: "flex", gap: 8, alignItems: "center", marginTop: 6 };
  const label: React.CSSProperties = { width: 96, color: "var(--catfish-muted)" };
  const input: React.CSSProperties = { flex: 1, padding: "3px 6px" };

  if (status?.configured && !open) {
    return (
      <div style={box}>
        <strong>邮箱直连 (IMAP)</strong>
        <div style={{ marginTop: 4 }}>
          {status.user} · {status.host}:{status.port}
          {!status.password_present && (
            <span style={{ color: "var(--status-danger)" }}>
              {" "}· 凭据库里找不到密码了, 请重新填写
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <button type="button" disabled={busy} onClick={() => setOpen(true)}>重新配置</button>
          <button type="button" disabled={busy} onClick={handleClear}>删除</button>
        </div>
        {error && <div style={{ color: "var(--status-danger)", marginTop: 6 }}>{error}</div>}
      </div>
    );
  }

  if (!open) {
    return (
      <div style={box}>
        <strong>邮箱直连 (IMAP)</strong>
        <div style={{ marginTop: 4, color: "var(--catfish-muted)" }}>
          不依赖任何邮件客户端, 直接连邮箱服务器。新版 Outlook 和 Foxmail 读不到邮件时用这个。
        </div>
        <button type="button" style={{ marginTop: 8 }} onClick={() => setOpen(true)}>配置</button>
      </div>
    );
  }

  return (
    <div style={box}>
      <strong>邮箱直连 (IMAP)</strong>
      <div style={field}>
        <span style={label}>邮箱地址</span>
        <input
          style={input}
          value={user}
          autoComplete="username"
          placeholder="you@company.com"
          onChange={(e) => setUser(e.target.value)}
        />
      </div>
      <div style={field}>
        <span style={label}>IMAP 服务器</span>
        <input
          style={input}
          value={host}
          placeholder="imap.company.com"
          onChange={(e) => { setHostTouched(true); setHost(e.target.value); }}
        />
      </div>
      <div style={field}>
        <span style={label}>端口</span>
        <input style={{ ...input, maxWidth: 80 }} value={port} onChange={(e) => setPort(e.target.value)} />
      </div>
      <div style={field}>
        <span style={label}>密码/授权码</span>
        <input
          style={input}
          type="password"
          value={password}
          autoComplete="current-password"
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>
      <div style={{ marginTop: 6, color: "var(--catfish-muted)" }}>
        企业邮箱通常要用「授权码」而不是登录密码 —— 在邮箱网页版的设置里生成。
        密码保存在本机系统凭据库, 不会上传, 存进去之后鲶鱼自己也读不出来给界面。
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <button type="button" disabled={busy || !user || !host || !password} onClick={handleSave}>
          {busy ? "正在连接…" : "保存并验证"}
        </button>
        <button type="button" disabled={busy} onClick={() => { setOpen(false); setPassword(""); setError(null); }}>
          取消
        </button>
      </div>
      {error && <div style={{ color: "var(--status-danger)", marginTop: 6 }}>{error}</div>}
    </div>
  );
}
