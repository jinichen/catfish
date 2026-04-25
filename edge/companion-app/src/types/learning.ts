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
  summary: string;
}
