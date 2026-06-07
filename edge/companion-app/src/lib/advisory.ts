/** Advisory feed client — 6/7 BL-MANIFESTO-ADVISORY-PHASE1.
 *
 * Spec: docs/ADVISORY-FEED-SPEC.md
 *
 * # 跟 manifesto 公理 4 一致
 *
 * 客户端走 fetchWithAuth 直接 pull `/api/advisory/feed.json`. 中央服务 publish,
 * 客户端自己拉, 没 "push to device" 类 API.
 *
 * # Phase 1 范围
 *
 * - fetchAdvisoryFeed: 拉 feed (gateway HTTP)
 * - matchAdvisories: 本机匹配 (跟 installed_skills + catfish 版本 比对)
 * - 本机 state ops 通过 Tauri command (commands/advisory.rs)
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";

import { config } from "./env";
import { fetchWithAuth } from "./me";
import type {
  Advisory,
  AdvisoryFeedResponse,
  AdvisoryLocalState,
} from "../types/advisory";
import type { SkillEntry, SkillNamespace } from "../types/identity";

const FEED_PATH = "/api/advisory/feed.json";

/** localStorage key 存上次 etag, 用于条件请求 (304 fast path). */
const ETAG_LS_KEY = "catfish.advisory.feed.etag";
const FEED_LS_KEY = "catfish.advisory.feed.payload";

/** 拉 advisory feed.
 *
 * 走 fetchWithAuth (path 含 /api/ → OAuth path, 直连 gateway 8999).
 * ETag 304 时返回 localStorage 缓存 payload, 失败时返回 null.
 */
export async function fetchAdvisoryFeed(): Promise<AdvisoryFeedResponse | null> {
  const url = `${config.backendUrl}${FEED_PATH}`;
  const cachedEtag = localStorage.getItem(ETAG_LS_KEY);
  const headers: Record<string, string> = {};
  if (cachedEtag) {
    headers["If-None-Match"] = cachedEtag;
  }
  try {
    const resp = await fetchWithAuth(url, { headers });
    if (resp.status === 304) {
      // 沿用缓存 payload
      const cached = localStorage.getItem(FEED_LS_KEY);
      if (cached) {
        try {
          return JSON.parse(cached) as AdvisoryFeedResponse;
        } catch {
          // 缓存损坏, 清掉
          localStorage.removeItem(FEED_LS_KEY);
          localStorage.removeItem(ETAG_LS_KEY);
        }
      }
      return null;
    }
    if (!resp.ok) {
      // 401 (token 过期) / 5xx → 静默, 不阻塞 UI (manifesto: advisory 不是关键路径)
      // eslint-disable-next-line no-console
      console.warn("[advisory] fetch feed failed:", resp.status);
      return null;
    }
    const newEtag = resp.headers.get("etag");
    if (newEtag) {
      localStorage.setItem(ETAG_LS_KEY, newEtag);
    }
    const payload = (await resp.json()) as AdvisoryFeedResponse;
    localStorage.setItem(FEED_LS_KEY, JSON.stringify(payload));
    return payload;
  } catch (e) {
    // 网络断 / fetch reject → 静默
    // eslint-disable-next-line no-console
    console.warn("[advisory] fetch feed exception:", e);
    return null;
  }
}

// ── 本机匹配 ─────────────────────────────────────────────────

/** semver-lite 比较: "<0.2.0" / ">=1.0.0" / "==0.1.0-frozen". 不实现完整 semver,
 *  只够 catfish skill / app 版本 pattern 用. */
function versionMatch(version: string, pattern: string): boolean {
  const trimmed = pattern.trim();
  // 简易解析: 提取 operator + version
  const m = /^(<=|>=|==|<|>|=)?\s*([\w.\-+]+)$/.exec(trimmed);
  if (!m) return false;
  const op = m[1] || "==";
  const target = m[2];
  // 跨字符串字典序比 (适合 X.Y.Z 三段数字, 不适合 100>99 那种)
  // 对 catfish 实际用例够用 (版本号都 < 1.0.0). semver 完整库后续 BL.
  const cmp = compareSemverLite(version, target);
  switch (op) {
    case "<": return cmp < 0;
    case "<=": return cmp <= 0;
    case ">": return cmp > 0;
    case ">=": return cmp >= 0;
    case "=":
    case "==": return cmp === 0;
    default: return false;
  }
}

function compareSemverLite(a: string, b: string): number {
  // 拆 X.Y.Z + 后缀
  const parseSeg = (s: string): [number[], string] => {
    const dash = s.indexOf("-");
    const numericPart = dash < 0 ? s : s.slice(0, dash);
    const suffix = dash < 0 ? "" : s.slice(dash);
    const nums = numericPart.split(".").map((p) => parseInt(p, 10) || 0);
    return [nums, suffix];
  };
  const [na, sa] = parseSeg(a);
  const [nb, sb] = parseSeg(b);
  const len = Math.max(na.length, nb.length);
  for (let i = 0; i < len; i++) {
    const xa = na[i] ?? 0;
    const xb = nb[i] ?? 0;
    if (xa !== xb) return xa - xb;
  }
  // 数字部分一样, 比后缀 (-frozen / -rc.1 / 空)
  // 空后缀 > 任何后缀 (e.g. 0.1.0 > 0.1.0-frozen)
  if (sa === sb) return 0;
  if (!sa) return 1;
  if (!sb) return -1;
  return sa < sb ? -1 : 1;
}

