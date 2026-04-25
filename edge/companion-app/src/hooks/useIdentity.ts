/** 拉身份信息 + Skills + MCP 列表 */

import { useEffect, useState } from "react";
import {
  fetchIdentity,
  fetchSkills,
  fetchMcpServers,
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
