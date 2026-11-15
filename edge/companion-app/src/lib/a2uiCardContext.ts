/** 卡片所在的环境 (lib/a2uiCard.ts): 只有对话页的会话能交卡片; 其他地方 (早安页、定时任务结果) 只看不填。 */
import { createContext } from "react";

export interface A2uiCardEnv {
  /** 点按钮能发回会话 (对话页 ChatPanel 提供) */
  interactive: boolean;
  /** 这个会话里已经交过的卡片编号 */
  submitted: ReadonlySet<string>;
}

export const A2uiCardContext = createContext<A2uiCardEnv>({ interactive: false, submitted: new Set() });
