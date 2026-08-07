/** EmailTab DetailPane — 抽自 EmailTab.tsx (5/20 拆分).
 *
 * 邮件详情区: 头部 (subject/from/to/cc/date/attachments) + 正文 + 行动按钮
 * (让小鲶处理 / 回复 → compose panel / 删除 两步确认 / 标记).
 *
 * Compose panel state machine 内嵌 (drafting/composing/sending). 5/18
 * BL-EMAIL-COMPOSE-SEND 红线: AI 不能绕过 panel 直发 send.
 */

import { useEffect, useMemo, useState } from "react";

import {
  emailDeleteMessage,
  emailPhishingGet,                 // P3.3.58 段 2C (6/12 鸿波)
  emailPoliticalScanNow,            // P3.3.53.2 (6/13 鸿波)
  emailExportAttachment,            // P3.5.103 (6/24 鸿波): 附件能点
  openFile,                         // P3.5.103: 系统默认 app 打开
  type EmailDigestItem,
  type PhishingScanResult,          // P3.3.58 段 2C
  type PoliticalScanResult,         // P3.3.53.2
} from "../../../lib/tauri";
import { _extractSenderName, _replyAddress, _buildReplySubject, _buildQuotedBody } from "./helpers";
import { useAgentStore } from "../../../store/agent";
// P3.5.58 (6/22 鸿波 catch "有回复了为啥还要让小鲶处理 是不是重复了"):
// RFC 822 thread chain 算法 + 已回复 badge
import { isReplied, formatReplyTime } from "../../../lib/emailThread";
// P3.5.158 Phase 3 (7/2 鸿波): Compose panel 抽到 ComposeCore 共享组件
import ComposeCore from "./ComposeCore";

