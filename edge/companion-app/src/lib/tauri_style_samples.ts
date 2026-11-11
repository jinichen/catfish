/** 文书风格 = 你指定的范文 + 小鲶总结的写法特点 (11/12) —— tool-bridge style_samples.py 的前端封装。 */
import { invoke } from "@tauri-apps/api/core";

export interface StyleSample { id: string; name: string; chars: number; added_at: number }
export interface StyleState {
  samples: StyleSample[];
  traits: string[];
  traits_at: number | null;
  /** 有范文但还没总结, 或者范文换过了、特点还是按旧的总结的 */
  stale: boolean;
  max_samples: number;
}

const call = (action: string, params: Record<string, unknown> = {}) => invoke<StyleState>("style_samples_call", { action, params });

export const styleState = () => call("state");
/** stage_id 来自群聊同一个暂存口 (roomStage / stageFiles) */
export const styleAdd = (stageId: string, name: string) => call("add", { stage_id: stageId, name });
export const styleRemove = (id: string) => call("remove", { id });
export const styleSetTraits = (traits: string[]) => call("set_traits", { traits });
/** 让小鲶读范文总结写法特点 —— 会把范文正文发给模型 */
export const styleSummarize = () => invoke<StyleState>("style_samples_summarize");

export const STYLE_ACCEPT = ".docx,.pdf,.txt,.md";
