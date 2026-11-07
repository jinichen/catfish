/**
 * 成果区跟着对话走 (11/07):
 *   - 回答进行中: 小鲶每做完一步 (工具调用完成), 这一步写出的这一轮新文件马上在右边打开;
 *     一轮里写了好几个, 右栏跟到最新的那个。只看已完成的工具结果 —— 正文还在一个字一个字出来, 路径可能只有半截。
 *   - 一轮结束: 再按回答正文 + 工具结果整体看一遍, 打开最后提到的新文件 (正文里才提到的也能接住)。
 *   - 「新文件」= 修改时间在这一轮开始之后; 小鲶只是读了 / 引用了的老文件不弹。
 *   - 这一轮里用户自己开过 / 切过 / 关过右栏, 自动打开就不再抢 (store.autoOpen 判断)。
 *   - 回答进行中小鲶用上了浏览器工具 (名字里带 browser 的, 如 catfish_browser_goto), 切到「浏览器」页看它操作;
 *     之后写出新文件照样切回「成果」。
 *   - 换到别的对话 / 开新对话, 右栏收起 (上一段对话的成果不该挂在新对话旁边)。
 */
import { useEffect, useRef } from "react";
import type { ChatMessage } from "../../../types/chat";
import { lastRoundArtifacts } from "../../../lib/artifactKinds";
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

  // 一轮结束: 正文 + 工具结果整体再看一遍
  const wasStreaming = useRef(isStreaming);
  useEffect(() => {
    const ended = wasStreaming.current && !isStreaming;
    wasStreaming.current = isStreaming;
    if (!ended) return;
    const round = lastRoundArtifacts(latest.current);
    if (!round) return;
    const seq = ++checkSeq.current;
    void freshArtifact(round.paths, round.since).then((p) => {
      if (!p || !mounted.current || seq < appliedSeq.current) return;
      appliedSeq.current = seq;
      useArtifactStore.getState().autoOpen(p, round.since);
    });
  }, [isStreaming]);

  const prevSession = useRef(sessionId);
  useEffect(() => {
    if (prevSession.current && prevSession.current !== sessionId) useArtifactStore.getState().dismiss();
    prevSession.current = sessionId;
  }, [sessionId]);
}
