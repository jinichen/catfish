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
 * 10/24: 默认开 (store/auto_continue.ts loadFromStorage)。
 */

import { useAutoContinueStore } from "../../../store/auto_continue";
import { FastForward } from "@phosphor-icons/react";


function AutoContinueToggleButton({ isStreaming }: { isStreaming: boolean }) {
  const on = useAutoContinueStore((s) => s.on);
  const toggle = useAutoContinueStore((s) => s.toggle);
  return (
    <button
      type="button"
      onClick={toggle}
      disabled={isStreaming}
      aria-pressed={on}
      className="chat-composer__tool-button chat-composer__tool-button--advanced"
      data-active={on || undefined}
      title={
        on
          ? "⏭ 自动接力开: 任务做到一半被叫停 (一轮里工具调用太多、回答太长被截断) 时, 自动发\"继续\" 接着做, 最多 3 次。AI 自己说做完了就停, 不追问 —— 漏了没做的, 自己打\"继续\"。点关闭: 被叫停也停下, 自己点\"继续\"。"
          : "⏭ 自动接力已关: 被叫停就停下, 要接着做自己打\"继续\"。点开启 (默认是开的): 任务做到一半被叫停时自动接着做, 最多 3 次。"
      }
    >
      <FastForward size={18} aria-hidden="true" />
    </button>
  );
}
export default AutoContinueToggleButton;
