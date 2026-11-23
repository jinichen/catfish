/**
 * 右栏「待处理」(11/24 鸿波「A2UI 生成的内容是不是应该放在右边」): 这个会话里要填 / 大件的对话卡片。
 *
 * 待你确认的在上 (新的在前), 交过的收在「已处理」里 (点开才看)。对话里点「在右边看」/「看一眼」→ 滚到那张、闪一下。
 * 卡片怎么读、怎么画、交了发回哪里, 跟对话里一样 (ToolCards 的 useCardMessages / CardBody, 提交走 catfish:approval-send)。
 */
import { useEffect, useRef, useState } from "react";

import { useCardPanelStore, type PanelCard } from "../../../store/cardPanel";
import { CardBody, useCardMessages } from "../ToolCards";

function when(at: number): string {
  if (!at) return "";
  const d = new Date(at);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function PanelCardView({ card, flash }: { card: PanelCard; flash: number }) {
  const state = useCardMessages(card.cardId);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!flash || !ref.current) return;
    ref.current.scrollIntoView?.({ block: "start", behavior: "smooth" });
    ref.current.classList.remove("cards-pane__item--flash");
    void ref.current.offsetWidth; // 重新触发动画
    ref.current.classList.add("cards-pane__item--flash");
  }, [flash]);
  return (
    <div ref={ref} className="cards-pane__item" data-panel-card={card.cardId}>
      <div className="cards-pane__meta">{when(card.at)}{card.submitted ? " · 已交给小鲶" : " · 待你确认"}</div>
      {state.error ? (
        <div className="cards-pane__note" role="note">{state.error}</div>
      ) : !state.messages ? (
        <div className="cards-pane__note">卡片加载中…</div>
      ) : (
        <CardBody cardId={card.cardId} messages={state.messages} interactive={card.interactive} submitted={card.submitted} />
      )}
    </div>
  );
}

export default function CardsPane() {
  const cards = useCardPanelStore((s) => s.cards);
  const focus = useCardPanelStore((s) => s.focus);
  const pending = cards.filter((c) => !c.submitted);
  const done = cards.filter((c) => c.submitted);
  const [showDone, setShowDone] = useState(false);
  // 点的是交过的那张: 把「已处理」展开
  useEffect(() => {
    if (focus && done.some((c) => c.cardId === focus.cardId)) setShowDone(true);
  }, [focus, done]);
  const flashOf = (id: string) => (focus?.cardId === id ? focus.n : 0);

  return (
    <div className="cards-pane">
      {cards.length === 0 && (
        <div className="cards-pane__empty">
          这个对话里还没有要你填的卡片。小鲶出了要填 / 要选的卡片会放到这里, 对话里只留一行, 不会被后面的消息顶上去。
        </div>
      )}
      {pending.map((c) => <PanelCardView key={c.cardId} card={c} flash={flashOf(c.cardId)} />)}
      {pending.length === 0 && cards.length > 0 && <div className="cards-pane__empty">都处理完了 ✓</div>}
      {done.length > 0 && (
        <div className="cards-pane__done">
          <button type="button" className="cards-pane__toggle" onClick={() => setShowDone((v) => !v)} aria-expanded={showDone}>
            {showDone ? "▾" : "▸"} 已处理 ({done.length})
          </button>
          {showDone && done.map((c) => <PanelCardView key={c.cardId} card={c} flash={flashOf(c.cardId)} />)}
        </div>
      )}
    </div>
  );
}
