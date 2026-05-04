/** BL-E11 命名权 — 员工自定义鲶鱼名 + 人设 (五一 sprint 5/3 晚)
 *
 * 通过 Tauri command 读写 ~/.catfish/companion.yaml 的 agent 段.
 * 写完下次 chat 请求 lib/chat.ts 自动带 X-Catfish-Agent-* header,
 * gateway 拼 personalization preamble 在 SOUL 前面 (优先级最高).
 */

import { invoke } from "@tauri-apps/api/core";

export type Personality = "gentle" | "direct" | "roast";

export interface AgentPrefs {
  name: string;
  personality: Personality;
  /** 后端给的 personality 白名单, 防前端写死跟后端不同步 */
  personality_options: Personality[];
}

export const DEFAULT_AGENT_PREFS: AgentPrefs = {
  name: "小鲶",
  personality: "gentle",
  personality_options: ["gentle", "direct", "roast"],
};

/** 人设的人话标签 + 一句话说明 (Onboarding / Dashboard 选择卡用) */
export const PERSONALITY_LABELS: Record<Personality, { label: string; desc: string }> = {
  gentle: {
    label: "温柔同事 (默认)",
    desc: "贴心, 会主动确认细节, 不催不烦, 适合大多数场景",
  },
  direct: {
    label: "直爽老李",
    desc: "说话短, 不绕弯, 不堆套话; 出错直接说哪儿错怎么改",
  },
  roast: {
    label: "毒舌小赵",
    desc: "敢吐槽, 看到写不好会损一句再给建议; 涉及健康/家庭/收入自动切温柔",
  },
};

export async function fetchAgentPrefs(): Promise<AgentPrefs> {
  return await invoke<AgentPrefs>("get_agent_prefs");
}

export async function saveAgentPrefs(name: string, personality: Personality): Promise<AgentPrefs> {
  return await invoke<AgentPrefs>("set_agent_prefs", { name, personality });
}
