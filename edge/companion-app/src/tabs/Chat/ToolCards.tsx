/** 对话卡片 (A2UI, 11/15): 这条回复里「出卡片」的工具调用 → 按编号读出卡片画出来 (lib/a2uiCard.ts)。
 *
 * 小鲶调 catfish_form 的 card 动作, tool-bridge 把卡片存本机、只把编号还给它; 这里从工具调用的结果里取编号,
 * 经 Rust a2ui_card_read 读出 A2UI 消息, 再交给按需加载的 A2uiCard 画。hermes 跑的工具, 结果要等这轮结束
 * 从库里重读才进消息 (lib/chat.ts 11/07 注释) —— 所以卡片在这轮回答结束时出现。
 */
import { invoke } from "@tauri-apps/api/core";
import { lazy, Suspense, useContext, useEffect, useState } from "react";

import { cardIdFromToolCall } from "../../lib/a2uiCard";
import { A2uiCardContext } from "../../lib/a2uiCardContext";
import type { ToolCall } from "../../types/chat";

const A2uiCard = lazy(() => import("./A2uiCard"));

const note = {
  margin: "var(--space-2) 0", padding: "8px 12px", border: "1px solid var(--catfish-border)", borderRadius: 8,
  background: "var(--catfish-bg-elevated)", fontSize: 12, color: "var(--catfish-text-muted)",
} as const;

function OneCard({ cardId }: { cardId: string }) {
  const env = useContext(A2uiCardContext);
  const [state, setState] = useState<{ messages?: unknown[]; error?: string }>({});
  useEffect(() => {
    let alive = true;
    invoke<unknown[]>("a2ui_card_read", { cardId })
      .then((messages) => alive && setState({ messages }))
      .catch((e) => alive && setState({ error: e instanceof Error ? e.message : String(e) }));
    return () => { alive = false; };
  }, [cardId]);
  if (state.error) return <div style={note} role="note">{state.error}</div>;
  if (!state.messages) return <div style={note}>卡片加载中…</div>;
  return (
    <Suspense fallback={<div style={note}>卡片加载中…</div>}>
      <A2uiCard cardId={cardId} messages={state.messages} interactive={env.interactive} submitted={env.submitted.has(cardId)} />
    </Suspense>
  );
}

export default function ToolCards({ calls }: { calls?: ToolCall[] }) {
  const ids = (calls ?? []).map(cardIdFromToolCall).filter((x): x is string => x !== null);
  if (!ids.length) return null;
  return <>{ids.map((id) => <OneCard key={id} cardId={id} />)}</>;
}
