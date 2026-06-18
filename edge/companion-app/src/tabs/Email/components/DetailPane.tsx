/** EmailTab DetailPane — 抽自 EmailTab.tsx (5/20 拆分).
 *
 * 邮件详情区: 头部 (subject/from/to/cc/date/attachments) + 正文 + 行动按钮
 * (让小鲶处理 / 回复 → compose panel / 删除 两步确认 / 标记).
 *
 * Compose panel state machine 内嵌 (drafting/composing/sending). 5/18
 * BL-EMAIL-COMPOSE-SEND 红线: AI 不能绕过 panel 直发 send.
 */

import { useEffect, useState } from "react";

import {
  emailCreateDraft,
  emailDeleteMessage,
  emailSendMessage,
  emailPhishingGet,                 // P3.3.58 段 2C (6/12 鸿波)
  emailPoliticalScanNow,            // P3.3.53.2 (6/13 鸿波)
  type EmailDigestItem,
  type PhishingScanResult,          // P3.3.58 段 2C
  type PoliticalScanResult,         // P3.3.53.2
} from "../../../lib/tauri";
import { _extractSenderName, _replyAddress } from "./helpers";

interface FullMessage extends EmailDigestItem {
  recipients?: string[];
  cc?: string[];
  attachments?: Array<{ filename: string; size_bytes: number; content_type: string }>;
  /** P3.5.31 (6/17 鸿波 catch): rust email_read_message 返 JSON 真**已含 body_html**
   *  (catfish-email Python adapter 真**MIME extract HTML part 填**, base.py:114).
   *  老 type 真**0 declare** → DetailPane 真**只 access body_text** (strip 后 plain) →
   *  和 Apple Mail rendered HTML 真**不一致**. 加 field 让 DetailPane 走 iframe render.
   *  read 场景填; list 场景空字符串 (节省 IPC, base.py:115 注释).
   */
  body_html?: string;
}

/** P3.3.57 (6/12 鸿波): 大群发邮件 header 收件人/抄送默认折叠.
 *  默认显前 N 个 + "... 共 X 人 [展开]", 点击切换 [折叠].
 *  防 81 收件人 + 30 抄送一次铺开把邮件正文挤出 viewport.
 */
function CollapsibleAddresses({ addrs, previewN = 3 }: { addrs: string[]; previewN?: number }) {
  const [expanded, setExpanded] = useState(false);
  if (addrs.length <= previewN) {
    return <>{addrs.join(", ")}</>;
  }
  return (
    <>
      {expanded ? addrs.join(", ") : addrs.slice(0, previewN).join(", ")}
      {!expanded && <span style={{ color: "var(--catfish-text-muted)" }}>... 共 {addrs.length} 人</span>}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        style={{
          marginLeft: 6,
          background: "transparent",
          border: "1px solid var(--catfish-border)",
          borderRadius: 3,
          padding: "0 6px",
          fontSize: 10,
          color: "var(--catfish-cyan)",
          cursor: "pointer",
        }}
      >
        {expanded ? "折叠" : "展开"}
      </button>
    </>
  );
}


