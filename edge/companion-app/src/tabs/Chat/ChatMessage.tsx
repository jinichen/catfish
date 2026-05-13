/** 单条消息渲染 —— user / assistant / 错误状态 */

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

interface Props {
  msg: Msg;
  /** 是否在这条消息末尾显示流式光标 —— 由父组件计算
   *  (只对"最后一条助手且全局 isStreaming"为 true) */
  showCaret?: boolean;
}

export default function ChatMessage({ msg, showCaret = false }: Props) {
  if (msg.role === "user") {
    return <UserBubble msg={msg} />;
  }
  if (msg.role === "assistant") {
    return <AssistantBubble msg={msg} showCaret={showCaret} />;
  }
  // system 不渲染(gateway 自动注入,前端看不到);
  // tool 角色消息也不直接渲染 —— 它的内容已通过 ChatToolCall 在
  // 上一条 assistant 消息里展示了
  return null;
}

function UserBubble({ msg }: { msg: Msg }) {
  const hasAttachments = msg.attachments && msg.attachments.length > 0;
  // BL-AUTO-CONTINUE (5/13): Companion 自动续跑发的 user msg, UI 标记淡色 +
  // 角标 "🔄 自动续 N/M", 让员工看见这是机器发的不是他自己发的.
  const isAutoContinue = msg._autoContinue !== undefined;
  // BL-HERMES013-RED-1B (5/13 ACP /steer): 中途插话发的 user msg, UI 标记
  // 橙色边框 + 角标 "🎯 中途插话", 跟普通 user msg + 自动续 区分开.
  // 用 partial content 末尾 30 字给 tooltip 显示 LLM 当时被打断在哪儿.
  const isSteered = msg._steered !== undefined;
  const steerTail = isSteered ? msg._steered!.atContent.trim().slice(-60) : "";
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "flex-end",
        marginBottom: "var(--space-4)",
      }}
    >
      <div
        style={{
          background: isSteered
            ? "var(--status-warn-dim, rgba(255, 165, 0, 0.12))"
            : isAutoContinue
            ? "var(--catfish-cyan-dim)"  // 淡色, 区别于真用户消息
            : "var(--catfish-cyan)",
          color: isSteered
            ? "var(--status-warn)"
            : isAutoContinue
            ? "var(--catfish-cyan)"
            : "white",
          padding: "var(--space-3) var(--space-4)",
          borderRadius: "var(--radius-md)",
          maxWidth: "75%",
          fontSize: 14,
          lineHeight: 1.5,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          display: "flex",
          flexDirection: "column",
          gap: hasAttachments && msg.content ? "var(--space-2)" : 0,
          border: isSteered
            ? "1px dashed var(--status-warn)"
            : isAutoContinue
            ? "1px dashed var(--catfish-cyan)"
            : "none",
          opacity: isAutoContinue || isSteered ? 0.92 : 1,
        }}
        title={
          isSteered
            ? `🎯 你中途打断了 LLM 改方向 (ACP /steer 等价).${steerTail ? ` LLM 当时正说到: "...${steerTail}"` : ""}`
            : isAutoContinue
            ? `🔄 Companion 自动续跑 ${msg._autoContinue!.round}/${msg._autoContinue!.max} 轮 (toggle 开了, 长任务 LLM 中途停了, Companion 帮你发 "继续")`
            : undefined
        }
      >
        {isSteered && (
          <div style={{
            fontSize: 11,
            opacity: 0.85,
            marginBottom: 2,
          }}>
            🎯 中途插话改方向
          </div>
        )}
        {isAutoContinue && (
          <div style={{
            fontSize: 11,
            opacity: 0.8,
            marginBottom: 2,
          }}>
            🔄 自动续跑 {msg._autoContinue!.round}/{msg._autoContinue!.max}
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
        {msg.content && <span>{msg.content}</span>}
      </div>
    </div>
  );
}

function AssistantBubble({
  msg,
  showCaret,
}: {
  msg: Msg;
  showCaret: boolean;
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
        {/* BL-MM6 feedback 按钮 + BL-VOICE2 TTS 喇叭: 流式中不显, 防员工误点未完成消息.
            内容空 + 没 tool_calls + 不报错 时也不显 (点空消息无意义).
            TTS 只在有真正文本时显示 (纯 tool_calls 不需要听). */}
        {!showCaret && (msg.content || msg.tool_calls?.length || isError) && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2)",
              marginTop: "var(--space-2)",
            }}
          >
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

/** 把 LiteLLM / Vertex 那种长 traceback 错误压成员工能看的短文案 */
function friendlyError(err: string | undefined): string {
  if (!err) return "未知错误";
  if (err.includes("UNAVAILABLE") || err.includes("503")) {
    return "模型服务器临时高峰 (503),稍后重试或换个模型";
  }
  if (err.includes("Connection error") || err.includes("ServerDisconnected")) {
    return "上游连接失败 — 检查 VPN / Clash / 内网,或换模型";
  }
  if (err.includes("rate limit") || err.includes("429")) {
    return "调用频率超限 (429),稍后重试";
  }
  if (err.includes("401") || err.includes("Unauthorized")) {
    return "鉴权失败 (401),检查 dev token";
  }
  if (err.includes("timeout") || err.includes("timed out")) {
    return "请求超时";
  }
  // 其他错误截短
  return err.length > 200 ? err.slice(0, 200) + "…" : err;
}
