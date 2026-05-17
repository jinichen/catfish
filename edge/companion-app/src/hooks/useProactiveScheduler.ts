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

import { sendNotification, petIsVisible, petEmitBubble } from "../lib/tauri";
import { useAgentStore } from "../store/agent";
import { useUIStore } from "../store/ui";

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
    console.log(`[proactive] fireOne(${time}): 拉 starter...`);
    const s = await fetchProactiveStarter();
    if (!s || !s.starter) {
      console.warn(
        `[proactive] fireOne(${time}): fetchProactiveStarter 没返 starter, 跳过. 检查 gateway /me/proactive_starter 接口或 LLM 配置.`,
        s,
      );
      // 5/6 fix: 已经 markFired 了, 不还原. 网络瞬挂的话员工今天这条就丢了 —
      // tradeoff: 防 StrictMode 双 mount 重复 fire (LLM 多花 token + 双气泡 spam)
      // > "瞬时网络挂导致今天那 1 条丢" (员工还有 dashboard 卡 + 下个时段补).
      return;
    }
    console.log(`[proactive] fireOne(${time}): starter="${s.starter.slice(0, 60)}..."`);
    // 5/18 BL-COMPANION-VITE-CHUNK-WARN: useAgentStore 顶部 static (跟 App.tsx 等保持一致).
    const agentName = useAgentStore.getState().name || "小鲶";

    // 5/6 鸿波: 桌宠 = 主动信息统一出口, 不再砸 macOS 通知刷屏.
    //
    // 流程:
    //   1. 总是 prefill chat input (员工开 Companion 时一眼看到 starter, 改一下就发)
    //   2. 桌宠 visible → emit pet_bubble (桌宠头顶冒气泡, 桌宠 idle → thinking)
    //      桌宠 hidden  → 兜底 macOS 通知 (员工自己关了桌宠, 不能漏消息)
    useUIStore.getState().startProactiveChat(s.starter);

    let usedBubble = false;
    try {
      const visible = await petIsVisible();
      console.log(`[proactive] 桌宠 visible=${visible}`);
      if (visible) {
        // 5/6: emitTo frontend 跨窗目测不可靠, 走 Rust 命令 (app.emit_to) 100% 准
        const diag = await petEmitBubble(s.starter, agentName);
        console.log("[proactive] pet_emit_bubble Rust 返回诊断:", diag);
        usedBubble = true;
      }
    } catch (e) {
      console.warn("[proactive] pet_is_visible 失败, fallback macOS 通知:", e);
    }
    if (!usedBubble) {
      console.log(`[proactive] 桌宠不可见, 走 macOS 通知 fallback`);
      await sendNotification(`${agentName}想跟你聊一句`, s.starter);
    }
    // 5/6 fix: markFired 已在 tick() 决定 fire 时立即标过, 这里不重复.
    console.log(`[proactive] fireOne(${time}): ✅ 完成 (usedBubble=${usedBubble})`);
  } catch (e) {
    console.warn(`[proactive] fireOne(${time}) 失败:`, e);
  }
}

/** 5/6 鸿波报"主动闲聊好像有问题": 老 tick 只在 cur === '09:30'/'14:00'/'17:30'
 *  那一精确分钟触发. 员工 9:31 才打开 Companion → 9:30 那条整天丢. 这 =
 *  "主动闲聊看似没工作". 修成"过点补发":
 *
 *    遍历 TIMES_LOCAL, 找到 "今天还没发 + 当前时间 ≥ schedule time" 的最近一条 fire.
 *    一次只发一个 (防员工跨午夜启动一口气发 3 条).
 *    跨日 firedToday 自然清空 (todayKey 不同).
 *
 *  时间字符串比较: "HH:MM" lexicographic 顺序就是时间顺序, 直接 >= 即可.
 */
function _hhmmGE(a: string, b: string): boolean {
  return a >= b;
}

/** 每分钟看一次, 到点了就发. 简单 polling, 不用 cron. */
export function useProactiveScheduler(): void {
  useEffect(() => {
    if (!isEnabled()) {
      console.log("[proactive] disabled (localStorage:catfish:proactive_enabled=false)");
      return;
    }

    const tick = () => {
      const cur = nowHHMM();
      const m = loadFiredMap();
      const today = todayKey();
      const firedToday = new Set(m[today] || []);

      // 找"今天该发但还没发"的最近一条
      // TIMES_LOCAL 升序, 反向遍历找最近过点的没发的
      for (let i = TIMES_LOCAL.length - 1; i >= 0; i--) {
        const t = TIMES_LOCAL[i];
        if (firedToday.has(t)) continue;
        if (_hhmmGE(cur, t)) {
          console.log(`[proactive] fire ${t} (now=${cur}, missed pickup)`);
          // 5/6 fix: markFired **立即**标 — 防 React StrictMode dev mode 双 mount
          // 让 useEffect 跑两次时, 第二次 tick 看到 firedToday 已含, 不重复 fire.
          // (老代码 markFired 在 fireOne 末尾, await 期间第二个 tick 抢着进来, LLM
          //  调两次 + 员工双气泡/双通知 spam)
          markFired(t);
          void fireOne(t);
          return; // 一次只发一个
        }
      }
    };

    console.log(
      `[proactive] scheduler 启动, times=${TIMES_LOCAL.join(",")} (mount @ ${nowHHMM()})`,
    );
    // mount 立即看一下 (员工任何时间打开都能补发当天最近过点的那条)
    tick();
    const t = window.setInterval(tick, 60_000);  // 每分钟看一次
    return () => window.clearInterval(t);
  }, []);
}
