/** 单条消息渲染 —— user / assistant / 错误状态 */

import { useState } from "react";
import { ArrowClockwise, FastForward, PencilSimple } from "@phosphor-icons/react";
import { Markdown } from "../../lib/markdown";
import type { ChatMessage as Msg } from "../../types/chat";
import ChatToolCall from "./ChatToolCall";
import { extractFilePaths } from "../../lib/path_detect";
import { FilePillList } from "../../components/FilePill";
import { useAgentStore } from "../../store/agent";
import FeedbackButtons from "./FeedbackButtons";
import SkillFeedbackButtons from "./SkillFeedbackButtons";
// BL-VOICE2 (5/10): TTS 喇叭按钮, 鸿波 "这么好玩的东西没理由不现在做"
import TTSButton from "../../components/TTSButton";
// BL-TASK-ASSESS-3-UI (5/15): assistant 嘴炮 ⚠ badge + 催继续按钮
import PromiseCheckBadge from "./PromiseCheckBadge";

interface Props {
  msg: Msg;
  /** 是否在这条消息末尾显示流式光标 —— 由父组件计算
   *  (只对"最后一条助手且全局 isStreaming"为 true) */
  showCaret?: boolean;
  /** BL-TASK-ASSESS-3-UI (5/15): 点 [⏩ 催它继续] 按钮时发"继续". 由父组件
   *  ChatPanel 传下来, 走跟用户手动发"继续"完全一样的路径. 不走 gateway 重试. */
  onNudge?: () => void;
  /** BL-COMPANION-RESEND (7/23): user msg hover 时显示 🔄 按钮 · 点重发这句.
   *  useChat.resendFromUserMsg 触发. streaming 中隐藏 (避免误触当前 stream). */
  onResend?: (id: string) => void;
  /** BL-COMPANION-EDIT (7/23 P1): user msg hover 时显示 ✏️ 按钮 · 点后气泡变
   *  textarea · Enter 确认发送新内容 · Esc 取消. useChat.editAndResendUserMsg 触发. */
  onEditAndResend?: (id: string, newContent: string) => void;
  /** streaming 中不显 resend/edit 按钮 · caller 传 isStreaming */
  isStreaming?: boolean;
}

export default function ChatMessage({
  msg,
  showCaret = false,
  onNudge,
  onResend,
  onEditAndResend,
  isStreaming = false,
}: Props) {
  if (msg.role === "user") {
    return (
      <UserBubble
        msg={msg}
        onResend={onResend}
        onEditAndResend={onEditAndResend}
        isStreaming={isStreaming}
      />
    );
  }
  if (msg.role === "assistant") {
    return <AssistantBubble msg={msg} showCaret={showCaret} onNudge={onNudge} />;
  }
  // system 不渲染(gateway 自动注入,前端看不到);
  // tool 角色消息也不直接渲染 —— 它的内容已通过 ChatToolCall 在
  // 上一条 assistant 消息里展示了
  return null;
}

