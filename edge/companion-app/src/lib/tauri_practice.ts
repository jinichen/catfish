/** 题库与复习 (11/02) —— Rust commands/practice_stats 的前端接口。 */
import { invoke } from "@tauri-apps/api/core";

export interface PracticeStats {
  total: number;
  /** 还没掌握的错题 */
  wrong: number;
  /** 今天到期 (含逾期) 要复习的 */
  dueToday: number;
  reviewedToday: number;
}

export const practiceStats = () => invoke<PracticeStats>("practice_stats");
