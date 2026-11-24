/** 对话卡片 (A2UI, 11/15): 这条回复里「出卡片」的工具调用 → 按编号读出卡片画出来 (lib/a2uiCard.ts)。
 *
 * 小鲶调 catfish_form 的 card 动作, tool-bridge 把卡片存本机、只把编号还给它; 这里从工具调用的结果里取编号,
 * 经 Rust a2ui_card_read 读出 A2UI 消息, 再交给按需加载的 A2uiCard 画。hermes 跑的工具, 结果要等这轮结束
 * 从库里重读才进消息 (lib/chat.ts 11/07 注释) —— 所以卡片在这轮回答结束时出现。
 *
 * 小鲶用 card_update 改了已有的卡: 这里不再画一张, 只留一行小字, 并通知原来那张卡重读文件、把新数据打上去。
 *
 * 11/24 鸿波「A2UI 生成的内容是不是应该放在右边」: 对话页里, 要填 / 大件的卡片 (cardPlacement = panel) 画在右栏
 * 「待处理」(artifacts/CardsPane), 对话里只留一行「📋 标题 · 待你确认 → 在右边看」; 点一下就完的小卡照旧画在这里。
 * 早安页 / 定时任务结果没有右栏 (env.panel 不开), 照旧画在原地。
 */
import { invoke } from "@tauri-apps/api/core";
import { lazy, Suspense, useContext, useEffect, useState } from "react";
import { ClipboardText } from "@phosphor-icons/react";
import { Ico } from "../../components/icons";

import { CARD_UPDATED_EVENT, cardIdFromToolCall, cardPlacement, cardTitle, cardUpdateFromToolCall } from "../../lib/a2uiCard";
import { A2uiCardContext } from "../../lib/a2uiCardContext";
import { useArtifactStore } from "../../store/artifacts";
import { cardTime, useCardPanelStore } from "../../store/cardPanel";
import type { ToolCall } from "../../types/chat";

const A2uiCard = lazy(() => import("./A2uiCard"));

const note = {
  margin: "var(--space-2) 0", padding: "8px 12px", border: "1px solid var(--catfish-border)", borderRadius: 8,
  background: "var(--catfish-bg-elevated)", fontSize: 12, color: "var(--catfish-text-muted)",
} as const;

/** 新出来 (或刚改过) 的卡才自动打开右栏: 翻老会话时不弹 */
const FRESH_MS = 30 * 60 * 1000;

/** 按编号读卡片文件; 小鲶改了 (CARD_UPDATED_EVENT) 就重读 */
export function useCardMessages(cardId: string): { messages?: unknown[]; error?: string } {
  const [state, setState] = useState<{ messages?: unknown[]; error?: string }>({});
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const onUpdated = (e: Event) => {
      if ((e as CustomEvent<{ cardId?: string }>).detail?.cardId === cardId) setVersion((v) => v + 1);
    };
    window.addEventListener(CARD_UPDATED_EVENT, onUpdated);
    return () => window.removeEventListener(CARD_UPDATED_EVENT, onUpdated);
  }, [cardId]);
  useEffect(() => {
    let alive = true;
    invoke<unknown[]>("a2ui_card_read", { cardId })
      .then((messages) => alive && setState({ messages }))
      .catch((e) => alive && setState({ error: e instanceof Error ? e.message : String(e) }));
    return () => { alive = false; };
  }, [cardId, version]);
  return state;
}

/** 画一张卡 (对话里、右栏里共用) */
export function CardBody({ cardId, messages, interactive, submitted }: { cardId: string; messages: unknown[]; interactive: boolean; submitted: boolean }) {
  return (
    <Suspense fallback={<div style={note}>卡片加载中…</div>}>
      <A2uiCard cardId={cardId} messages={messages} interactive={interactive} submitted={submitted} />
    </Suspense>
  );
}

export const openCardInPanel = (cardId: string) => {
  useArtifactStore.getState().showView("cards");
  useCardPanelStore.getState().setFocus(cardId);
};

