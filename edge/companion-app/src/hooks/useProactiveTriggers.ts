/** BL-E13.5 真主动 Phase A — 信号触发主动闲聊.
 *
 * 5/6 鸿波 "现在那个不好玩, 直接做" 后启动. 跟 useProactiveScheduler (死时间)
 * 并存, 后者保留作兜底.
 *
 * 流程:
 *   1. 1 分钟一次 tick
 *   2. 拉当前 chat messages + journal raw + focus 状态 + fired log
 *   3. 调 detectAnyTrigger 看有没有信号命中 + 不打扰守卫
 *   4. 命中 → petEmitBubble (桌宠头顶) + startProactiveChat (chat assistant 直接出)
 *      + 写 firedLog 给下次 tick 防重
 *
 * focus 状态: getCurrentWindow().onFocusChanged 事件, useRef 维护离开 / 回来时间戳
 *
 * Dismiss: 桌宠气泡 8s 自动收 (现有 pet.tsx 逻辑); 员工显式点桌宠唤主窗算"互动",
 *   可以视为 dismiss → 写 lastDismissTs. 暂不做这个细化, 先观察用户体验.
 */

import { useEffect, useRef } from "react";

import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";

import { useChatStore } from "../store/chat";
import { useUIStore } from "../store/ui";
import { useAgentStore } from "../store/agent";
import { petEmitBubble, petIsVisible } from "../lib/tauri";
import { fetchContextualStarter } from "../lib/me";
import { detectAnyTrigger, type TriggerKind, type TriggerResult } from "../lib/triggers";

const TICK_MS = 60_000;
const FIRED_LOG_KEY = "catfish:proactive_triggers_fired_log";
const FIRED_LOG_MAX_ENTRIES = 30;
const DISMISS_TS_KEY = "catfish:proactive_triggers_last_dismiss";

interface FiredEntry {
  kind: TriggerKind;
  ts: number;
  why: string;
}

function loadFiredLog(): FiredEntry[] {
  try {
    const raw = localStorage.getItem(FIRED_LOG_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.slice(-FIRED_LOG_MAX_ENTRIES) : [];
  } catch {
    return [];
  }
}

function saveFiredLog(log: FiredEntry[]): void {
  try {
    const trimmed = log.slice(-FIRED_LOG_MAX_ENTRIES);
    localStorage.setItem(FIRED_LOG_KEY, JSON.stringify(trimmed));
  } catch {
    /* ignore quota */
  }
}

function loadDismissTs(): number | null {
  try {
    const raw = localStorage.getItem(DISMISS_TS_KEY);
    return raw ? parseInt(raw, 10) || null : null;
  } catch {
    return null;
  }
}

export function useProactiveTriggers(): void {
  // focus 状态用 ref 避免 effect 重 mount
  const lastFocusLeftTsRef = useRef<number | null>(null);
  const lastFocusReturnTsRef = useRef<number | null>(null);

  // 监听 Companion 主窗 focus / blur (Tauri 2 API)
  useEffect(() => {
    const win = getCurrentWindow();
    const unlistenPromise = win.onFocusChanged(({ payload: focused }) => {
      const now = Date.now();
      if (focused) {
        lastFocusReturnTsRef.current = now;
        console.log(`[triggers] focus return @ ${new Date(now).toLocaleTimeString()}`);
      } else {
        lastFocusLeftTsRef.current = now;
        console.log(`[triggers] focus left @ ${new Date(now).toLocaleTimeString()}`);
      }
    });
    return () => {
      void unlistenPromise.then((u) => u());
    };
  }, []);

  // 主 tick: 1 分钟看一次有没有信号命中
  useEffect(() => {
    let cancelled = false;

    const tick = async () => {
      if (cancelled) return;
      try {
        const now = new Date();
        const messages = useChatStore.getState().messages;

        // 拉 journal raw (Rust 命令)
        let journalText = "";
        try {
          journalText = await invoke<string>("journal_read_raw");
        } catch (e) {
          console.warn("[triggers] journal_read_raw 失败 (跳过 deadline 信号):", e);
        }

        const firedLog = loadFiredLog();
        const lastDismissTs = loadDismissTs();

        const trigger: TriggerResult | null = detectAnyTrigger({
          now,
          messages,
          journalText,
          lastFocusLeftTs: lastFocusLeftTsRef.current,
          lastFocusReturnTs: lastFocusReturnTsRef.current,
          firedLog: firedLog.map((e) => ({ kind: e.kind, ts: e.ts })),
          lastDismissTs,
        });

        if (!trigger) return;

        console.log(
          `[triggers] 🔔 fire ${trigger.kind}: ${trigger.why}`,
        );

        // 落 fired log (写 localStorage 给下一 tick 防重)
        firedLog.push({ kind: trigger.kind, ts: now.getTime(), why: trigger.why });
        saveFiredLog(firedLog);

        // Phase B: 先调 gateway 让 LLM 用信号 + context 重写 starter (5s timeout).
        // 失败兜底用 trigger.message (本地模板).
        let finalStarter = trigger.message;
        let starterSource = "local_template";
        try {
          const llm = await fetchContextualStarter(trigger.kind, trigger.context);
          if (llm && llm.starter) {
            finalStarter = llm.starter;
            starterSource = `llm (${llm.source})`;
          }
        } catch (e) {
          console.warn("[triggers] fetchContextualStarter 异常, 用本地模板:", e);
        }
        console.log(
          `[triggers]   starter (${starterSource}): "${finalStarter.slice(0, 80)}"`,
        );

        // 走桌宠气泡 + chat assistant 直接发 (跟死时间触发同一出口)
        const agentName = useAgentStore.getState().name || "小鲶";
        useUIStore.getState().startProactiveChat(finalStarter);

        try {
          const visible = await petIsVisible();
          if (visible) {
            await petEmitBubble(finalStarter, agentName);
            console.log("[triggers] pet_emit_bubble OK");
          } else {
            console.log("[triggers] 桌宠不可见, 仅 chat 直接 push assistant message");
          }
        } catch (e) {
          console.warn("[triggers] petEmitBubble 失败:", e);
        }

        // focus return 触发后, 清掉 lastFocusLeftTs 防重复触发
        if (trigger.kind === "focus") {
          lastFocusLeftTsRef.current = null;
        }
      } catch (e) {
        console.warn("[triggers] tick 异常:", e);
      }
    };

    // 启动立即跑一次 (员工启动后该看就看)
    void tick();
    const t = window.setInterval(tick, TICK_MS);

    console.log(`[triggers] scheduler 启动, tick=${TICK_MS / 1000}s`);

    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, []);
}
