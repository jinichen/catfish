/** ChatInput AutoContinueToggleButton — 抽自 ChatInput.tsx (5/20 拆分).
 *
 * BL-AUTO-CONTINUE (5/13): auto-continue toggle, 让 LLM 自动续接 truncated 输出.
 *
 * P3.5.46 (鸿波 6/20 catch '🔄 跟长程啥关系'): emoji + tooltip 改, 跟系统自带
 * retry 明确区分.
 * - 系统自带 retry (默认开, 透明): chat.ts P3.5.34 修-A 上游 idle / 修-D
 *   finish_reason 缺失 → 自动重试整条 message. 管"流挂了"技术故障.
 * - 本 toggle (手动开): finish_reason 正常 stop 但任务没完 → 自动发"继续".
 *   管"LLM 偷懒 / context 截断" semantic 场景.
 * 老 emoji 🔄 跟"刷新/重试" 撞, 改 ⏭ (next-track) 暗示"接力下一段".
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
          ? "⏭ 自动接力 ON: LLM 正常 stop 但任务没完 (e.g. 处理 10 个 Excel 只处理了 3 个), Companion 自动发\"继续\" (上限 3 轮). 跟系统自带 retry (流挂了自动重试) 不同 — 这条管 LLM 偷懒. 点关闭回常态."
          : "⏭ 开启自动接力: 长任务 (合并多 Excel / 资质材料整理) LLM 中途 stop 了, Companion 自动发\"继续\" (上限 3 轮). 跟系统自带 retry (上游卡死自动重试) 不同 — 这条管 LLM 正常 stop 但任务没做完. 不开就跟 ChatGPT 一样, 自己打\"继续\"."
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
      ⏭
    </button>
  );
}
export default AutoContinueToggleButton;
