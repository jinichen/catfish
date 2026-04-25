/** 与 Rust commands::identity::IdentityInfo 对齐 */

export interface IdentityInfo {
  systemUser: string;
  soulSource: string;
  soulTarget?: string;
  skin: string;
  defaultModel?: string;
  activeSessionId?: string;
  activeSessionModel?: string;
}

/** 与 Rust commands::skills::* 对齐 */

export interface SkillEntry {
  name: string;
  description: string;
  version?: string;
}

export interface SkillNamespace {
  namespace: string;
  skills: SkillEntry[];
}

export interface McpServerEntry {
  name: string;
  command: string;
  args: string[];
}
