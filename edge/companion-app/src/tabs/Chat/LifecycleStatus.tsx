/**
 * 对话进行中的状态条 (10/15): hermes 的英文状态翻成中文 + 已等多久。
 *
 * 鸿波 10/15: 压缩上下文那句是英文, 而且一压就是一两分钟、没别的动静, 让人焦虑。
 * 这里: 文案走 hermesStatusZh (认不出的英文不直接给人看, 原文放 title 里); 同一件事持续时
 * 显示"已 N 秒" —— hermes 压缩时每隔一阵会重发"还在压缩", 翻出来文字相同, 计时不归零。
 */
import { useEffect, useRef, useState } from "react";

import { translateHermesStatus } from "../../lib/hermesStatusZh";

export default function LifecycleStatus({ raw }: { raw: string }) {
  const view = translateHermesStatus(raw);
  const since = useRef<{ text: string; at: number }>({ text: view.text, at: Date.now() });
  if (since.current.text !== view.text) since.current = { text: view.text, at: Date.now() };
  const [, tick] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, []);
  const elapsed = Math.floor((Date.now() - since.current.at) / 1000);

  return (
    <div className="chat-panel__lifecycle" aria-live="polite" title={view.raw !== view.text ? view.raw : undefined}>
      <span className="chat-panel__lifecycle-spinner" aria-hidden="true" />
      <span className="chat-panel__lifecycle-body">
        <span>
          {view.text}
          {elapsed >= 5 && <span className="chat-panel__lifecycle-elapsed"> · 已 {elapsed} 秒</span>}
        </span>
        {view.hint && <span className="chat-panel__lifecycle-hint">{view.hint}</span>}
      </span>
    </div>
  );
}
