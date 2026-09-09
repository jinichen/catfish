/** 简报右栏 task-scoped chat 里的单条消息气泡。
 *
 * 8/15 从 BriefingTwoColumnView.tsx 搬出来 (先进 BriefingDetailPane.tsx,
 * 发现那个文件加上 import 头会变成 811 行又过线, 于是这个叶子再单独一个文件)。
 *
 * # user 气泡为什么不走 Markdown
 *
 * user 气泡是 teal 填充 + 白字, 而 Markdown 渲染出来默认是黑字, 叠在 teal 上
 * 直接撞色看不清。所以 user 消息走纯文本 + whiteSpace: pre-wrap 保留换行,
 * 只有助手消息才过 Markdown。
 */
import ChatToolCall from "../../Chat/ChatToolCall";
import { Markdown } from "../../../lib/markdown";  // P3.3.10 fix (6/10): 复用工作台 markdown render (粗体/列表/代码块)
import type { ChatMessage } from "../../../types/chat";

export function ChatMsg({ msg }: { msg: ChatMessage }) {
  if (msg.role === "user") {
    // user bubble teal fill + 白字, Markdown 默认黑字会撞色 — user 消息走纯文本.
    // whiteSpace pre-wrap 保留换行.
    return (
      <div
        className="briefing-2col__msg briefing-2col__msg--user"
        style={{ whiteSpace: "pre-wrap" }}
      >
        {msg.content}
      </div>
    );
  }
  // tool 角色不直接渲染 — 已经通过 ChatToolCall 卡片在 assistant.tool_calls[i].result 里显
  if (msg.role === "tool" || msg.role === "system") return null;

  // assistant: markdown 文本 + tool_calls 卡片 (P3.3.10 fix 6/10: 用 Markdown 组件)
  const hasContent = (msg.content ?? "").length > 0;
  const hasToolCalls = (msg.tool_calls ?? []).length > 0;
  // 任务详情已有 TaskChatProgress 展示阶段、耗时和停止入口；空的 assistant
  // 气泡只会让员工误以为页面卡死，因此在首段输出前不渲染它。
  if (!hasContent && !hasToolCalls && msg.status === "streaming") return null;
  return (
    <div
      className={
        "briefing-2col__msg briefing-2col__msg--assistant" +
        (msg.status === "streaming" ? " briefing-2col__msg--streaming" : "") +
        (msg.status === "error" ? " briefing-2col__msg--error" : "")
      }
    >
      {hasContent && <Markdown text={msg.content} />}
      {msg.status === "error" && (
        <div>⚠️ {msg.error || "回答失败，请重新发送"}</div>
      )}
      {hasToolCalls && (
        <div style={{ marginTop: hasContent ? 8 : 0 }}>
          {msg.tool_calls!.map((tc) => (
            <ChatToolCall key={tc.id} call={tc} />
          ))}
        </div>
      )}
    </div>
  );
}

// (P3.3.7 Phase 1: Section / OptionRow / ComplianceFlagInline / PoliticalFlagInline
//  砍掉, 因为 detail pane 改成 chat. flag / option 信息已经在 system prompt 里注入,
//  LLM 会主动用. 老 helper 在 git history 6/10 之前的 commit 找得回.)
