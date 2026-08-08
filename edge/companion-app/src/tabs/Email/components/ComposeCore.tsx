/** ComposeCore — 邮件起草 panel 共享组件 (P3.5.158 Phase 2, 7/2 鸿波).
 *
 * # 抽出真原因
 *
 * 5/18 BL-EMAIL-COMPOSE-SEND Compose panel 内嵌 DetailPane 里, 只支持"回复既有邮件"
 * 场景 (依赖 selected msg 派生 to/subject/quoted body / 拟稿基于原邮件). 7/2 鸿波
 * catch"邮件 tab 少新建入口" — 员工在 catfish 内没法主动写新邮件, 5/18 删的
 * `📬 Mail.app` 按钮是跳系统 Mail.app 起草, 员工离开 catfish. 需要 catfish 内部
 * "新建邮件"入口, 但 Compose panel state (11 useState) + 3 handler + 250 行 JSX
 * 深耦合 msg. 抽 ComposeCore 共享组件, DetailPane 回复场景 / EmailTab 新建场景
 * 都复用.
 *
 * # Props 语义
 *
 * - **isOpen**: 控制 panel 显示. parent 决定何时打开 (回复: 点回复按钮; 新建: 点
 *   新建按钮). 关闭走 onClose callback.
 * - **initialTo/Cc/Subject/Body**: 初始值. 回复场景 parent 从 msg 派生
 *   (_replyAddress / _buildReplySubject / _buildQuotedBody), 新建场景传空.
 * - **inReplyToMsgId**: 传给 email_create_draft 真 in_reply_to 字段. 回复场景传
 *   msg.id, 新建场景传 null. backend `email_create_draft` 支持 Option 是空 = 新建.
 * - **account**: email_create_draft 走真账号. 回复场景传 msg.account (跟原邮件同
 *   账号), 新建场景 parent 决定 (通常传 default account).
 * - **originalMessage**: 拟稿 LLM call `draftEmailReply` 真原邮件 context.
 *   - 回复场景传 {sender, subject, date, bodyText}
 *   - 新建场景传 null → 拟稿按钮禁用 + title 变文案 "新建邮件无原邮件, 拟稿不可用"
 * - **resetKey**: state 清除触发. msg.id 变化 (换邮件) / 新建打开 / 关闭都应该清
 *   state. parent 传 msg.id (回复场景) / "new-compose" (新建场景).
 *
 * # 事件回调
 *
 * - **onClose**: 员工点 × 取消 / 发送成功 2s 后自动 close 触发.
 * - **onSendSuccess(draftId)**: 发送成功真回调. parent 可用来 refresh list / 提示 toast.
 * - **onSaveDraftSuccess(draftId)**: 保存草稿成功真回调. 同上.
 *
 * # 红线保留 (BL-EMAIL-COMPOSE-SEND 5/18)
 *
 * AI 不能绕过这个 panel 直发 send. 员工必须在 panel 里点两步 confirm 按钮才真发.
 * 抽 ComposeCore 后红线**完全保留** — 两步 confirm state machine + `disabled=!composeTo.trim()`
 * + 只暴露 onSendSuccess callback (parent 不能自己触发发送).
 */
import { useEffect, useState } from "react";

import { useChatStore } from "../../../store/chat";

import {
  emailCreateDraft,
  emailSendMessage,
  fetchRole,
} from "../../../lib/tauri";
import { draftEmailReply } from "../../../lib/emailDraft";
import { buildDraftContext } from "../../../lib/emailDraftContext";
import { type Personality } from "../../../lib/agent";

export interface ComposeOriginalMessage {
  sender: string;
  subject: string;
  date: string;
  bodyText: string;
}

export interface ComposeCoreProps {
  /** 控制 panel 显示 */
  isOpen: boolean;
  /** 关闭 panel (取消 / 发送成功后 auto close) */
  onClose: () => void;
  /** 发送成功 callback, parent 可 refresh list / 提示 toast */
  onSendSuccess?: (draftId: string) => void;
  /** 保存草稿成功 callback */
  onSaveDraftSuccess?: (draftId: string) => void;

  /** 初始值 (回复场景派生自 msg, 新建场景传空) */
  initialTo?: string;
  initialCc?: string;
  initialSubject?: string;
  initialBody?: string;

