/** 主动闲聊定时调度 — BL-E13 C-MVP (五一 sprint 5/2 收尾).
 *
 * 每天 3 个时段自动 fetch starter + 发 macOS 通知:
 *   - 9:30   早上召唤
 *   - 14:00  下午召唤
 *   - 17:30  傍晚 (鼓励攒今日进 journal)
 *
 * 通知点击 → macOS 把 Companion 拉前台, RunEvent::Reopen 处理 (lib.rs 有了).
 *
 * 防抖: localStorage 记每个时段当天发没发, 同一时段不重复.
 *
 * 配置 (后续加 UI, 现在硬编码):
 *   localStorage["catfish:proactive_enabled"] = "true" | "false" (默认 true)
 */

import { useEffect } from "react";

import { fetchProactiveStarter } from "../lib/me";
import { sendNotification } from "../lib/tauri";

const TIMES_LOCAL = ["09:30", "14:00", "17:30"];
const _ENABLED_KEY = "catfish:proactive_enabled";
const _LAST_FIRED_KEY = "catfish:proactive_last_fired";

function isEnabled(): boolean {
  try {
    const v = localStorage.getItem(_ENABLED_KEY);
    return v === null ? true : v === "true";
  } catch {
    return true;
  }
}

function todayKey(): string {
  const d = new Date();
  return `${d.getFullYear()}-${(d.getMonth() + 1)
    .toString()
    .padStart(2, "0")}-${d.getDate().toString().padStart(2, "0")}`;
}

function loadFiredMap(): Record<string, string[]> {
  try {
    const raw = localStorage.getItem(_LAST_FIRED_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveFiredMap(m: Record<string, string[]>): void {
  try {
    localStorage.setItem(_LAST_FIRED_KEY, JSON.stringify(m));
  } catch {
    /* ignore quota / etc */
  }
}

function alreadyFiredToday(time: string): boolean {
  const m = loadFiredMap();
  const key = todayKey();
  const list = m[key] || [];
  return list.includes(time);
}

function markFired(time: string): void {
  const m = loadFiredMap();
  const key = todayKey();
  // 只保留今天 + 昨天 (清掉更老的)
  const today = key;
  const trimmed: Record<string, string[]> = {};
  for (const [k, v] of Object.entries(m)) {
    if (k === today) trimmed[k] = v;
  }
  trimmed[today] = [...(trimmed[today] || []), time];
  saveFiredMap(trimmed);
}

function nowHHMM(): string {
  const d = new Date();
  return `${d.getHours().toString().padStart(2, "0")}:${d
    .getMinutes()
    .toString()
    .padStart(2, "0")}`;
}

async function fireOne(time: string): Promise<void> {
  try {
    const s = await fetchProactiveStarter();
    if (!s || !s.starter) return;
    // 五一 sprint 5/3 BL-D11: macOS 通知左侧已有 app icon (新 mark), 标题去 🐟 冗余
    await sendNotification("小鲶想跟你聊一句", s.starter);
    markFired(time);
  } catch (e) {
    console.warn("[proactive] fire 失败:", e);
  }
}

/** 每分钟看一次, 到点了就发. 简单 polling, 不用 cron. */
export function useProactiveScheduler(): void {
  useEffect(() => {
    if (!isEnabled()) return;

    const tick = () => {
      const cur = nowHHMM();
      if (!TIMES_LOCAL.includes(cur)) return;
      if (alreadyFiredToday(cur)) return;
      void fireOne(cur);
    };

    // mount 立即看一下 (员工 9:30 重启 Companion 还是该收到)
    tick();
    const t = window.setInterval(tick, 60_000);  // 每分钟看一次
    return () => window.clearInterval(t);
  }, []);
}