interface FullMessage extends EmailDigestItem {
  recipients?: string[];
  cc?: string[];
  attachments?: Array<{ filename: string; size_bytes: number; content_type: string }>;
  /** P3.5.31 (6/17 鸿波 catch): rust email_read_message 返 JSON 已含 body_html
   *  (catfish-email Python adapter MIME extract HTML part 填, base.py:114).
   *  老 type 0 declare → DetailPane 只 access body_text (strip 后 plain) →
   *  和 Apple Mail rendered HTML 不一致. 加 field 让 DetailPane 走 iframe render.
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
  list,
  repliedPool,
  onAskCatfish,
  onDeleted,
}: {
  msg: FullMessage;
  // P3.5.58 (6/22 鸿波): 全 list 传进来给 isReplied 算法用. 算"当前邮件
  // 是否已被回复"必须扫整 list 找 in_reply_to/references 命中.
  list: EmailDigestItem[];
  /** 8/6: 含 Sent 的候选池, 只给 isReplied 用.
   *  跟 list 分开是因为 list 只有 Inbox —— 见下面 replyStatus 的注释. */
  repliedPool?: EmailDigestItem[];
  onAskCatfish: (m: FullMessage) => void;
  onDeleted: () => void;  // 5/18 BL-EMAIL-DELETE: 删除成功 → 父组件移除 item
}) {
  // P3.5.58: 算已回复状态. msg / list 任一变即重算 (useMemo 兜 O(N) 性能).
  //
  // ★ 8/6 修 bug: 原来用的是 list (只有 Inbox). 员工的回信在 Sent, 不在 Inbox
  // → isReplied 永远匹配不到 → **详情页的「已回复」提示从来没亮过**。
  // 这跟 P3.5.204.b 修的 ListItem 角标是同一个病: 7/9 那次在 EmailTab 里用
  // items.concat(sentItems) 修好了列表, 但传给 DetailPane 的还是 items, 漏了这处。
  // 现在优先用 repliedPool (含 Sent), 没传才退回 list.
  const replySource = repliedPool && repliedPool.length ? repliedPool : list;
  const replyStatus = useMemo(() => isReplied(msg, replySource), [msg, replySource]);
  // P3.5.158 (7/2): 保留 draftResult/draftError 显 header 附近保存草稿成功/失败提示,
  // ComposeCore 走 onSaveDraftSuccess callback 通知这个 state.
  const [draftResult, setDraftResult] = useState<string | null>(null);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  // 拿员工自定义 agent name + personality 注入 ComposeCore 拟稿 prompt
  const agentName = useAgentStore((s) => s.name);
  const agentPersonality = useAgentStore((s) => s.personality);
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
  // P3.5.158 (7/2): 拟稿相关 state 搬 ComposeCore, 走 resetKey={msg.id} 自动清.
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
  // 显示 ComposeCore. 红线: AI 不能绕过这个 panel 直接 send, 必须人工在 panel
  // 里点按钮 (ComposeCore 内部两步 confirm 保留红线).
  // P3.5.158 Phase 3 (7/2): Compose 相关 state 全部搬 ComposeCore. DetailPane 只
  // 留 composing 控 open, ComposeCore 通过 resetKey={msg.id} 触发 state 清.
  const [composing, setComposing] = useState(false);

  // 选别的邮件时关 compose
  useEffect(() => {
    setComposing(false);
  }, [msg.id]);

  // P3.5.38.3 (6/18 鸿波 catch '还是无效, 仔细分析'):
  //   audit P3.5.38.2 失败真因 (MDN 实证):
  //     https://developer.mozilla.org/en-US/docs/Web/Security/Practical_implementation_guides/Sandbox_attribute
  //     "If the document is loaded by a sandboxed iframe with srcdoc, the document's
  //      origin is always treated as opaque, even with allow-same-origin."
  //     → srcDoc + sandbox (任何 value) 在 WKWebView 下 origin 是 opaque,
  //       parent.contentDocument 返 null. P3.5.38.2 改 sandbox=allow-same-origin
  //       没用因为 srcDoc 的特殊规则覆盖 allow-same-origin.
  //
  //   正解: **完全砍 sandbox attribute**. srcDoc iframe 跟 parent 同源,
  //   parent 能拿 contentDocument 监听 click → shell.open.
  //
  //   安全 trade-off:
  //   - 砍 sandbox 意味邮件原 <script> 能跑 — XSS 风险存在
  //   - 缓解 1: 邮件客户端历来 strip script, 99% 邮件没 <script> (Gmail/Outlook 等
  //     收发邮件时 server-side sanitize). 实测 GitHub notification / Superlinear
  //     newsletter 等鸿波收的邮件都没 script.
  //   - 缓解 2: iframe 仍是独立 document, CSS 不污染 parent, 即使 script 跑也只能
  //     操作 iframe 内容. parent webview cookies / localStorage 在不同 frame 不可访问.
  //   - 真要严格 XSS 防御应该用 dompurify sanitize body_html 砍 <script> tag —
  //     这是另一个 ticket (引入 npm 依赖 + sanitize 政策 audit), 不在本 hotfix 范围.
  //
  //   用 React onLoad prop 不用 useRef+useEffect 防 race condition (useEffect 可能
  //   跑在 iframe load 之后错过 load event).
  const handleEmailIframeLoad = (e: React.SyntheticEvent<HTMLIFrameElement>) => {
    const iframe = e.currentTarget;
    const doc = iframe.contentDocument;
    if (!doc) {
      console.warn("[EmailDetail] iframe.contentDocument null (cross-origin?)");
      return;
    }
    const onClick = (ev: Event) => {
      let t = ev.target as HTMLElement | null;
      while (t && t.tagName !== "A") t = t.parentElement;
      if (!t) return;
      const url = (t as HTMLAnchorElement).href;
      if (!url) return;
      if (!/^(https?|mailto):/i.test(url)) return;
      ev.preventDefault();
      ev.stopPropagation();
      void (async () => {
        try {
          const { open } = await import("@tauri-apps/plugin-shell");
          await open(url);
        } catch (err) {
          console.warn("[EmailDetail] shell.open 失败:", err);
        }
      })();
    };
    doc.addEventListener("click", onClick, true);
  };

  /** 5/18 BL-EMAIL-COMPOSE-SEND: 点 "起草回复" 不再立即建 draft, 而是开
   *  compose panel 让员工编辑. 这样:
   *    - 取消不留 orphan draft
   *    - 编辑后再 send 不会跟 Mail.app 那侧的草稿不一致 */
  const handleOpenCompose = () => {
    // P3.5.158 Phase 3 (7/2 鸿波): Compose 逻辑抽到 ComposeCore. handleOpenCompose
    // 只负责 open panel, 初始值通过 ComposeCore initial* props 直接从 msg 派生
    // (每次 render 都算, ComposeCore resetKey={msg.id} 触发 state 清).
    setDraftResult(null);
    setDraftError(null);
    setComposing(true);
  };

  const handleComposeSaveSuccess = (draftId: string) => {
    setDraftResult(draftId);
    // ComposeCore 内部会 onClose(), DetailPane 无需再 setComposing(false)
  };

  // 5/18 BL-EMAIL-COMPOSE-SEND 保留 hook: 发送成功 ComposeCore 内部 2s auto close.
  // 未来可加 refresh list / toast 逻辑, 目前 no-op (DetailPane 内不需要额外反应).
  const handleComposeSendSuccess = (_draftId: string) => {
    // no-op for now — ComposeCore auto close + Mail.app 那边处理已足够
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
          {/* 8/8 评审: 原样输出 ISO 串 (2026-08-07T15:45:51.210Z) → 格式化为人话.
              解析失败 (老数据格式怪) 时回退原串. */}
          <div>
            {(() => {
              const d = new Date(msg.date);
              return Number.isNaN(d.getTime())
                ? msg.date
                : d.toLocaleString("zh-CN", {
                    month: "long",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  });
            })()}
          </div>
          <div>账号</div>
          <div>{msg.account}</div>
          {msg.has_attachments && msg.attachments && msg.attachments.length > 0 && (
            <>
              <div>附件</div>
              <div>
                {msg.attachments.map((a, i) => (
                  <button
                    key={i}
                    onClick={async () => {
                      // P3.5.103 (6/24 鸿波): 点附件 → CLI 导出到本地 tmp → 系统默认 app 打开.
                      // CLI 退出码 4 = adapter 不支持 (foxmail / outlook 暂未 implement export_attachment).
                      try {
                        const path = await emailExportAttachment(msg.id, a.filename);
                        await openFile(path);
                      } catch (e) {
                        console.warn("[EmailDetail] 打开附件失败:", e);
                        alert(`打开附件失败: ${e instanceof Error ? e.message : String(e)}`);
                      }
                    }}
                    title={`点击下载并用系统默认 app 打开: ${a.filename}`}
                    style={{
                      marginRight: 8,
                      padding: "3px 10px",
                      border: "1px solid var(--catfish-accent, #0d9488)",
                      borderRadius: 4,
                      background: "transparent",
                      color: "var(--catfish-accent, #0d9488)",
                      cursor: "pointer",
                      fontSize: 12,
                    }}
                  >
                    📎 {a.filename} ({Math.round(a.size_bytes / 1024)} KB)
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
        {/* P3.5.58 (6/22 鸿波 catch): 已回复 badge — RFC 822 thread chain 算法.
            ∃ R: R.in_reply_to == msg.message_id OR msg.message_id ∈ R.references.
            老邮件 / 老 Mail.app 没 message_id 时 isReplied 返 false 静默不显. */}
        {replyStatus.replied && (
          <div
            style={{
              marginTop: 12,
              padding: "6px 10px",
              background: "rgba(34, 197, 94, 0.1)",
              border: "1px solid rgba(34, 197, 94, 0.3)",
              borderRadius: 4,
              fontSize: 12,
              color: "rgb(21, 128, 61)",
              display: "flex",
              alignItems: "center",
              gap: 6,
              flexWrap: "wrap",
            }}
            title="基于 RFC 822 thread headers 算的, 不是 fuzzy subject 比对"
          >
            <span>✓ 这封你已回复过 {replyStatus.replies.length} 次</span>
            <span style={{ opacity: 0.7 }}>
              最新 {formatReplyTime(replyStatus.replies[0]?.date || "")}
              {replyStatus.replies[0]?.sender
                ? ` 由 ${replyStatus.replies[0].sender.replace(/<[^>]+>/g, "").trim() || replyStatus.replies[0].sender}`
                : ""}
            </span>
          </div>
        )}
        {/* 行动按钮 */}
        <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap", alignItems: "center" }}>
          <button
            type="button"
            onClick={() => onAskCatfish(msg)}
            style={{
              // P3.5.58: 已回复时按钮降级为次要色 (淡灰底+绿字), 让 user 视觉感知"这封
              // 不是主要待办了". 不强禁 — user 可能想问后续是否要再回 (例如对方又问问题).
              background: replyStatus.replied
                ? "var(--catfish-bg)"
                : "var(--catfish-cyan)",
              color: replyStatus.replied ? "rgb(21, 128, 61)" : "#fff",
              border: replyStatus.replied
                ? "1px solid rgba(34, 197, 94, 0.4)"
                : "none",
              borderRadius: 4,
              padding: "8px 16px",
              fontSize: 13,
              fontWeight: 500,
              cursor: "pointer",
              fontFamily: "inherit",
            }}
            title={
              replyStatus.replied
                ? `这封你已回复过 ${replyStatus.replies.length} 次. 仍想让小鲶看是不是要再回, 点这里.`
                : "让小鲶在工作台 chat 里多轮聊这封怎么回"
            }
          >
            {replyStatus.replied
              ? `💬 已回复, 再看一下?`
              : "💬 让小鲶处理这封"}
          </button>
          <button
            type="button"
            onClick={handleOpenCompose}
            disabled={composing}
            style={{
              background: "var(--catfish-bg)",
              color: "var(--catfish-text)",
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              padding: "8px 16px",
              fontSize: 13,
              // P3.5.57 (6/22 鸿波 catch UX 误导): composing 时按钮 disabled, opacity 加深
              // 让视觉明确表达"不可点", 不再用 "📝 编辑中…" 文案切换
              // (老文案让 user 以为这个按钮还能点 / 正在做某动作, 实际是 disabled).
              // P3.5.158 (7/2): drafting/setDrafting 搬 ComposeCore, 这里只判 composing
              cursor: composing ? "default" : "pointer",
              opacity: composing ? 0.4 : 1,
              fontFamily: "inherit",
            }}
            title="打开 compose 面板, 编辑回复内容 + 选择保存草稿或发送 (人工 confirm 才发)"
          >
            ✏️ 回复
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

      {/* P3.5.158 Phase 3 (7/2 鸿波): Compose panel 抽 ComposeCore 共享组件.
          回复场景 originalMessage=msg + resetKey=msg.id. 保存/发送成功走 callback. */}
      {composing ? (
        <ComposeCore
          isOpen={composing}
          onClose={() => setComposing(false)}
          onSaveDraftSuccess={handleComposeSaveSuccess}
          onSendSuccess={handleComposeSendSuccess}
          initialTo={_replyAddress(msg.sender)}
          initialCc=""
          initialSubject={_buildReplySubject(msg.subject)}
          initialBody={_buildQuotedBody({
            sender: msg.sender,
            date: msg.date,
            subject: msg.subject || "",
            body_text: msg.body_text,
          })}
          inReplyToMsgId={msg.id}
          account={msg.account}
          originalMessage={{
            sender: msg.sender,
            subject: msg.subject || "",
            date: msg.date,
            bodyText: msg.body_text || "",
          }}
          agentName={agentName}
          agentPersonality={agentPersonality}
          resetKey={msg.id}
        />
      ) : msg.body_html ? (
        /* P3.5.31 (6/17): HTML 邮件 iframe srcdoc render.
           P3.5.38.3 (6/18): 砍 sandbox attribute - parent 能拿 contentDocument 监听 click.
           P3.5.38.4 (6/18 鸿波 catch '内容太靠左, 部分内容超出左边界'):
           audit: iframe 默认 body margin 8px, 但邮件 HTML 常用 margin:0 reset + 自定 layout,
           内容紧贴 iframe edge. 部分 newsletter (例 Superlinear) 用大 fixed-width container
           + negative margin, 在窄 iframe 内会被裁掉左侧.
           修: srcDoc 注入 reset CSS — body padding/margin + img/table max-width 100% +
           long-word break-word, 防超出 iframe 视口. 邮件原 CSS 后置覆盖, 这是 default-only.
           bg 白: 邮件默认 white, override Companion dark theme. */
        <iframe
          title="邮件正文"
          srcDoc={buildEmailSrcDoc(msg.body_html)}
          onLoad={handleEmailIframeLoad}
          style={{
            flex: 1,
            width: "100%",
            border: "none",
            background: "white",
          }}
        />
      ) : (
        /* 正文 (非 compose 时, body_html 空 fallback plain text)
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

/** P3.5.38.4 (6/18 鸿波 catch '内容太靠左, 部分内容超出左边界'): 构造邮件 srcDoc.
 *
 * 注入 default CSS:
 * - body padding 16px / margin 0 / box-sizing border-box → 内容跟 iframe edge 留 buffer
 * - img/table max-width 100% → 防大图把 iframe 推宽
 * - word-wrap / overflow-wrap break-word → 长 URL 折行不溢出
 * - 邮件原 <style> 后置, 覆盖这些 default 是预期 — 这只是邮件没 padding 时的 fallback
 *
 * <base target="_blank">: 兜底 (没 sandbox 时 noop, parent contentDocument click handler 真实生效)
 */
function buildEmailSrcDoc(bodyHtml: string): string {
  const defaultCss = `<style>
html,body{margin:0;padding:0;background:#fff;color:#000;word-wrap:break-word;overflow-wrap:break-word;}
body{padding:16px;box-sizing:border-box;}
img,table,video,iframe{max-width:100% !important;height:auto;}
pre,code{white-space:pre-wrap;word-break:break-word;}
a{word-break:break-all;}
</style>`;
  return `<base target="_blank">${defaultCss}${bodyHtml}`;
}

export default DetailPane;
export type { FullMessage };
