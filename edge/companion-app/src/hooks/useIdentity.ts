/** 拉身份信息 + Skills + MCP 列表
 *
 * 6/2 BL-SKILLS-CARD-SPLIT (鸿波 6/2 凌晨拍 方案 C): "我录的 skill" 跟 "已装的 skill/MCP"
 * 拆 2 张卡, 配套 2 个 hook:
 *   - useMySkills: 扫 ~/.catfish/skills/ — 员工自己生成 (RecMode / propose_skill)
 *   - useInstalledSkillsAndMcp: 扫 catfish 仓库 skills/ + ~/.hermes/skills/ — 内置 + 装的
 *
 * 老 useSkillsAndMcp 保留 backward compat (内部走 installed), 没真 caller 但留 safety net.
 */

import { useEffect, useState } from "react";
import {
  fetchIdentity,
  fetchInstalledSkills,
  fetchMcpServers,
  fetchMySkills,
  fetchSkills,
} from "../lib/tauri";
import type {
  IdentityInfo,
  SkillNamespace,
  McpServerEntry,
} from "../types/identity";

export function useIdentity() {
  const [identity, setIdentity] = useState<IdentityInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchIdentity()
      .then((d) => !cancelled && setIdentity(d))
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  return { identity, error };
}

/** 6/2 BL-SKILLS-CARD-SPLIT: 员工自己生成的 skill (~/.catfish/skills/). 主卡 MySkillsCard. */
export function useMySkills() {
  const [skills, setSkills] = useState<SkillNamespace[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchMySkills()
      .then((s) => !cancelled && setSkills(s))
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  return { skills, error };
}

/** 6/2 BL-SKILLS-CARD-SPLIT: 内置 + 装的 skill + MCP. 次卡 SkillsMcpCard.
 *
 * E7 phase 2 (6/6): 加 reload() — install/uninstall/addMcp/removeMcp 后调用,
 * 重拉一次数据让 UI 立即反映新状态 (不用员工自己关 / 重开 app). */
export function useInstalledSkillsAndMcp() {
  const [skills, setSkills] = useState<SkillNamespace[] | null>(null);
  const [mcps, setMcps] = useState<McpServerEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // bump 自增触发 useEffect 重跑
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchInstalledSkills(), fetchMcpServers()])
      .then(([s, m]) => {
        if (cancelled) return;
        setSkills(s);
        setMcps(m);
        setError(null);  // reload 成功清错
      })
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const reload = () => setTick((t) => t + 1);

  return { skills, mcps, error, reload };
}

/** @deprecated 6/2 BL-SKILLS-CARD-SPLIT: 老 hook, 拆分后没真 caller. 留 backward compat
 * (Companion 走 useMySkills + useInstalledSkillsAndMcp), 周一可 rm. */
export function useSkillsAndMcp() {
  const [skills, setSkills] = useState<SkillNamespace[] | null>(null);
  const [mcps, setMcps] = useState<McpServerEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchSkills(), fetchMcpServers()])
      .then(([s, m]) => {
        if (cancelled) return;
        setSkills(s);
        setMcps(m);
      })
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  return { skills, mcps, error };
}
