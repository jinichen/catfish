/** ChatInput AutoContinueToggleButton — 抽自 ChatInput.tsx (5/20 拆分).
 *
 * BL-AUTO-CONTINUE (5/13): auto-continue toggle, 让 LLM 自动续接 truncated 输出.
 */

import { useAutoContinueStore } from "../../../store/auto_continue";


function AutoContinueToggleButton({ isStreaming }: { isStreaming: boolean }) {
  const on = useAutoContinueStore((s) => s.on);
  const toggle = useAutoContinueStore((s) => s.toggle);
  return (
    <button
      onClick={toggle}
      disabled={isStreaming}
      title={
        on
          ? "🔄 自动续跑 ON: LLM 跑过 tool 后停了不调下个 tool, Companion 自动发\"继续\" 续 (上限 3 轮). 点关闭回常态."
          : "🔄 开启自动续跑: 长任务 (合并多 Excel / 资质材料整理) 时 LLM 中途停了, Companion 帮你自动发\"继续\" 接力. 不开就跟 ChatGPT 一样, 自己打\"继续\"."
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
      🔄
    </button>
  );
}
export default AutoContinueToggleButton;