function DetailPane({
  msg,
  onAskCatfish,
  onDeleted,
}: {
  msg: FullMessage;
  onAskCatfish: (m: FullMessage) => void;
  onDeleted: () => void;  // 5/18 BL-EMAIL-DELETE: 删除成功 → 父组件移除 item
}) {
  const [drafting, setDrafting] = useState(false);
  const [draftResult, setDraftResult] = useState<string | null>(null);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  /** 5/18 BL-EMAIL-DELETE: 两步确认 — 第一次点 "🗑 删除" 切到 "再次点击确认" 状态,
   *  第二次点才真删. 3s 后自动取消恢复初态. 比 window.confirm 在 Tauri WebView
   *  下可靠 (有些场景 confirm 被吞), 也比系统 dialog 打扰. */
  const [confirmPending, setConfirmPending] = useState(false);

  // 取消 confirm 状态的 timer
  useEffect(() => {
    if (!confirmPending) return;
    const t = window.setTimeout(() => setConfirmPending(false), 3000);
    return () => window.clearTimeout(t);
  }, [confirmPending]);

  // 选不同邮件时清 state, 防上封邮件的 error / confirm 残留
  useEffect(() => {
    setConfirmPending(false);
    setDeleteError(null);
  }, [msg.id]);

  // P3.3.58 段 2C (6/12 鸿波): 拉单封邮件的钓鱼扫描结果, 显红条
  const [phishing, setPhishing] = useState<PhishingScanResult | null>(null);
  useEffect(() => {
    let cancelled = false;
    setPhishing(null);
    (async () => {
      try {
        const map = await emailPhishingGet([msg.id]);
        if (!cancelled) setPhishing(map[msg.id] ?? null);
      } catch {
        // 拉失败静默 — 没扫过的 detail pane 不显红条 OK
      }
    })();
    return () => { cancelled = true; };
  }, [msg.id]);

  // P3.3.53.2 (6/13 鸿波): 政治敏感扫描 — detail pane 打开时主动扫.
  // 默认 yaml political.enabled=false → 返 engineEnabled=false, 不显红条.
  // 集团下发词库后, 本机扫 + LRU 缓存防重扫, 0 字节数据出端.
  const [political, setPolitical] = useState<PoliticalScanResult | null>(null);
  useEffect(() => {
    let cancelled = false;
    setPolitical(null);
    (async () => {
      try {
        const r = await emailPoliticalScanNow(
          msg.id,
          msg.subject || "",
          msg.sender || "",
          msg.body_text || "",
        );
        if (!cancelled) setPolitical(r);
      } catch {
        // 扫失败静默 — 不挂红条, 跟 phishing 一致
      }
    })();
    return () => { cancelled = true; };
  }, [msg.id, msg.subject, msg.sender, msg.body_text]);

  const handleDelete = async () => {
    if (!confirmPending) {
      // 第一次点 — 进 "再次确认" 状态, 不调 CLI
      setConfirmPending(true);
      setDeleteError(null);
      return;
    }
    // 第二次点 — 真删
    setConfirmPending(false);
    setDeleting(true);
    setDeleteError(null);
    console.log("[BL-EMAIL-DELETE] 调用 emailDeleteMessage", msg.id);
    try {
      const result = await emailDeleteMessage(msg.id);
      console.log("[BL-EMAIL-DELETE] 删除成功", result);
      onDeleted();
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : String(e);
      console.error("[BL-EMAIL-DELETE] 删除失败:", errMsg);
      setDeleteError(errMsg);
    } finally {
      setDeleting(false);
    }
  };

  // 5/18 BL-EMAIL-COMPOSE-SEND: compose panel state — 点 "起草回复" 后展开,
  // 显示可编辑 to/cc/subject/body, 员工 review/编辑 → 点 "✉ 发送" 真发.
  // 红线: AI 不能绕过这个 panel 直接 send, 必须人工在 panel 里点按钮.
  const [composing, setComposing] = useState(false);
  const [composeTo, setComposeTo] = useState("");
  const [composeCc, setComposeCc] = useState("");
  const [composeSubject, setComposeSubject] = useState("");
  const [composeBody, setComposeBody] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [sendResult, setSendResult] = useState<string | null>(null);
  const [sendConfirmPending, setSendConfirmPending] = useState(false);

  // 3s 自动取消 send confirm
  useEffect(() => {
    if (!sendConfirmPending) return;
    const t = window.setTimeout(() => setSendConfirmPending(false), 3000);
    return () => window.clearTimeout(t);
  }, [sendConfirmPending]);

  // 选别的邮件时清 compose 状态
  useEffect(() => {
    setComposing(false);
    setSendError(null);
    setSendResult(null);
    setSendConfirmPending(false);
  }, [msg.id]);

  // P3.5.38.1 (6/18 鸿波 catch '邮件链接还是无效'):
  //   audit (markdown.tsx:211 BL-ARCH2 fix1 5/10): Tauri webview 默认吞 <a target="_blank">,
  //   必须程序化调 shell.open 才能真在系统浏览器开. P3.5.38 改 sandbox + base target 没用
  //   因为 popup 本身就被 Tauri webview 吞.
  //   修: iframe srcDoc 注入 click 拦截脚本 → postMessage URL 给 parent → parent
  //   调 @tauri-apps/plugin-shell.open. iframe sandbox 加 allow-scripts (脚本能跑),
  //   不加 allow-same-origin (邮件 script 是 opaque origin, 拿不到 cookies/parent).
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      const data = e.data;
      if (
        data &&
        typeof data === "object" &&
        data.type === "catfish-email-link-click" &&
        typeof data.url === "string" &&
        // 防注入: 只接受 http/https/mailto, 砍 javascript: / data: 等
        /^(https?|mailto):/i.test(data.url)
      ) {
        void (async () => {
          try {
            const { open } = await import("@tauri-apps/plugin-shell");
            await open(data.url);
          } catch (err) {
            console.warn("[EmailDetail] shell.open 失败:", err);
          }
        })();
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  /** 5/18 BL-EMAIL-COMPOSE-SEND: 点 "起草回复" 不再立即建 draft, 而是开
   *  compose panel 让员工编辑. 这样:
   *    - 取消不留 orphan draft
   *    - 编辑后再 send 不会跟 Mail.app 那侧的草稿不一致 */
  const handleOpenCompose = () => {
    const replyTo = _replyAddress(msg.sender);
    const subj = msg.subject?.startsWith("Re:") ? msg.subject : `Re: ${msg.subject || ""}`;
    const quoted = (msg.body_text || "")
      .split("\n")
      .map((l) => `> ${l}`)
      .join("\n");
    const body = `\n\n\n${"-".repeat(20)} 原邮件 ${"-".repeat(20)}\n` +
      `发件人: ${msg.sender}\n` +
      `时间: ${msg.date}\n` +
      `主题: ${msg.subject}\n\n` +
      quoted;
    setComposeTo(replyTo);
    setComposeCc("");
    setComposeSubject(subj);
    setComposeBody(body);
    setSendError(null);
    setSendResult(null);
    setSendConfirmPending(false);
    setComposing(true);
  };

  /** 只保存到 Drafts, 不发. 等价于老 handleDraftReply 行为 (但用 panel 的内容). */
  const handleSaveDraft = async () => {
    setDrafting(true);
    setDraftResult(null);
    setDraftError(null);
    try {
      const resultJson = await emailCreateDraft({
        to: composeTo,
        cc: composeCc || undefined,
        subject: composeSubject,
        body: composeBody,
        inReplyTo: msg.id,
        account: msg.account,
      });
      const parsed = JSON.parse(resultJson);
      setDraftResult(parsed?.draft_id ?? "ok");
      setComposing(false);  // 保存成功关 panel
    } catch (e) {
      setDraftError(e instanceof Error ? e.message : String(e));
    } finally {
      setDrafting(false);
    }
  };

  /** 真发送: 两步 confirm, 第一次切 "再次点击确认", 第二次真发.
   *  实现: 先 emailCreateDraft 拿 id, 再 emailSendMessage(id). */
  const handleSendNow = async () => {
    if (!sendConfirmPending) {
      setSendConfirmPending(true);
      setSendError(null);
      return;
    }
    setSendConfirmPending(false);
    setSending(true);
    setSendError(null);
    setSendResult(null);
    console.log("[BL-EMAIL-COMPOSE-SEND] 起草+发送", { to: composeTo, subject: composeSubject });
    try {
      const draftJson = await emailCreateDraft({
        to: composeTo,
        cc: composeCc || undefined,
        subject: composeSubject,
        body: composeBody,
        inReplyTo: msg.id,
        account: msg.account,
      });
      const draftParsed = JSON.parse(draftJson);
      const draftId = draftParsed?.draft_id;
      if (!draftId) {
        throw new Error("起草返回没 draft_id, 无法发送");
      }
      console.log("[BL-EMAIL-COMPOSE-SEND] draft 已建, 现在 send", draftId);
      const sendJson = await emailSendMessage(draftId);
      console.log("[BL-EMAIL-COMPOSE-SEND] 发送成功", sendJson);
      setSendResult("✓ 已发送");
      // 2s 后关 panel
      setTimeout(() => {
        setComposing(false);
        setSendResult(null);
      }, 2000);
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : String(e);
      console.error("[BL-EMAIL-COMPOSE-SEND] 发送失败:", errMsg);
      setSendError(errMsg);
    } finally {
      setSending(false);
    }
  };
  return (
    <>
      {/* 详情 header */}
      <div
        style={{
          padding: "var(--space-4)",
          borderBottom: "1px solid var(--catfish-border)",
          background: "var(--catfish-bg-elevated)",
        }}
      >
        {/* P3.3.58 段 2C (6/12 鸿波): 钓鱼红条 — high/medium 触发规则或 LLM 标 phishing/suspicious */}
        {phishing &&
          (phishing.highestSeverity === "high" || phishing.highestSeverity === "medium") && (
            <div
              style={{
                marginBottom: 12,
                padding: "10px 12px",
                background: phishing.highestSeverity === "high"
                  ? "rgba(220,38,38,0.08)"
                  : "rgba(251,146,60,0.08)",
                border: phishing.highestSeverity === "high"
                  ? "1px solid rgba(220,38,38,0.4)"
                  : "1px solid rgba(251,146,60,0.4)",
                borderRadius: 4,
                fontSize: 12,
              }}
            >
              <div
                style={{
                  fontWeight: 700,
                  color: phishing.highestSeverity === "high"
                    ? "rgb(185,28,28)"
                    : "rgb(194,65,12)",
                  marginBottom: 6,
                }}
              >
                {phishing.highestSeverity === "high"
                  ? "⚠ catfish 标记此邮件有钓鱼嫌疑"
                  : "⚠ catfish 标记此邮件可疑"}
              </div>
              {phishing.flags.length > 0 && (
                <div style={{ marginBottom: 4, color: "var(--catfish-text)" }}>
                  触发规则 ({phishing.flags.length}):{" "}
                  {phishing.flags.slice(0, 4).map((f) => f.ruleId).join(", ")}
                  {phishing.flags.length > 4 && ` 等 ${phishing.flags.length} 条`}
                </div>
              )}
              {phishing.llmVerdict && (
                <div style={{ marginBottom: 4, color: "var(--catfish-text)" }}>
                  LLM 复审: <strong>{phishing.llmVerdict}</strong>
                  {phishing.llmReason ? ` — ${phishing.llmReason}` : ""}
                </div>
              )}
              <div
                style={{
                  marginTop: 4,
                  fontWeight: 600,
                  color: phishing.highestSeverity === "high"
                    ? "rgb(185,28,28)"
                    : "rgb(194,65,12)",
                }}
              >
                审慎打开链接 / 附件 / 回复, 如不确定请咨询信安部门.
              </div>
            </div>
          )}
        {/* P3.3.53.2 (6/13 鸿波): 政治敏感红条 — 引擎开 + high/medium 触发 */}
        {political && political.engineEnabled &&
          (political.highestSeverity === "high" || political.highestSeverity === "medium") && (
            <div
              style={{
                marginBottom: 12,
                padding: "10px 12px",
                background: political.highestSeverity === "high"
                  ? "rgba(220,38,38,0.10)"
                  : "rgba(234,179,8,0.10)",
                border: political.highestSeverity === "high"
                  ? "1.5px solid rgba(220,38,38,0.5)"
                  : "1px solid rgba(234,179,8,0.5)",
                borderRadius: 4,
                fontSize: 12,
              }}
            >
              <div
                style={{
                  fontWeight: 700,
                  color: political.highestSeverity === "high"
                    ? "rgb(185,28,28)"
                    : "rgb(133,77,14)",
                  marginBottom: 6,
                }}
              >
                {political.highestSeverity === "high"
                  ? "⚠ catfish 标记此邮件含合规红线提醒"
                  : "⚠ catfish 标记此邮件需要复核"}
              </div>
              {political.flags.length > 0 && (
                <div style={{ marginBottom: 4, color: "var(--catfish-text)" }}>
                  触发规则 ({political.flags.length}):{" "}
                  {political.flags.slice(0, 4).map((f) => f.ruleId).join(", ")}
                  {political.flags.length > 4 && ` 等 ${political.flags.length} 条`}
                </div>
              )}
              {political.llmVerdict && (
                <div style={{ marginBottom: 4, color: "var(--catfish-text)" }}>
                  LLM 复审: <strong>{political.llmVerdict}</strong>
                  {political.llmReason ? ` — ${political.llmReason}` : ""}
                </div>
              )}
              <div
                style={{
                  marginTop: 4,
                  fontWeight: 600,
                  color: political.highestSeverity === "high"
                    ? "rgb(185,28,28)"
                    : "rgb(133,77,14)",
                }}
              >
                如不确定请咨询信安部门 / 党办 / 法务. catfish 仅本机识别提示,
                不上行邮件原文.
              </div>
            </div>
          )}
        <h2
          style={{
            margin: 0,
            fontSize: 16,
            fontWeight: 600,
            color: "var(--catfish-text)",
            lineHeight: 1.4,
          }}
        >
          {msg.subject || "(无主题)"}
        </h2>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "auto 1fr",
            gap: "4px 12px",
            fontSize: 12,
            color: "var(--catfish-text-muted)",
            marginTop: 12,
          }}
        >
          <div>发件人</div>
          <div style={{ color: "var(--catfish-text)" }}>{msg.sender}</div>
          {msg.recipients && msg.recipients.length > 0 && (
            <>
              <div>收件人</div>
              <div><CollapsibleAddresses addrs={msg.recipients} /></div>
            </>
          )}
          {msg.cc && msg.cc.length > 0 && (
            <>
              <div>抄送</div>
              <div><CollapsibleAddresses addrs={msg.cc} /></div>
            </>
          )}
          <div>时间</div>
          <div>{msg.date}</div>
          <div>账号</div>
          <div>{msg.account}</div>
          {msg.has_attachments && msg.attachments && msg.attachments.length > 0 && (
            <>
              <div>附件</div>
              <div>
                {msg.attachments.map((a, i) => (
                  <span key={i} style={{ marginRight: 8 }}>
                    📎 {a.filename} ({Math.round(a.size_bytes / 1024)} KB)
                  </span>
                ))}
              </div>
            </>
          )}
        </div>
        {/* 行动按钮 */}
        <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap", alignItems: "center" }}>
          <button
            type="button"
            onClick={() => onAskCatfish(msg)}
            style={{
              background: "var(--catfish-cyan)",
              color: "#fff",
              border: "none",
              borderRadius: 4,
              padding: "8px 16px",
              fontSize: 13,
              fontWeight: 500,
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            💬 让小鲶处理这封
          </button>
          <button
            type="button"
            onClick={handleOpenCompose}
            disabled={drafting || composing}
            style={{
              background: "var(--catfish-bg)",
              color: "var(--catfish-text)",
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              padding: "8px 16px",
              fontSize: 13,
              cursor: composing ? "default" : "pointer",
              fontFamily: "inherit",
            }}
            title="打开 compose 面板, 编辑回复内容 + 选择保存草稿或发送 (人工 confirm 才发)"
          >
            {composing ? "📝 编辑中…" : "✏️ 回复"}
          </button>
          {/* 5/18 BL-EMAIL-DELETE: 两步点击确认 (window.confirm 在 Tauri 不可靠).
              第一次点 → "🗑 再次点击确认" (3s 内有效), 第二次才真删. */}
          <button
            type="button"
            onClick={() => void handleDelete()}
            disabled={deleting}
            style={{
              background: confirmPending ? "rgba(220, 80, 60, 0.15)" : "var(--catfish-bg)",
              color: "rgb(220, 80, 60)",
              border: `1px solid ${confirmPending ? "rgb(220, 80, 60)" : "rgba(220, 80, 60, 0.4)"}`,
              borderRadius: 4,
              padding: "8px 14px",
              fontSize: 13,
              fontWeight: confirmPending ? 600 : 400,
              cursor: deleting ? "wait" : "pointer",
              fontFamily: "inherit",
              marginLeft: "auto",
            }}
            title={
              confirmPending
                ? "再次点击确认删除 (3s 内有效, 否则自动取消)"
                : "把这封邮件移到客户端 Trash 文件夹 (软删, 30 天内可恢复)"
            }
          >
            {deleting
              ? "删除中…"
              : confirmPending
                ? "🗑 再次点击确认 (3s)"
                : "🗑 删除"}
          </button>
          {deleteError && (
            <div
              style={{
                width: "100%",
                marginTop: 8,
                padding: "8px 12px",
                background: "rgba(220, 80, 60, 0.1)",
                border: "1px solid rgba(220, 80, 60, 0.3)",
                borderRadius: 4,
                fontSize: 12,
                color: "rgb(220, 80, 60)",
                lineHeight: 1.5,
              }}
            >
              <strong>✗ 删除失败</strong>
              <br />
              {deleteError}
              {deleteError.includes("不支持") && (
                <>
                  <br />
                  <span style={{ opacity: 0.85 }}>
                    (Foxmail Mac 没暴露删除 IPC, 我们试过直接动 sqlite 但 IMAP
                    同步会把邮件从 server 拉回 INBOX 让操作无效. 请打开 Foxmail
                    客户端自己删 — Foxmail 会通知 server, 然后下次 Companion
                    刷新就看不到这封了.)
                  </span>
                </>
              )}
            </div>
          )}
          {draftResult && (
            <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
              ✓ 草稿已落 Mail.app Drafts, Mail.app 已切前台 + 草稿窗口弹出 —
              在那审改后按 ⌘+Shift+D 发送
            </span>
          )}
          {draftError && (
            <span style={{ fontSize: 11, color: "rgb(185, 28, 28)" }}>
              起草失败: {draftError}
            </span>
          )}
        </div>
      </div>

      {/* 5/18 BL-EMAIL-COMPOSE-SEND: compose panel - composing=true 时取代正文区显. */}
      {composing ? (
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "var(--space-4)",
            fontSize: 13,
            background: "var(--catfish-bg)",
            display: "flex",
            flexDirection: "column",
            gap: 10,
          }}
        >
          {/* 红线提示横幅 */}
          <div
            style={{
              padding: "8px 12px",
              background: "rgba(34, 197, 94, 0.1)",
              border: "1px solid rgba(34, 197, 94, 0.3)",
              borderRadius: 4,
              fontSize: 11,
              color: "rgb(21, 128, 61)",
              lineHeight: 1.5,
            }}
          >
            ✏️ <strong>Compose 模式</strong> — 编辑下面内容, "💾 仅保存草稿"
            落 Mail.app Drafts; "✉ 发送" 真发出去 (两步 confirm).
            红线: AI 永远不能绕过这步直接 send, 必须你人工点按钮.
          </div>

          {/* to */}
          <label style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
            <span style={{ width: 50, color: "var(--catfish-text-muted)", flex: "0 0 auto" }}>收件人</span>
            <input
              type="text"
              value={composeTo}
              onChange={(e) => setComposeTo(e.target.value)}
              placeholder="alice@x.com, bob@y.com"
              style={{
                flex: 1,
                background: "var(--catfish-bg-elevated)",
                color: "var(--catfish-text)",
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                padding: "6px 10px",
                fontSize: 13,
                fontFamily: "inherit",
              }}
            />
          </label>

          {/* cc */}
          <label style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
            <span style={{ width: 50, color: "var(--catfish-text-muted)", flex: "0 0 auto" }}>抄送</span>
            <input
              type="text"
              value={composeCc}
              onChange={(e) => setComposeCc(e.target.value)}
              placeholder="可选, 多人逗号分隔"
              style={{
                flex: 1,
                background: "var(--catfish-bg-elevated)",
                color: "var(--catfish-text)",
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                padding: "6px 10px",
                fontSize: 13,
                fontFamily: "inherit",
              }}
            />
          </label>

          {/* subject */}
          <label style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
            <span style={{ width: 50, color: "var(--catfish-text-muted)", flex: "0 0 auto" }}>主题</span>
            <input
              type="text"
              value={composeSubject}
              onChange={(e) => setComposeSubject(e.target.value)}
              style={{
                flex: 1,
                background: "var(--catfish-bg-elevated)",
                color: "var(--catfish-text)",
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                padding: "6px 10px",
                fontSize: 13,
                fontFamily: "inherit",
              }}
            />
          </label>

          {/* body */}
          <textarea
            value={composeBody}
            onChange={(e) => setComposeBody(e.target.value)}
            placeholder="正文..."
            style={{
              flex: 1,
              minHeight: 200,
              background: "var(--catfish-bg-elevated)",
              color: "var(--catfish-text)",
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              padding: "8px 12px",
              fontSize: 13,
              lineHeight: 1.6,
              fontFamily: "inherit",
              resize: "vertical",
            }}
          />

          {/* 错误 / 成功提示 */}
          {sendError && (
            <div
              style={{
                padding: "8px 12px",
                background: "rgba(220, 80, 60, 0.1)",
                border: "1px solid rgba(220, 80, 60, 0.3)",
                borderRadius: 4,
                fontSize: 12,
                color: "rgb(220, 80, 60)",
                lineHeight: 1.5,
              }}
            >
              <strong>✗ 发送失败</strong>
              <br />
              {sendError}
            </div>
          )}
          {sendResult && (
            <div
              style={{
                padding: "8px 12px",
                background: "rgba(34, 197, 94, 0.1)",
                border: "1px solid rgba(34, 197, 94, 0.3)",
                borderRadius: 4,
                fontSize: 12,
                color: "rgb(21, 128, 61)",
              }}
            >
              {sendResult} (2 秒后关闭)
            </div>
          )}

          {/* 行动按钮区 */}
          <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
            <button
              type="button"
              onClick={() => {
                setComposing(false);
                setSendConfirmPending(false);
                setSendError(null);
              }}
              disabled={sending}
              style={{
                background: "transparent",
                color: "var(--catfish-text-muted)",
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                padding: "8px 14px",
                fontSize: 13,
                cursor: sending ? "wait" : "pointer",
                fontFamily: "inherit",
              }}
            >
              × 取消
            </button>
            <button
              type="button"
              onClick={() => void handleSaveDraft()}
              disabled={sending || drafting}
              style={{
                background: "var(--catfish-bg)",
                color: "var(--catfish-text)",
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                padding: "8px 14px",
                fontSize: 13,
                cursor: drafting ? "wait" : "pointer",
                fontFamily: "inherit",
              }}
              title="保存到 Mail.app Drafts, 不发送. 等会儿去 Mail.app 改完自己发."
            >
              {drafting ? "保存中…" : "💾 仅保存草稿"}
            </button>
            <button
              type="button"
              onClick={() => void handleSendNow()}
              disabled={sending || !composeTo.trim()}
              style={{
                background: sendConfirmPending ? "rgba(34, 197, 94, 0.15)" : "var(--catfish-cyan)",
                color: sendConfirmPending ? "rgb(21, 128, 61)" : "#fff",
                border: sendConfirmPending
                  ? "1px solid rgb(21, 128, 61)"
                  : "1px solid var(--catfish-cyan)",
                borderRadius: 4,
                padding: "8px 14px",
                fontSize: 13,
                fontWeight: sendConfirmPending ? 600 : 500,
                cursor: sending ? "wait" : (!composeTo.trim() ? "not-allowed" : "pointer"),
                fontFamily: "inherit",
                marginLeft: "auto",
              }}
              title={
                sendConfirmPending
                  ? "再次点击确认发送 (3s 内有效)"
                  : "起草 + 发送邮件. 红线: 必须人工点这个按钮."
              }
            >
              {sending
                ? "发送中…"
                : sendConfirmPending
                  ? "✉ 再次点击确认 (3s)"
                  : "✉ 发送"}
            </button>
          </div>
        </div>
      ) : msg.body_html ? (
        /* P3.5.31 (6/17): HTML 邮件 iframe srcdoc render 跟 Apple Mail 一致.
           P3.5.38 (6/18, revert): 单加 sandbox allow-popups + base target 无效,
             因为 Tauri webview 默认吞 <a target="_blank"> (markdown.tsx:211 BL-ARCH2
             fix1 5/10 鸿波反馈实证).
           P3.5.38.1 (6/18 鸿波 catch '还是无效'): 正解 — iframe 内注入 click 拦截
             脚本, 拦 a tag click → postMessage URL → parent useEffect 收 → 调
             @tauri-apps/plugin-shell.open 跳系统浏览器.
           sandbox 加 allow-scripts (注入脚本能跑) — 不加 allow-same-origin (邮件 script
             opaque origin, 拿不到 cookies / parent), 不加 allow-forms / allow-top-navigation.
             邮件原 script 也能跑, 但 opaque origin 风险可控 (跟 Gmail / Outlook 同 setup).
           parent useEffect (上面 P3.5.38.1) 白名单 http/https/mailto, 砍 javascript:/data: 防注入.
           bg 白: 邮件默认 white, override Companion dark theme. */
        <iframe
          title="邮件正文"
          srcDoc={buildEmailSrcDoc(msg.body_html)}
          sandbox="allow-scripts"
          style={{
            flex: 1,
            width: "100%",
            border: "none",
            background: "white",
          }}
        />
      ) : (
        /* 正文 (非 compose 时, body_html 空 真**fallback plain text**)
           触发场景: 纯文本邮件 (CTFF 通知 / 系统通知) — body_html 空, body_text 唯一来源 */
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "var(--space-4)",
            fontSize: 13,
            lineHeight: 1.6,
            color: "var(--catfish-text)",
            whiteSpace: "pre-wrap",
            background: "var(--catfish-bg)",
          }}
        >
          {msg.body_text || "(无正文)"}
        </div>
      )}
    </>
  );
}

