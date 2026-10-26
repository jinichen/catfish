/** Auto-continue toggle (5/13 鸿波"长程任务咋办" — gateway 删 BL-FIX23 retry 后客户端补).
 *
 * 取代之前 gateway 层 BL-FIX23 mid_task plan-only retry. gateway 现在干净
 * 转发, 长任务中途 LLM `finish_reason=stop` 不调 tool 时, 由用户在客户端
 * 主动开 toggle 控制要不要"自动续跑".
 *
 * 开启时:
 *   - 状态进 zustand store + localStorage 持久化
 *   - 这一轮有明确「半途停下」信号 (见 autoContinueDecision) → 自动 append 一条 user "继续", 续到下一轮
 *   - 上限 MAX_AUTO_CONTINUES (3) 防死循环 — 跑到上限就停, 并说一声
 *
 * 关闭时 (10/24 起默认开, 关了才是这样):
 *   - 跟 ChatGPT 一样: LLM stop 就 stop, 用户自己打"继续" 续跑
 *
 * 跟 gateway BL-FIX23 retry 的关键区别:
 *   - 这是**客户端**逻辑, 用户开/关自己控
 *   - 只影响开 toggle 的 session, 不影响其他员工
 *   - 看到自动发"继续" 是可见的 (UI 显淡色 user msg), 不像 gateway 偷偷重发
 *   - 不灌 hint 进 prompt, 只多发一条短 "继续", context 涨得少
 */

import { create } from "zustand";

const STORAGE_KEY = "catfish.auto_continue";

/** 自动续跑上限 — 防死循环. 3 轮够大部分长任务接力, 太多就该手动介入. */
export const MAX_AUTO_CONTINUES = 3;

/** 自动续跑发的消息 — 保持短, 不灌 hint, 让 LLM 接前文继续. */
export const AUTO_CONTINUE_PROMPT = "继续";

/** 10/24 鸿波「现在的任务基本都是长时任务, 缺省就是长时任务」: 默认开。
 *  存 "0" = 员工明确关过 (记住); 没存 / "1" = 开。原来没存 = 关, 关掉是删键 —— 所以老用户也一并变成开。 */
export function loadFromStorage(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) !== "0";
  } catch {
    return true;
  }
}

function saveToStorage(on: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, on ? "1" : "0");
  } catch {
    // localStorage 满 / 禁用 — 静默, 当前 session 内 zustand 仍工作
  }
}

interface AutoContinueState {
  on: boolean;
  toggle: () => void;
  setOn: (on: boolean) => void;
}

export const useAutoContinueStore = create<AutoContinueState>((set) => ({
  on: loadFromStorage(),
  toggle: () => set((s) => {
    const next = !s.on;
    saveToStorage(next);
    return { on: next };
  }),
  setOn: (on: boolean) => {
    saveToStorage(on);
    set({ on });
  },
}));

/** 这一轮停下之后: 自动接力 / 接力次数用完了要说一声 / 就此停。
 *
 * 10/26 改: 只认明确的「半途停下」信号 ——
 *   - loopLimit: 网关每轮工具调用上限叫停 (409 agent_loop_limit_exceeded)
 *   - truncated: 输出被截断 (finish_reason "length")
 *
 * 原来的「这次发送跑过工具 + 模型停下」不再算: 成功的任务最后一轮本来就是「跑过工具后停下」的总结轮,
 * 直连网关时 Companion 看得见工具调用, 做完的任务会被追发 3 次「继续」; 走 hermes 时工具在 hermes 里跑,
 * Companion 看不见, 这一条从来不成立。模型自己说做完了到底做没做完, 前端判断不了, 不猜。 */
export type AutoContinueDecision = "continue" | "exhausted" | "stop";

export function autoContinueDecision(args: {
  on: boolean;
  loopLimit?: boolean;
  truncated?: boolean;
  used: number;
  aborted: boolean;
}): AutoContinueDecision {
  const midTask = args.loopLimit === true || args.truncated === true;
  if (!args.on || !midTask || args.aborted) return "stop";
  return args.used < MAX_AUTO_CONTINUES ? "continue" : "exhausted";
}

/** 接力次数用完时补的一句 (不然员工不知道自动接力已经停了)。 */
export const AUTO_CONTINUE_EXHAUSTED_TEXT =
  `⏭ 已自动接力 ${MAX_AUTO_CONTINUES} 次, 这次先停在这里。还没做完的话点「继续」接着做。`;
