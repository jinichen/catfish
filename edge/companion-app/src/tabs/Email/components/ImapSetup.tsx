import { useEffect, useState } from "react";

import {
  RETENTION_LABELS,
  RETENTION_ORDER,
  type RetentionPolicy,
  normalizeRetention,
  clearImapCredential,
  getImapStatus,
  guessImapHost,
  guessSmtpHost,
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
  const [retention, setRetention] = useState<RetentionPolicy>("never");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 9/18: 发信是 SMTP, 跟收信完全是两套。绝大多数企业邮箱 smtp.<域名>:465
  // 就对, 所以默认折叠、自动填好, 员工不用管; 但猜错时**必须有地方改**,
  // 否则回复永远发不出去而他无从下手。
  const [smtpOpen, setSmtpOpen] = useState(false);
  const [smtpHost, setSmtpHost] = useState("");
  const [smtpHostTouched, setSmtpHostTouched] = useState(false);
  const [smtpPort, setSmtpPort] = useState("465");

  useEffect(() => {
    getImapStatus()
      .then((next) => {
        setStatus(next);
        // 表单回显已存的策略。不回显的话, 员工点「重新配置」只改了个端口,
        // 保存时会把 retention 一起写成表单里的默认值 never —— **静默地把
        // 他之前选的策略关掉**, 而界面上什么提示都没有。
        setRetention(normalizeRetention(next.retention));
      })
      .catch(() => setStatus(null));
  }, []);

  // 邮箱地址填完自动猜服务器, 但**用户改过就不再覆盖** —— 猜测是省事,
  // 不是替用户做主。
  const [hostTouched, setHostTouched] = useState(false);
  useEffect(() => {
    if (hostTouched) return;
    const guess = guessImapHost(user);
    if (guess) setHost(guess);
  }, [user, hostTouched]);

  // 发信服务器跟着收信服务器走 —— 同样是"改过就不再覆盖"。
  useEffect(() => {
    if (smtpHostTouched) return;
    const guess = guessSmtpHost(host);
    if (guess) setSmtpHost(guess);
  }, [host, smtpHostTouched]);

  async function handleSave() {
    setBusy(true);
    setError(null);
    try {
      const next = await saveImapCredential({
        host: host.trim(),
        user: user.trim(),
        password,
        port: Number(port) || 993,
        // 跟猜出来的一样就不存 —— 存下来的话等于把"当时猜的那个值"钉死,
        // 以后我们改了猜法, 这台机器还用着旧的。只存员工真改过的。
        smtpHost: smtpHost.trim() === guessSmtpHost(host.trim()) ? "" : smtpHost.trim(),
        smtpPort: Number(smtpPort) === 465 ? 0 : Number(smtpPort) || 0,
        retention,
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
          {status.user} · 收 {status.host}:{status.port} · 发{" "}
          {/* 如实显示发信走哪台 —— 没配过就显示我们会猜成什么, 别让员工
              到发失败了才去猜我们猜了什么。 */}
          {(status.smtp_host || guessSmtpHost(status.host))}:{status.smtp_port || 465}
          {!status.password_present && (
            <span style={{ color: "var(--status-danger)" }}>
              {" "}· 凭据库里找不到密码了, 请重新填写
            </span>
          )}
        </div>
        {/* 保留策略**一直显示**, 包括 never。
            只在"会删"的时候才显示, 等于让员工自己去记得他选没选过 ——
            而这是这套配置里唯一不可逆的一项, 它的当前值应该是随时看得见的。 */}
        <div style={{ marginTop: 4, color: "var(--catfish-muted)" }}>
          归档后: {RETENTION_LABELS[normalizeRetention(status.retention)]}
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
      {/* 归档保留策略。
          **放在主表单里不折叠** —— 它决定要不要删员工服务器上的邮件, 不可逆。
          折起来等于让人在不知情的状态下用默认值, 而"我从来没见过这个选项"
          正是事后最难解释的一种。发信那块折叠是因为猜错了还能改; 这里删错了
          没有改的余地。 */}
      <div style={{ ...field, alignItems: "flex-start" }}>
        <span style={label}>归档后</span>
        <div style={{ flex: 1 }}>
          <select
            style={{ ...input, width: "100%" }}
            value={retention}
            onChange={(e) => setRetention(normalizeRetention(e.target.value))}
          >
            {RETENTION_ORDER.map((key) => (
              <option key={key} value={key}>{RETENTION_LABELS[key]}</option>
            ))}
          </select>
          {retention !== "never" && (
            <div style={{ marginTop: 4, color: "var(--status-danger)", fontSize: 11 }}>
              ⚠ 服务器上那份会被删掉, **不可恢复**。只有原文已落到本地档案
              并重新读出来核对过的邮件才会删。
            </div>
          )}
        </div>
      </div>

      {/* 发信 —— 默认折叠。绝大多数情况自动填的就对, 但猜错时必须能改,
          否则员工的回复永远发不出去而他无从下手。 */}
      <div style={{ marginTop: 8 }}>
        <button
          type="button"
          onClick={() => setSmtpOpen((v) => !v)}
          style={{
            border: "none", background: "transparent", padding: 0,
            color: "var(--catfish-muted)", fontSize: 12, fontFamily: "inherit",
            cursor: "pointer", textDecoration: "underline",
          }}
        >
          {smtpOpen ? "收起发信设置" : "发信设置（一般不用改）"}
        </button>
      </div>
      {smtpOpen && (
        <>
          <div style={field}>
            <span style={label}>发信服务器</span>
            <input
              style={input}
              value={smtpHost}
              placeholder="smtp.company.com"
              onChange={(e) => { setSmtpHostTouched(true); setSmtpHost(e.target.value); }}
            />
          </div>
          <div style={field}>
            <span style={label}>发信端口</span>
            <input
              style={{ ...input, maxWidth: 80 }}
              value={smtpPort}
              onChange={(e) => setSmtpPort(e.target.value)}
            />
          </div>
          <div style={{ marginTop: 4, color: "var(--catfish-muted)", fontSize: 11 }}>
            465 是加密连接, 推荐。587 也行, 但只在服务器支持加密升级时才发 ——
            不支持就报错, 绝不明文把密码和正文发出去。
          </div>
        </>
      )}
      <div style={{ marginTop: 6, color: "var(--catfish-muted)" }}>
        企业邮箱通常要用「授权码」而不是登录密码 —— 在邮箱网页版的设置里生成。
        密码保存在本机系统凭据库, 不会上传, 存进去之后鲶鱼自己也读不出来给界面。
        <br />
        保存时会**真连一次收信服务器**验证。发信是另一套服务器, 这一步验不到,
        第一次回复邮件时才知道通不通。
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
