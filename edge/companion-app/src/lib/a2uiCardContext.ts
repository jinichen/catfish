/** 卡片所在的环境 (lib/a2uiCard.ts): 只有对话页的会话能交卡片; 其他地方 (早安页、定时任务结果、知识库预览) 只看不填。 */
import { createContext } from "react";

export interface A2uiCardEnv {
  /** 点按钮能发回会话 (对话页 ChatPanel 提供) */
  interactive: boolean;
  /** 这条回复还在流式输出 —— 代码块没写完, JSON 坏是正常的 */
  streaming: boolean;
  /** 这个会话里已经交过的卡片 key */
  submitted: ReadonlySet<string>;
}

export const A2uiCardContext = createContext<A2uiCardEnv>({ interactive: false, streaming: false, submitted: new Set() });
