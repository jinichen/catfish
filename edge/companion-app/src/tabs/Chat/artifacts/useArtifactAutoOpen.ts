/**
 * 成果区跟着对话走 (11/07):
 *   - 回答进行中: 小鲶每做完一步 (工具调用完成), 这一步写出的这一轮新文件马上在右边打开;
 *     一轮里写了好几个, 右栏跟到最新的那个。只看已完成的工具结果 —— 正文还在一个字一个字出来, 路径可能只有半截。
 *   - 一轮结束: 再按回答正文 + 工具结果整体看一遍, 打开最后提到的新文件 (正文里才提到的也能接住)。
 *   - 「新文件」= 修改时间在这一轮开始之后; 小鲶只是读了 / 引用了的老文件不弹。
 *     例外: 一轮结束时没有新文件, 但最后回答里交出了成果目录 (~/.catfish/outputs/) 下的文件 —— 之前做好、
 *     这一轮原样交付的 —— 也打开 (lib/artifactKinds.ts lastAnswerOutputs)。
 *   - 这一轮里用户自己开过 / 切过 / 关过右栏, 自动打开就不再抢 (store.autoOpen 判断)。
 *   - 回答进行中小鲶用上了浏览器工具 (名字里带 browser 的, 如 catfish_browser_goto), 切到「浏览器」页看它操作;
 *     之后写出新文件照样切回「成果」。
 *   - 换到别的对话 / 开新对话, 右栏收起 (上一段对话的成果不该挂在新对话旁边)。
 *
 * 两条信号来源 (11/07 鸿波实测 dev 下右栏没弹才发现第二条):
 *   - Companion 自己跑的工具: 消息里的 tool_calls 随时更新, 上面按消息看。
 *   - hermes 跑的工具 (小鲶多数工具, 包括 catfish_browser_goto): 回答进行中消息里**没有** tool_calls,
 *     要等这一轮结束从库里重读才有。过程中只有 hermes 推的「工具开始 / 结束」事件
 *     (lib/chat.ts 转成 window 事件 catfish:tool-progress): 开始带工具名和一行参数摘要, 结束只带名字。
 *     用上浏览器 → 开始就切浏览器页; 写文件 → 结束时从它开始时的摘要里取路径, 是这一轮写的就打开。
 */
import { useEffect, useRef } from "react";
import type { ChatMessage } from "../../../types/chat";
import { artifactKind, lastAnswerOutputs, lastRoundArtifacts } from "../../../lib/artifactKinds";
import { extractFilePaths } from "../../../lib/path_detect";
import { artifactStat } from "../../../lib/tauri_artifacts";
import { useArtifactStore } from "../../../store/artifacts";

/** 文件时间和消息时间来自同一台机器, 留 2 秒余量 (文件系统时间精度 / 消息时间是发送前打的)。 */
const CLOCK_SLACK_MS = 2000;

export async function freshArtifact(paths: string[], since: number): Promise<string | null> {
  for (const p of paths) {
    try {
      const info = await artifactStat(p);
      if (info.mtime_ms >= since - CLOCK_SLACK_MS) return p;
    } catch {
      // 文件不在 / 打不开: 看下一个
    }
  }
  return null;
}

/** 这一轮 (最后一条用户消息之后) 小鲶有没有开始用浏览器工具, 以及这一轮开始的时间 */
export function roundBrowserUse(messages: ChatMessage[]): number | null {
  let i = messages.length - 1;
  while (i >= 0 && messages[i].role !== "user") i--;
  if (i < 0) return null;
  const since = Date.parse(messages[i].ts);
  const used = messages.slice(i + 1).some((m) => (m.tool_calls ?? []).some((c) => /browser/i.test(c.name)));
  return used && Number.isFinite(since) ? since : null;
}

/** 这一轮开始的时间 (最后一条用户消息的时间) */
function roundStart(messages: ChatMessage[]): number | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "user") {
      const t = Date.parse(messages[i].ts);
      return Number.isFinite(t) ? t : null;
    }
  }
  return null;
}

export interface ToolProgress {
  tool: string;
  status: "running" | "completed";
  label: string;
  toolCallId: string;
  session_id: string | null;
}

/** 已完成的工具调用数 —— 回答进行中消息每来一个字都会变, 只在这个数变了才去找新文件 */
function doneToolCount(messages: ChatMessage[]): number {
  let n = 0;
  for (const m of messages) {
    if (m.role === "tool") n++;
    for (const c of m.tool_calls ?? []) if (c.status === "done") n++;
  }
  return n;
}