/** 卡片放到右栏了: 对话里留的那一行 */
function CardStub({ cardId, title, submitted }: { cardId: string; title: string; submitted: boolean }) {
  return (
    <div className="a2ui-card-stub" data-card-stub={cardId} style={{ ...note, display: "flex", alignItems: "center", gap: 8, color: "var(--catfish-text)", maxWidth: 640 }}>
      <Ico as={ClipboardText} gap={0} />
      <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {title}
        <span style={{ color: "var(--catfish-text-muted)" }}> · {submitted ? "已交给小鲶 ✓" : "待你确认"}</span>
      </span>
      <button type="button" onClick={() => openCardInPanel(cardId)}
        style={{ border: "1px solid var(--catfish-border)", borderRadius: 6, background: "transparent", color: "var(--catfish-cyan)", fontSize: 12, padding: "2px 10px", cursor: "pointer", whiteSpace: "nowrap" }}>
        在右边看 →
      </button>
    </div>
  );
}

function OneCard({ cardId }: { cardId: string }) {
  const env = useContext(A2uiCardContext);
  const state = useCardMessages(cardId);
  const submitted = env.submitted.has(cardId);
  const toPanel = !!env.panel && !!state.messages && cardPlacement(state.messages) === "panel";
  const title = state.messages ? cardTitle(state.messages, "卡片") : "";
  useEffect(() => {
    if (!toPanel) return;
    const at = cardTime(cardId);
    useCardPanelStore.getState().register({ cardId, title, submitted, interactive: env.interactive, at });
    if (!submitted && at > Date.now() - FRESH_MS) useArtifactStore.getState().autoCards(at);
  }, [toPanel, cardId, title, submitted, env.interactive]);
  if (state.error) return <div style={note} role="note">{state.error}</div>;
  if (!state.messages) return <div style={note}>卡片加载中…</div>;
  if (toPanel) return <CardStub cardId={cardId} title={title} submitted={submitted} />;
  return <CardBody cardId={cardId} messages={state.messages} interactive={env.interactive} submitted={submitted} />;
}

function CardUpdated({ cardId, revision }: { cardId: string; revision: number }) {
  const inPanel = useCardPanelStore((s) => s.cards.find((c) => c.cardId === cardId));
  const fresh = !!inPanel && !inPanel.submitted && inPanel.at > Date.now() - FRESH_MS;
  useEffect(() => {
    window.dispatchEvent(new CustomEvent(CARD_UPDATED_EVENT, { detail: { cardId, revision } }));
  }, [cardId, revision]);
  useEffect(() => {
    // 小鲶改了右栏里这一轮的卡: 切过去、闪一下, 让员工看到
    if (!fresh) return;
    useArtifactStore.getState().autoCards(Date.now() - 60_000);
    useCardPanelStore.getState().setFocus(cardId);
  }, [fresh, cardId, revision]);
  const line = { ...note, padding: "2px 10px", border: "none", background: "none" };
  if (inPanel) {
    return (
      <div style={line}>
        → 更新了右边的卡片「{inPanel.title}」{" "}
        <button type="button" onClick={() => openCardInPanel(cardId)}
          style={{ border: "none", background: "none", color: "var(--catfish-cyan)", cursor: "pointer", fontSize: 12, padding: 0 }}>
          看一眼
        </button>
      </div>
    );
  }
  return <div style={line}>↑ 更新了上面的卡片</div>;
}

export default function ToolCards({ calls }: { calls?: ToolCall[] }) {
  const items = (calls ?? []).flatMap((c) => {
    const id = cardIdFromToolCall(c);
    if (id) return [<OneCard key={id} cardId={id} />];
    const up = cardUpdateFromToolCall(c);
    return up ? [<CardUpdated key={`${up.cardId}@${up.revision}`} cardId={up.cardId} revision={up.revision} />] : [];
  });
  return items.length ? <>{items}</> : null;
}
