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

/** BL-MM9 propose_skill 提议 (jsonl event log → 一条).
 *
 * 跟 NewSkill 区分: NewSkill 是真 ship 文件 (~/.hermes/skills/), ProposedSkill
 * 是 LLM 调 propose_skill tool 写到 ~/.catfish/skill_proposals.jsonl 等员工 accept.
 * 鸿波 5/9 反馈 '今天不是有新增 SKILL 吗?' — 真情况经常是 LLM 嘴说没真调 tool
 * (plan-only). UI 区分这三层.
 */
export interface ProposedSkill {
  fullName: string;
  description: string;
  proposedAt: string;
  /** "proposed" / "accepted" / "rejected" */
  status: string;
}

export interface TodayLearningStats {
  memories: MemoryFile[];
  memoriesUpdatedToday: number;
  newSkills: NewSkill[];
  newSkillsCount: number;
  /** BL-MM9-followup (5/9): 今天 LLM propose_skill 写到 jsonl 的 (未 ship) */
  proposedSkillsToday: ProposedSkill[];
  proposedSkillsTodayCount: number;
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