export function useArtifactAutoOpen(messages: ChatMessage[], isStreaming: boolean, sessionId: string | null) {
  const latest = useRef(messages);
  latest.current = messages;

  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const checkSeq = useRef(0);
  const appliedSeq = useRef(0);

  // 回答进行中: 每做完一步看一次
  const seen = useRef<{ since: number; done: number; paths: Set<string> }>({ since: 0, done: -1, paths: new Set() });
  const done = isStreaming ? doneToolCount(messages) : -1;
  useEffect(() => {
    if (!isStreaming) return;
    const round = lastRoundArtifacts(latest.current, true);
    if (!round) return;
    if (seen.current.since !== round.since) seen.current = { since: round.since, done: -1, paths: new Set() };
    if (seen.current.done === done) return;
    seen.current.done = done;
    const fresh = round.paths.filter((p) => !seen.current.paths.has(p));
    if (fresh.length === 0) return;
    fresh.forEach((p) => seen.current.paths.add(p));
    // 不在下一步完成时取消 (那样这一步的文件就丢了); 用序号保证后发起的赢, 先发起的晚回来不会盖掉新的
    const seq = ++checkSeq.current;
    void freshArtifact(fresh, round.since).then((p) => {
      if (!p || !mounted.current || seq < appliedSeq.current) return;
      appliedSeq.current = seq;
      useArtifactStore.getState().autoOpen(p, round.since);
    });
  }, [isStreaming, done]);

  // 回答进行中: 小鲶一用上浏览器就切到浏览器页 (每轮一次)
  const browserRound = useRef(0);
  const browserSince = isStreaming ? roundBrowserUse(messages) : null;
  useEffect(() => {
    if (browserSince === null || browserRound.current === browserSince) return;
    browserRound.current = browserSince;
    useArtifactStore.getState().autoBrowser(browserSince);
  }, [browserSince]);

  // hermes 跑的工具: 过程中只有开始 / 结束事件
  const sessionRef = useRef(sessionId);
  sessionRef.current = sessionId;
  useEffect(() => {
    const labels = new Map<string, string>();
    const onProgress = (e: Event) => {
      const d = (e as CustomEvent<ToolProgress>).detail;
      if (!d?.tool) return;
      if (d.session_id && sessionRef.current && d.session_id !== sessionRef.current) return; // 别的对话的
      const since = roundStart(latest.current);
      if (since === null) return;
      if (d.status === "running") {
        labels.set(d.toolCallId, d.label);
        if (/browser/i.test(d.tool)) useArtifactStore.getState().autoBrowser(since);
        return;
      }
      const label = labels.get(d.toolCallId) ?? "";
      labels.delete(d.toolCallId);
      const paths = extractFilePaths(label).filter((p) => artifactKind(p) !== null).reverse();
      if (paths.length === 0) return;
      const seq = ++checkSeq.current;
      void freshArtifact(paths, since).then((p) => {
        if (!p || !mounted.current || seq < appliedSeq.current) return;
        appliedSeq.current = seq;
        useArtifactStore.getState().autoOpen(p, since);
      });
    };
    window.addEventListener("catfish:tool-progress", onProgress);
    return () => window.removeEventListener("catfish:tool-progress", onProgress);
  }, []);

  // 一轮结束: 正文 + 工具结果整体再看一遍
  const wasStreaming = useRef(isStreaming);
  useEffect(() => {
    const ended = wasStreaming.current && !isStreaming;
    wasStreaming.current = isStreaming;
    if (!ended) return;
    const round = lastRoundArtifacts(latest.current);
    if (!round) return;
    const delivered = lastAnswerOutputs(latest.current);
    const seq = ++checkSeq.current;
    void (async () => {
      // 先找这一轮新写的; 没有就看最后回答里交出来的成果 (之前做好、这轮原样交付的)
      const p = (await freshArtifact(round.paths, round.since)) ?? (await freshArtifact(delivered, 0));
      if (!p || !mounted.current || seq < appliedSeq.current) return;
      appliedSeq.current = seq;
      useArtifactStore.getState().autoOpen(p, round.since);
    })();
  }, [isStreaming]);

  const prevSession = useRef(sessionId);
  useEffect(() => {
    if (prevSession.current && prevSession.current !== sessionId) useArtifactStore.getState().dismiss();
    prevSession.current = sessionId;
  }, [sessionId]);
}
