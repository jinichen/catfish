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
  /** 6/2 BL-SKILLS-PUBLISH-WIRE: 真 skill 目录绝对路径. MySkillsCard 共享按钮
   * 直接传给 catfish_skill_publish(skill_path=...). Rust serde camelCase 单字段不变. */
  path: string;
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