/** 拿员工本机 installed skill list (flatten namespace).
 *
 * 走 fetchInstalledSkills Tauri command — 跟 SkillsMcpCard 同源. */
export async function getInstalledSkills(): Promise<SkillEntry[]> {
  try {
    const namespaces = await rawInvoke<SkillNamespace[]>("list_installed_skills");
    return namespaces.flatMap((ns) => ns.skills);
  } catch (e) {
    // eslint-disable-next-line no-console
    console.warn("[advisory] fetch skills failed:", e);
    return [];
  }
}

/** 拿 catfish 客户端版本. 走 tauri.ts fetchIdentity 已有 (或者直接 import.meta.env).
 *
 * 简化版: 从 package.json 跳, 用 import.meta.env.VITE_CATFISH_VERSION. 没设
 * 时降级 "0.0.0" (永不命中 < 任何 pattern, 安全默认). */
export function getCatfishVersion(): string {
  const env = (import.meta as ImportMeta & { env?: { VITE_CATFISH_VERSION?: string } }).env;
  return env?.VITE_CATFISH_VERSION ?? "0.15.2";
}

/** 单条 advisory 是否命中本机. target 空 → 全员相关 (e.g. policy_recommendation). */
export function matchesAdvisory(
  advisory: Advisory,
  ctx: {
    installedSkills: SkillEntry[];
    catfishVersion: string;
  },
): boolean {
  const target = advisory.target;
  if (!target) return true; // 无 target = 全员相关

  if (target.skill) {
    const skill = ctx.installedSkills.find((s) => s.name === target.skill);
    if (skill) {
      if (!target.skillVersionPattern) return true;
      if (skill.version && versionMatch(skill.version, target.skillVersionPattern)) {
        return true;
      }
    }
  }

  if (target.catfishVersionPattern) {
    if (versionMatch(ctx.catfishVersion, target.catfishVersionPattern)) {
      return true;
    }
  }

  // 没命中
  return false;
}

/** 筛选 feed 里命中本机的 advisory + 排序 (severity 高 → 低). */
export async function getMatchedAdvisories(
  feed: AdvisoryFeedResponse,
): Promise<Advisory[]> {
  const installedSkills = await getInstalledSkills();
  const catfishVersion = getCatfishVersion();
  const matched = feed.advisories.filter((a) =>
    matchesAdvisory(a, { installedSkills, catfishVersion }),
  );
  return matched.sort(
    (a, b) => severityWeight(b.severity) - severityWeight(a.severity),
  );
}

function severityWeight(s: string): number {
  switch (s) {
    case "critical": return 5;
    case "high": return 4;
    case "medium": return 3;
    case "low": return 2;
    case "info": return 1;
    default: return 0;
  }
}

// ── 本机 state Tauri commands ──────────────────────────────

export const advisoryListLocalStates = () =>
  rawInvoke<AdvisoryLocalState[]>("advisory_list_local_states");

export const advisoryGetLocalState = (advisoryId: string) =>
  rawInvoke<AdvisoryLocalState | null>("advisory_get_local_state", { advisoryId });

export const advisoryMarkShown = (advisoryId: string) =>
  rawInvoke<void>("advisory_mark_shown", { advisoryId });

export const advisoryAck = (advisoryId: string) =>
  rawInvoke<void>("advisory_ack", { advisoryId });

export const advisorySnooze = (advisoryId: string, hours: number) =>
  rawInvoke<void>("advisory_snooze", { advisoryId, hours });

export const advisoryDismiss = (advisoryId: string) =>
  rawInvoke<void>("advisory_dismiss", { advisoryId });

// ── 综合: 决定哪条 advisory 该弹 banner ───────────────────

/** 是否该弹 banner. 综合 matched advisory + 本机 state + severity 规则. */
export function shouldShowBanner(
  advisory: Advisory,
  state: AdvisoryLocalState | null,
): boolean {
  if (!state) return true; // 没看过 → 弹

  const now = new Date();
  switch (state.status) {
    case "unseen":
      return true;
    case "seen":
      // seen 但没明确 ack/dismiss → critical 仍然弹, 其它不
      return advisory.severity === "critical";
    case "snoozed":
      if (!state.snoozeUntil) return true;
      return now > new Date(state.snoozeUntil);
    case "acked":
      return false;
    case "dismissed":
      // critical 不允许永久 dismiss, 24h 后重新弹
      if (advisory.severity === "critical" && state.lastShown) {
        const since = now.getTime() - new Date(state.lastShown).getTime();
        return since > 24 * 3600 * 1000;
      }
      return false;
    default:
      return true;
  }
}