/** ─── 工具函数 ───────────────────────────────────────── */

/** P3.5.38.1 (6/18 鸿波 catch '邮件链接还是无效'): 构造邮件 iframe srcDoc.
 *
 * 注入 click 拦截脚本 — 拦 a tag click + preventDefault + postMessage URL 给 parent.
 * parent 在 DetailPane useEffect 收到后调 @tauri-apps/plugin-shell.open 跳系统浏览器.
 *
 * 跟 markdown.tsx:213 a renderer 同模式 (BL-ARCH2 fix1 5/10 鸿波反馈 — Tauri webview
 * 默认吞 target=_blank, 必须 shell.open). 这里 iframe sandbox 内不能直接 import,
 * 走 postMessage 桥.
 *
 * <base target="_blank"> 保留 (邮件 HTML 没显式 target 时也能在 popup 试一次,
 * 跟 sandbox allow-scripts 注入脚本组合冗余兜底).
 *
 * script 用 capture phase 拦截 (true 第三参), 因为 e.target 可能是 a 内部 <span>/<img>,
 * 需向上找到 a 元素拿 href.
 */
function buildEmailSrcDoc(bodyHtml: string): string {
  const interceptScript = `
<script>
(function() {
  document.addEventListener('click', function(e) {
    var t = e.target;
    while (t && t.tagName !== 'A') t = t.parentElement;
    if (t && t.href) {
      e.preventDefault();
      e.stopPropagation();
      try {
        window.parent.postMessage({
          type: 'catfish-email-link-click',
          url: t.href
        }, '*');
      } catch (err) { /* silent */ }
    }
  }, true);
})();
</script>`;
  return `<base target="_blank">${bodyHtml}${interceptScript}`;
}

export default DetailPane;
export type { FullMessage };