function UserBubble({
  msg,
  onResend,
  onEditAndResend,
  isStreaming,
}: {
  msg: Msg;
  onResend?: (id: string) => void;
  onEditAndResend?: (id: string, newContent: string) => void;
  isStreaming?: boolean;
}) {
  const hasAttachments = msg.attachments && msg.attachments.length > 0;
  // BL-AUTO-CONTINUE (5/13): Companion 自动续跑发的 user msg, UI 标记淡色 +
  // 角标 "🔄 自动续 N/M", 让员工看见这是机器发的不是他自己发的.
  const isAutoContinue = msg._autoContinue !== undefined;
  // P3.5.20.1 (6/17 鸿波): isSteered / _steered render 砍 — steer 整链退役.

  // BL-COMPANION-RESEND (7/23): hover state · 显 🔄 按钮.
  // 自动续跑消息不显 (不是员工发的 · 重发无意义).
  const [hovered, setHovered] = useState(false);
  // BL-COMPANION-EDIT (7/23 P1): 编辑态 · click ✏️ → editing=true · 气泡变 textarea.
  // draft = 编辑中的草稿 · 确认前老 msg.content 保留 · 取消 (Esc) 时 draft 丢弃.
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(msg.content);
  const canResend = !!onResend && !isAutoContinue && !isStreaming;
  const canEdit = !!onEditAndResend && !isAutoContinue && !isStreaming;

  function startEdit() {
    // 每次进入编辑态 · 从当前 msg.content 初始化 draft (防上次取消后残留)
    setDraft(msg.content);
    setEditing(true);
  }
  function cancelEdit() {
    // 军规 · draft 丢弃 · 老 msg.content 完好. 无 side effect.
    setEditing(false);
  }
  function confirmEdit() {
    const trimmed = draft.trim();
    // UI 层 gate · 跟 send() 早退语义一致: trimmed 空 + 无附件不发.
    // useChat.editAndResendUserMsg 也有 fail-loud 兜底 (throw) · 双层保护.
    if (!trimmed && !hasAttachments) return;
    onEditAndResend!(msg.id, trimmed);
    setEditing(false);
  }
  function onEditorKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // 复用 ChatInput.tsx:270-275 键盘模式: Shift+Enter 换行 · Enter 发送
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      confirmEdit();
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      cancelEdit();
    }
  }

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: "flex",
        justifyContent: "flex-end",
        alignItems: "center",
        gap: "var(--space-2)",
        // BL-COMPANION-EDIT-RESEND-OVERLAY (7/24 鸿波 catch "按钮太占空间"):
        // 按钮 group 从气泡左侧同一行 · 挪到 absolute overlay 气泡右下.
        //
        // 7/24 v3 修 "hover 时有时看不到按钮" (缝隙 bug):
        //   v1/v2 用 marginBottom + 按钮 top:100% marginTop:2px · wrapper 物理
        //   高度只 = 气泡高, mouse 从气泡下缘移到按钮上缘时**离开 wrapper 边界**,
        //   onMouseLeave fire · hovered=false · 按钮 opacity 0 · pointerEvents
        //   none · mouse event 穿透 · 无法 re-fire onMouseEnter · 按钮卡隐.
        //
        //   修: 用 paddingBottom 扩 wrapper 物理 hit area · 让按钮 physical 位置
        //   完全在 wrapper 内. 按钮 bottom:0 贴 wrapper 底 (padding 里). mouse
        //   从气泡 slide 到按钮全程在 wrapper 边界内 · hovered 持续 true.
        //   总消息间距 = paddingBottom 24 + marginBottom 8 = 32px (跟 space-6+space-2).
        paddingBottom: "24px",
        marginBottom: "var(--space-2)",
        // 按钮 group absolute 定位需要这个作 offsetParent
        position: "relative",
      }}
    >
      <div
        style={{
          background: isAutoContinue
            ? "var(--catfish-cyan-dim)"  // 淡色, 区别于真用户消息
            : "var(--catfish-cyan)",
          color: isAutoContinue
            ? "var(--catfish-cyan)"
            : "white",
          padding: "var(--space-3) var(--space-4)",
          borderRadius: "var(--radius-md)",
          // BL-COMPANION-EDIT (7/23 P1): editing 时 minWidth 60% 撑起 textarea ·
          // 避免气泡太窄. 非编辑态保持 maxWidth 75% 短句居右紧凑.
          maxWidth: "75%",
          minWidth: editing ? "60%" : "auto",
          fontSize: 14,
          lineHeight: 1.5,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          display: "flex",
          flexDirection: "column",
          gap: hasAttachments && (msg.content || editing) ? "var(--space-2)" : 0,
          border: isAutoContinue
            ? "1px dashed var(--catfish-cyan)"
            : editing
            ? "2px solid var(--catfish-cyan-hover, #58c1c9)"  // 编辑态 · 边框加粗提示
            : "none",
          opacity: isAutoContinue ? 0.92 : 1,
        }}
        title={
          isAutoContinue
            ? `🔄 Companion 自动续跑 ${msg._autoContinue!.round}/${msg._autoContinue!.max} 轮 (toggle 开了, 长任务 LLM 中途停了, Companion 帮你发 "继续")`
            : undefined
        }
      >
        {isAutoContinue && (
          <div style={{
            fontSize: 11,
            opacity: 0.8,
            marginBottom: 2,
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
          }}>
            <FastForward size={13} aria-hidden="true" />
            自动续跑 {msg._autoContinue!.round}/{msg._autoContinue!.max}
          </div>
        )}
        {/* 图片附件优先于文字, 视觉上更清楚 */}
        {hasAttachments && (
          <div
            style={{
              display: "flex",
              gap: "var(--space-2)",
              flexWrap: "wrap",
            }}
          >
            {msg.attachments!.map((att, i) =>
              att.kind === "image" ? (
                <img
                  key={i}
                  src={`data:${att.mimeType};base64,${att.base64}`}
                  alt={att.name}
                  style={{
                    maxWidth: 220,
                    maxHeight: 220,
                    borderRadius: "var(--radius-sm)",
                    display: "block",
                    objectFit: "contain",
                    background: "rgba(0,0,0,0.1)",
                  }}
                />
              ) : null,
            )}
          </div>
        )}
        {/* BL-COMPANION-EDIT (7/23 P1): 编辑态 · content 变 textarea + 键盘 tip
            非编辑态 · 老 span 显示 (行为不变). */}
        {editing ? (
          <>
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onEditorKey}
              autoFocus
              rows={Math.max(2, Math.min(8, draft.split("\n").length + 1))}
              style={{
                width: "100%",
                background: "transparent",
                border: "none",
                color: "inherit",
                fontSize: 14,
                lineHeight: 1.5,
                fontFamily: "inherit",
                outline: "none",
                resize: "vertical",
                padding: 0,
                margin: 0,
              }}
            />
            <div
              style={{
                fontSize: 11,
                opacity: 0.75,
                marginTop: "var(--space-1)",
                display: "flex",
                justifyContent: "space-between",
                gap: "var(--space-2)",
              }}
            >
              <span>Enter 发送 · Shift+Enter 换行 · Esc 取消</span>
              {!draft.trim() && !hasAttachments && (
                <span style={{ opacity: 0.9 }}>· 内容不能为空</span>
              )}
            </div>
          </>
        ) : (
          msg.content && <span>{msg.content}</span>
        )}
      </div>
      {/* BL-COMPANION-EDIT-RESEND-OVERLAY (7/24 鸿波 catch "按钮太占空间"):
          编辑/重发按钮 group 从气泡左侧同一行 · 挪到 wrapper 右下角外 absolute
          定位. 老 flex 行内布局: hover 时按钮从 0 宽 → 130px · 挤动气泡位置 ·
          layout 跳. 新 absolute overlay: 不占 flow · 气泡位置永远稳定 · hover
          fade in 150ms · 不 hover pointerEvents:none 防误触.
          位置: top:100% right:0 (贴 wrapper 右下 · 气泡也靠右, 视觉对齐右边).
          文案: 老 "✏️ 编辑" / "🔄 重发" 简化为纯 icon · title tooltip 保留.
          editing 中隐藏 (气泡本身变编辑区, 按钮无意义). */}
      {(canEdit || canResend) && !editing && (
        <div
          className="chat-user-actions"
          data-visible={hovered || undefined}
        >
          {/* 7/24 v4: 按钮 style 对齐 assistant 侧 FeedbackBtn (FeedbackButtons.tsx:268-303):
              - border transparent (无框感) · 老用 catfish-border 实体框 · 视觉风格分裂
              - fontSize 11 · 老 13 · 老太大不搭 assistant 侧
              - padding 2px 8px · 老 2px 6px · 对齐
              - opacity 0.5 常态 → hover 1 · 老无 opacity · 太重
              - transition 100ms · 老 150ms · 对齐
              统一后 · user 侧 ✏️🔄 跟 assistant 侧 👍👎 改 存wiki 视觉一致. */}
          {canEdit && (
            <button
              type="button"
              onClick={startEdit}
              title="编辑这句 · 改完 Enter 发送 · Esc 取消"
              className="chat-user-action"
            >
              <PencilSimple size={14} aria-hidden="true" />
            </button>
          )}
          {canResend && (
            <button
              type="button"
              onClick={() => onResend!(msg.id)}
              title="重发这句 · 删除此消息后的所有回复 · 再发同款给 AI"
              className="chat-user-action"
            >
              <ArrowClockwise size={14} aria-hidden="true" />
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function AssistantBubble({
  msg,
  showCaret,
  onNudge,
}: {
  msg: Msg;
  showCaret: boolean;
  onNudge?: () => void;
}) {
  // BL-E11 后续: 头像 alt 用员工自定义名 (默认 "小鲶")
  const agentName = useAgentStore((s) => s.name);
  const isError = msg.status === "error";
  // 助手把生成的文件路径拼在了 markdown 里 (eg "已生成 /Users/.../report.docx").
  // 提一组 FilePill 出来 — 但只在内容稳定后(非流式)做, 否则路径还没写完就误识别.
  const assistantFilePaths =
    !showCaret && msg.content ? extractFilePaths(msg.content) : [];

  return (
    <div
      className="chat-assistant-message"
      style={{
        display: "flex",
        gap: "var(--space-3)",
        marginBottom: "var(--space-4)",
      }}
    >
      {/* 五一 sprint 5/3 BL-D11: 占位 🐟 emoji 换成正式头像 (avatar-circle.svg).
          头像本身是圆形带暖米底, 不再需要外层 background. width/height 固定 28x28.
          BL-E11 后续: alt 用员工自定义名 (老李/小赵/...) — 屏读员工的命名 */}
      <img
        src="/catfish-avatar.svg"
        alt={agentName}
        width={28}
        height={28}
        style={{
          flexShrink: 0,
          borderRadius: "50%",
          display: "block",
        }}
      />
      <div
        style={{
          flex: 1,
          fontSize: 14,
          lineHeight: 1.6,
          color: "var(--catfish-text)",
          minWidth: 0, // 防 markdown 撑开
        }}
      >
        {msg.content && <Markdown text={msg.content} />}
        {!msg.content && showCaret && (
          <span style={{ color: "var(--catfish-text-muted)" }}>…</span>
        )}
        {/* 助手 message 里直接提到的文件路径 → pill —— skill 之后助手往往
            会写一句 "已生成 /Users/.../报告.docx", 这里让它点击可达. */}
        {assistantFilePaths.length > 0 && (
          <FilePillList paths={assistantFilePaths} />
        )}
        {/* tool calls 列表 —— 每个一行,折叠式 */}
        {msg.tool_calls && msg.tool_calls.length > 0 && (
          <div style={{ marginTop: msg.content ? "var(--space-2)" : 0 }}>
            {msg.tool_calls.map((tc) => (
              <ChatToolCall key={tc.id} call={tc} />
            ))}
          </div>
        )}
        {showCaret && (
          <span
            style={{
              display: "inline-block",
              width: 8,
              height: 14,
              marginLeft: 2,
              background: "var(--catfish-cyan-dim)",
              animation: "catfish-blink 1s infinite",
              verticalAlign: "text-bottom",
            }}
          />
        )}
        {isError && (
          <div
            style={{
              marginTop: "var(--space-2)",
              padding: "var(--space-2) var(--space-3)",
              background: "var(--catfish-bg)",
              border: "1px solid var(--status-err)",
              borderRadius: "var(--radius-sm)",
              fontSize: 12,
              color: "var(--status-err)",
              fontFamily: "var(--font-mono)",
              wordBreak: "break-word",
            }}
          >
            ✗ {friendlyError(msg.error)}
          </div>
        )}
        {/* BL-TASK-ASSESS-3-UI (5/15): 嘴炮断言 badge + 催继续按钮.
            仅 stream done 后渲染 (!showCaret), 且 _promise_check.is_promise_only=true. */}
        {!showCaret && msg._promise_check?.is_promise_only && onNudge && (
          <PromiseCheckBadge msg={msg} onNudge={onNudge} />
        )}
        {/* 只给真正文本回答显示操作。纯 tool call 不再铺一排赞/踩/存档按钮，
            避免长任务里每个内部步骤都制造相同视觉噪声。 */}
        {!showCaret && ((msg.content?.trim().length ?? 0) > 0 || isError) && (
          <div className="chat-message-actions">
            <FeedbackButtons
              messageId={msg.id}
              preview={msg.content || (msg.tool_calls?.[0]?.name ?? "(空)")}
            />
            {msg.content && msg.content.trim().length > 0 && (
              <TTSButton text={msg.content} size={16} />
            )}
          </div>
        )}
        {/* BL-MM11 (5/8) skill 级 feedback: 一条消息含 catfish_run_skill 时,
            对每个调用的 skill 加一行 👍/👎/改 按钮, 写 ~/.catfish/skill_quality.jsonl.
            跟 BL-MM6 共存 — 整体回答打分 + 单 skill 打分独立. */}
        {!showCaret && msg.tool_calls && msg.tool_calls.length > 0 && (
          <>
            {msg.tool_calls
              .filter((tc) => tc.name === "catfish_run_skill" && tc.status === "done")
              .map((tc) => {
                const skillPath = (tc.args?.skill_path as string | undefined) ?? "";
                if (!skillPath) return null;
                return (
                  <SkillFeedbackButtons
                    key={`sf-${tc.id}`}
                    skillPath={skillPath}
                  />
                );
              })}
          </>
        )}
      </div>
    </div>
  );
}

/** 把 LiteLLM / Vertex 那种长 traceback 错误压成员工能看的短文案.
 *
 * BL-C8 (5/16): 这是**双保险兜底**. 主路径走 backend errors.py friendly 翻译
 * (chat.ts 已优先读 detail.friendly), 这里只在 backend friendly 缺失 / 失败时
 * 用关键词匹配兜一层.
 *
 * 跟 central/llm-gateway/src/catfish_gateway/errors.py 保持类别覆盖对齐, 不必
 * 100% 重复翻译 (那是双倍维护负担), 只 catch 最常撞的几类.
 */
function friendlyError(err: string | undefined): string {
  // ⚠ 空错误就说"不知道", 别编一个具体原因。
  //
  // 8/1 这里一度改成了「请求被切换或重启中断，请稍等几秒后重发」——
  // 把"我不知道"换成了一句斩钉截铁的因果断言。而空 err 的真实来源包括网络断、
  // 鉴权过期、上游拒答、进程崩; 这些情况下按提示"等几秒重发"只会再失败一次,
  // 而且把排查方向带偏到一个根本没发生的事情上。
  //
  // 猜错原因比说不知道更糟: 说不知道的人会去看日志, 被告知原因的人不会。
  if (!err) return "未知错误 —— 可以重发一次；如果一直失败，把这条截图给 IT";
  if (err.includes("模型运行通道仍在切换")) {
    return "模型正在切换，请等下拉框恢复后重发";
  }
  // 5xx 上游异常
  if (err.includes("UNAVAILABLE") || err.includes("503") || err.includes("overloaded")) {
    return "模型服务器临时高峰 (503), 稍后重试或换个模型";
  }
  if (err.includes("502") || err.includes("Bad Gateway")) {
    return "上游网关异常 (502) — 内网 LLM endpoint 可能失效, 切换模型";
  }
  if (err.includes("504") || err.includes("Gateway Timeout")) {
    return "上游网关超时 (504) — 上游慢, 稍后再试 / 换模型";
  }
  if (err.includes("500") || err.includes("InternalServerError") || err.includes("Internal Server Error")) {
    return "上游内部错误 (500) — 上游服务自己挂了, 换模型或稍后试";
  }
  // 网络层
  if (
    err.includes("Connection error") ||
    err.includes("ServerDisconnected") ||
    err.includes("APIConnectionError") ||
    err.includes("ConnectionError")
  ) {
    return "上游连接失败 — 检查 VPN / Clash / 内网, 或换模型";
  }
  if (err.includes("ConnectionRefused") || err.includes("connection refused")) {
    return "网络层拒绝连接 — 内网服务没在跑 / VPN 没连上";
  }
  if (err.includes("Broken pipe") || err.includes("ClientOSError")) {
    return "连接中途断了 — 网络抖动, 重试一次";
  }
  // 4xx 客户端错误
  if (err.includes("rate limit") || err.includes("429")) {
    return "调用频率超限 (429), 稍后重试";
  }
  if (err.includes("401") || err.includes("Unauthorized")) {
    return "鉴权失败 (401), 检查登录状态";
  }
  if (err.includes("403") || err.includes("Forbidden")) {
    return "没权限调这个模型 (403) — 公司账号未开通 / 区域受限";
  }
  if (err.includes("404") || err.includes("not found")) {
    return "上游说没这个模型 (404) — 检查 models.yaml 配置";
  }
  if (err.includes("400") || err.includes("Bad Request") || err.includes("BadRequest")) {
    return "请求格式错 (400) — 模型 / 参数 / 工具 schema 有一个不对";
  }
  // Provider 特有
  if (err.includes("RESOURCE_EXHAUSTED") || err.includes("quota exceeded")) {
    return "Gemini 免费配额今日耗尽 — 切到 Qwen 或明天再试";
  }
  if (err.includes("timeout") || err.includes("timed out")) {
    return "请求超时 — 网络慢或上游慢, 稍后再试";
  }
  // 其他错误截短
  return err.length > 200 ? err.slice(0, 200) + "…" : err;
}
