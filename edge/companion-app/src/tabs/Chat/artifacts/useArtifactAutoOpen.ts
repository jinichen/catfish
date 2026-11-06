/**
 * 成果区跟着对话走 (11/07):
 *   - 一轮回答结束 (isStreaming true → false), 这一轮提到的文件里, 修改时间在这一轮开始之后的
 *     (= 这一轮真写出来的) 自动在右边打开, 取最后提到的那个。小鲶只是读了 / 引用了的老文件不弹。
 *   - 换到别的对话 / 开新对话, 右栏关掉 (上一段对话的成果不该挂在新对话旁边)。
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

export function useArtifactAutoOpen(messages: ChatMessage[], isStreaming: boolean, sessionId: string | null) {
  const wasStreaming = useRef(isStreaming);
  const latest = useRef(messages);
  latest.current = messages;

  useEffect(() => {
    const ended = wasStreaming.current && !isStreaming;
    wasStreaming.current = isStreaming;
    if (!ended) return;
    const round = lastRoundArtifacts(latest.current);
    if (!round) return;
    let cancelled = false;
    void freshArtifact(round.paths, round.since).then((p) => {
      if (p && !cancelled) useArtifactStore.getState().openArtifact(p);
    });
    return () => {
      cancelled = true;
    };
  }, [isStreaming]);

  const prevSession = useRef(sessionId);
  useEffect(() => {
    if (prevSession.current && prevSession.current !== sessionId) useArtifactStore.getState().close();
    prevSession.current = sessionId;
  }, [sessionId]);
}