  /** 回复场景传 msg.id, 新建场景传 null. 直接透传给 email_create_draft */
  inReplyToMsgId?: string | null;
  /** 走真账号. 回复场景传 msg.account, 新建场景 parent 决定 (default account) */
  account?: string;

  /** 拟稿真原邮件 context. null = 新建场景 → 拟稿按钮禁用 */
  originalMessage?: ComposeOriginalMessage | null;


  /** agent 名字 / 人格 (拟稿真 prompt 用) */
  agentName: string;
  agentPersonality: Personality | undefined;

  /** state 清除触发 key. msg.id 变化 / 新建打开 / 关闭都应传新 key */
  resetKey: string;
}

export default function ComposeCore({
  isOpen,
  onClose,
  onSendSuccess,
  onSaveDraftSuccess,
  initialTo = "",
  initialCc = "",
  initialSubject = "",
  initialBody = "",
  inReplyToMsgId = null,
  account,
  originalMessage = null,
  agentName,
  agentPersonality,
  resetKey,
}: ComposeCoreProps) {
  // 5/18 BL-EMAIL-COMPOSE-SEND: compose panel state — 员工可编辑真 4 字段 +
  // 发送/保存/拟稿 各阶段 state. 抽自 DetailPane 内嵌 Compose panel (P3.5.158 Phase 2).
  // 7/30: 员工在聊天页 picker 上选的 model, 起草邮件时优先用它 ——
  // 见下面 draftWithLlm 里的说明 (原来读的是滞后的 picker_state.json)。
  const chatModel = useChatStore((s) => s.model);

  const [composeTo, setComposeTo] = useState(initialTo);
  const [composeCc, setComposeCc] = useState(initialCc);
  const [composeSubject, setComposeSubject] = useState(initialSubject);
  const [composeBody, setComposeBody] = useState(initialBody);
  const [sending, setSending] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [sendResult, setSendResult] = useState<string | null>(null);
  const [sendConfirmPending, setSendConfirmPending] = useState(false);

  // P3.5.57 Phase 2 (6/22 鸿波): Compose 内"让小鲶帮我拟稿" 状态
  const [draftingLlm, setDraftingLlm] = useState(false);
  const [draftLlmError, setDraftLlmError] = useState<string | null>(null);
  const [draftLlmDone, setDraftLlmDone] = useState(false); // 拟过一次 → 按钮变"🔄 重拟"
  // 8/6: 这次拟稿参考了哪几篇本地资料, 显给员工看 (空 = 没检索到, 只喂了这一封)
  const [draftCtxNote, setDraftCtxNote] = useState("");
  // 8/7: 429 退避重试时的进度提示, 免得员工干等以为卡死
  const [draftRetry, setDraftRetry] = useState("");

  // 3s 自动取消 send confirm (抽自 DetailPane 相同逻辑)
  useEffect(() => {
    if (!sendConfirmPending) return;
    const t = window.setTimeout(() => setSendConfirmPending(false), 3000);
    return () => window.clearTimeout(t);
  }, [sendConfirmPending]);

  // resetKey 变化清 state — 换邮件 / 新建打开 / 关闭都清. 之前 DetailPane 里
  // 靠 msg.id useEffect 触发, 现在 parent 传 resetKey 达到相同效果.
  useEffect(() => {
    setComposeTo(initialTo);
    setComposeCc(initialCc);
    setComposeSubject(initialSubject);
    setComposeBody(initialBody);
    setSendError(null);
    setSendResult(null);
    setSendConfirmPending(false);
    setDraftLlmError(null);
    setDraftLlmDone(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey]);

  /** P3.5.57 Phase 2 (6/22 鸿波): "让小鲶帮我拟稿" — 一次性 LLM call 填 composeBody.
   *
   *  跟"💬 让小鲶处理这封" (跳工作台 chat) 区别: 这里直接落 Compose body, 不跳 chat.
   *  失败 toast, 不挂 Compose UI. 拟过后按钮变"🔄 重拟" 给 user 不满意时重生成.
   *
   *  P3.5.158 (7/2 鸿波): 抽自 DetailPane. 新建场景 originalMessage=null → 按钮
   *  禁用 (拟稿依赖原邮件 sender/subject/date/bodyText 派生 prompt).
   */
  const handleDraftWithLlm = async () => {
    if (!originalMessage) {
      // 新建场景 UI 已禁用真按钮, 这里兜底防调用
      setDraftLlmError("新建邮件无原邮件, 拟稿功能不可用");
      return;
    }
    setDraftingLlm(true);
    setDraftLlmError(null);
    try {
      // P3.5.139 (6/29 鸿波"都要去除硬编码"): 删 "catfish-private-main" 字面值.
      // chain: picker > role chat_default > Err.
      //   picker 优先 — 跟员工当前对话 model 一致, 不发散 (鸿波 ack)
      //   role chat_default 兜底 — roles.yaml truth source, 客户改 yaml 跟着走
      //   都没拿到抛错 — 比静默兜底硬编码清晰, 数据红线由 roles.yaml 配置
      //
      // 7/30: "picker" 这一环从 getPickerState() 改成直接读 useChatStore().model。
      // 链路和优先级不变, 变的是 picker 值的来源:
      //   picker_state.json 由 chat.ts 在**发送消息前** fire-and-forget 写入
      //   (它存在的目的是给 hermes memory plugin 读 —— sync_turn 的签名拿不到
      //    请求头; 见 lib/picker_state.ts, getPickerState 自己注明"调试用")。
      //   也就是说它是**滞后的派生副本**: 员工切了 model 但还没发过聊天,
      //   读到的是上一个 model —— 写邮件就用了他没选的那个。
      // useChatStore().model 是 ChatModelPicker 的 onChange 直接写的值, 无滞后。
      let model = chatModel || "";
      if (!model) {
        const roleModel = await fetchRole("chat_default");
        if (!roleModel) {
          setDraftLlmError(
            "无法 resolve model (picker 没选 + roles.yaml chat_default 拉不到, gateway 可能没起)",
          );
          return;
        }
        model = roleModel;
      }
      // 8/6: 先从本地 wiki 捞背景 (见 emailDraftContext.ts —— 为什么是 wiki
      // 而不是历史邮件, 那里有完整说明). 捞不到不阻塞拟稿.
      let context;
      let ctxNote = "";
      try {
        const ctx = await buildDraftContext(originalMessage.sender);
        if (ctx.items.length) {
          context = ctx.items;
          ctxNote = `已参考 ${ctx.items.length} 篇本地资料: ${ctx.items
            .map((c) => c.title)
            .join(" · ")}`;
        }
      } catch {
        /* 捞背景失败 → 退回只喂这一封, 不打断 */
      }
      setDraftCtxNote(ctxNote);

      setDraftRetry("");
      const result = await draftEmailReply(
        {
          sender: originalMessage.sender,
          subject: originalMessage.subject,
          date: originalMessage.date,
          bodyText: originalMessage.bodyText,
          context,
          agentName,
          personality: agentPersonality,
          model,
        },
        (attempt, total, status) =>
          setDraftRetry(
            status === 429
              ? `模型正忙, 重试中 (${attempt}/${total})…`
              : `连接不稳, 重试中 (${attempt}/${total})…`,
          ),
      );
      setDraftRetry("");
      if (!result.ok || !result.body) {
        setDraftLlmError(result.error || "未知错误");
        return;
      }
      setComposeBody(result.body);
      setDraftLlmDone(true);
    } catch (e) {
      setDraftLlmError(e instanceof Error ? e.message : String(e));
    } finally {
      setDraftingLlm(false);
    }
  };

  /** 只保存到 Drafts, 不发. 抽自 DetailPane 相同逻辑. */
  const handleSaveDraft = async () => {
    setDrafting(true);
    setSendError(null);
    try {
      const resultJson = await emailCreateDraft({
        to: composeTo,
        cc: composeCc || undefined,
        subject: composeSubject,
        body: composeBody,
        inReplyTo: inReplyToMsgId || undefined,
        account: account,
      });
      const parsed = JSON.parse(resultJson);
      const draftId = parsed?.draft_id ?? "ok";
      onSaveDraftSuccess?.(draftId);
      onClose(); // 保存成功关 panel
    } catch (e) {
      setSendError(e instanceof Error ? e.message : String(e));
    } finally {
      setDrafting(false);
    }
  };

  /** 真发送: 两步 confirm. 抽自 DetailPane 相同逻辑. */
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
    console.log("[BL-EMAIL-COMPOSE-SEND] 起草+发送", {
      to: composeTo,
      subject: composeSubject,
    });
    try {
      const draftJson = await emailCreateDraft({
        to: composeTo,
        cc: composeCc || undefined,
        subject: composeSubject,
        body: composeBody,
        inReplyTo: inReplyToMsgId || undefined,
        account: account,
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
      onSendSuccess?.(draftId);
      // 2s 后关 panel (跟 DetailPane 老逻辑一致, 给员工看"✓ 已发送" 反馈)
      setTimeout(() => {
        onClose();
      }, 2000);
    } catch (e) {
      setSendError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  };

  if (!isOpen) return null;

  // P3.5.57 (6/22 鸿波 catch "都是误导"): 砍绿色红线提示横幅, 通过 UI 两步 confirm
  // 按钮自身表达红线, 不需要文字重复.
  return (
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
      {/* to */}
      <label style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
        <span style={{ width: 50, color: "var(--catfish-text-muted)", flex: "0 0 auto" }}>
          收件人
        </span>
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
        <span style={{ width: 50, color: "var(--catfish-text-muted)", flex: "0 0 auto" }}>
          抄送
        </span>
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
        <span style={{ width: 50, color: "var(--catfish-text-muted)", flex: "0 0 auto" }}>
          主题
        </span>
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

      {/* P3.5.57 Phase 2: "让小鲶帮我拟稿" 按钮 + P3.5.158 新场景判空
          回复场景 originalMessage 非空且 bodyText 非空 → 按钮启用
          新建场景 originalMessage=null → 按钮禁用 + title 变文案
          回复场景但原邮件正文为空 → 按钮禁用 (拟不出稿, 老 DetailPane 相同逻辑) */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ width: 50, color: "var(--catfish-text-muted)", flex: "0 0 auto" }}>
          正文
        </span>
        {(() => {
          const canDraft = !!originalMessage?.bodyText;
          return (
            <button
              type="button"
              onClick={() => void handleDraftWithLlm()}
              disabled={draftingLlm || !canDraft}
              style={{
                background: draftLlmDone ? "var(--catfish-bg)" : "rgba(34, 197, 94, 0.1)",
                color: draftLlmDone ? "var(--catfish-text)" : "rgb(21, 128, 61)",
                border: draftLlmDone
                  ? "1px solid var(--catfish-border)"
                  : "1px solid rgba(34, 197, 94, 0.4)",
                borderRadius: 4,
                padding: "4px 10px",
                fontSize: 12,
                cursor: draftingLlm ? "wait" : canDraft ? "pointer" : "not-allowed",
                fontFamily: "inherit",
                opacity: canDraft ? 1 : 0.4,
              }}
              title={
                canDraft
                  ? draftLlmDone
                    ? "不满意? 重新生成一份草稿 (会覆盖正文区现有内容)"
                    : `让${agentName}根据原邮件起一段回复草稿, 落到下面正文区. 你可改可不发.`
                  : originalMessage
                    ? "原邮件正文为空, 没法拟稿"
                    : "新建邮件无原邮件, 拟稿功能不可用"
              }
            >
              {draftingLlm
                ? `⏳ ${agentName}拟稿中…`
                : draftLlmDone
                  ? "🔄 重拟"
                  : `💡 让${agentName}帮我拟稿`}
            </button>
          );
        })()}
        {draftLlmError && (
          <span style={{ fontSize: 11, color: "rgb(220, 80, 60)" }}>
            ✗ 拟稿失败: {draftLlmError}
          </span>
        )}
        {draftRetry && (
          <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
            ⏳ {draftRetry}
          </span>
        )}
        {/* 8/6: 让员工看得见这次拟稿到底参考了什么 —— 不显的话"读了资料"
            跟"没读"在界面上完全一样, 出问题也无从判断.
            8/7: 去掉 !draftLlmError 条件 —— 原来 LLM 失败就把这行藏了,
            于是 429 那次根本看不出检索成没成, 白丢一条诊断信息. */}
        {draftCtxNote && (
          <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
            📎 {draftCtxNote}
          </span>
        )}
      </div>

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
            setSendConfirmPending(false);
            setSendError(null);
            onClose();
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
            cursor: sending ? "wait" : !composeTo.trim() ? "not-allowed" : "pointer",
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
  );
}
