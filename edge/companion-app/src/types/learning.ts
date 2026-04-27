/** 与 Rust commands::learning 对齐 */

export interface MemoryFile {
  name: string;
  size: number;
  modifiedAt: string; // ISO-8601
  modifiedToday: boolean;
}

export interface NewSkill {
  fullName: string;
  description: string;
  modifiedAt: string;
}

export interface TodayLearningStats {
  memories: MemoryFile[];
  memoriesUpdatedToday: number;
  newSkills: NewSkill[];
  newSkillsCount: number;
  sessionsToday: number;
  toolCallsToday: number;
  totalTokensToday: number;

  // ====== 软技能维度 (#46) ======
  /** 今天演练完成次数 (catfish-roleplay 复盘 session 数) */
  coachingSessionsToday: number;
  /** 本周演练次数 (周一至今) */
  coachingSessionsThisWeek: number;
  /** 上周演练次数 (做趋势对比) */
  coachingSessionsPrevWeek: number;
  /** 今天通过 catfish-email 起草的邮件次数 */
  emailsDraftedToday: number;
  /** 本周接触的沟通方法论列表 (STAR / SBI / NVC / ...) */
  methodologiesThisWeek: string[];

  summary: string;
}
