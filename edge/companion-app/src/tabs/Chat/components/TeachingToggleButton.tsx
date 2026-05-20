/** ChatInput TeachingToggleButton — 抽自 ChatInput.tsx (5/20 拆分).
 *
 * BL-LEAN-SESSION (5/13): 教学模式 toggle. on=true 时 gateway 收到 X-Catfish-
 * Teaching-Mode header → lean inject (跳 session_history / facts 等).
 */

import { useTeachingStore } from "../../../store/teaching";


function TeachingToggleButton({ isStreaming }: { isStreaming: boolean }) {
  const on = useTeachingStore((s) => s.on);
  const toggle = useTeachingStore((s) => s.toggle);
  return (
    <button
      onClick={toggle}
      disabled={isStreaming}
      title={
        on
          ? "🎓 教学模式 ON: 走 LEAN (关 9 个干扰 inject), 适合教新 skill. 点关闭回常态."
          : "🎓 开启教学模式: 关掉 9 个干扰 inject 让 prompt 干净, 适合教 catfish 跑新流程 (catfish_teach_start). 不影响 mid-task retry."
      }
      style={{
        padding: "6px 10px",
        border: "1px solid " + (on ? "var(--catfish-cyan)" : "var(--catfish-border)"),
        borderRadius: "var(--radius-sm)",
        background: on ? "var(--catfish-cyan-dim)" : "transparent",
        color: on ? "var(--catfish-cyan)" : "var(--catfish-text-muted)",
        fontSize: 14,
        fontWeight: on ? 600 : 400,
        cursor: isStreaming ? "default" : "pointer",
        lineHeight: 1,
        minHeight: 36,
        transition: "all 120ms ease",
      }}
    >
      🎓
    </button>
  );
}


// ── BL-AUTO-CONTINUE (5/13 鸿波"长程任务咋办") ──────────────────────
//
// 自动续跑 toggle. 取代 gateway 删掉的 BL-FIX23 mid-task retry. 开启时:
//   - LLM 跑过 tool 又 stop 没调下个 tool → Companion 自动发"继续" 续跑
//   - 上限 3 轮防死循环
//   - 自动续的 user msg 在 UI 显淡色 + 🔄 角标, 让员工看见
// 关闭时 (默认):
//   - LLM stop 就 stop, 用户自己打"继续" — 跟 ChatGPT 一样

// ── BL-HERMES013-RED-1A (5/13 借鉴 Hermes 0.13 ACP /queue) ─────────
//
// 输框上方 strip — 显排队中的消息 (1+ 条时显示, 0 条时不渲染).
// 每条显: ⏳ + 文本前 60 字 + X 撤回按钮.
// 当前 stream [DONE] 触发 useChat send finally 自动 dequeue + send 第一条.

export default TeachingToggleButton;
