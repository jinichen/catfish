/** Agent 偏好全局 store — 名字 + 人设, 多处 UI 共用 (BL-E11 五一 sprint 5/3 晚).
 *
 * 流程:
 *   App 启动 → loadAgentPrefs() 从 Rust yaml 拉一次 → 缓存
 *   Onboarding / Dashboard 改 → updateAgentPrefs() 写 yaml + 更 store
 *   ChatPanel / Onboarding / Notification 等读 → useAgentStore(s => s.name)
 *
 * 不轮询: 名字一旦改极少再变, 改完手动触发刷新.
 */

import { create } from "zustand";

import {
  DEFAULT_AGENT_PREFS,
  fetchAgentPrefs,
  saveAgentPrefs,
  type AgentPrefs,
  type Personality,
} from "../lib/agent";

interface AgentState {
  name: string;
  personality: Personality;
  /** 是否已成功从 Rust 加载过一次 (防 SSR/启动闪烁默认值) */
  loaded: boolean;
  /** 最近一次错误 */
  error: string | null;
  loadAgentPrefs: () => Promise<void>;
  updateAgentPrefs: (name: string, personality: Personality) => Promise<void>;
}

export const useAgentStore = create<AgentState>((set) => ({
  name: DEFAULT_AGENT_PREFS.name,
  personality: DEFAULT_AGENT_PREFS.personality,
  loaded: false,
  error: null,

  loadAgentPrefs: async () => {
    try {
      const p: AgentPrefs = await fetchAgentPrefs();
      set({ name: p.name, personality: p.personality, loaded: true, error: null });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      console.warn("[agent] load 失败, 用默认:", msg);
      set({ loaded: true, error: msg });
    }
  },

  updateAgentPrefs: async (name, personality) => {
    try {
      const p = await saveAgentPrefs(name, personality);
      set({ name: p.name, personality: p.personality, error: null });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      set({ error: msg });
      throw e; // 让调用方知道失败 (Onboarding 显示错误)
    }
  },
}));
