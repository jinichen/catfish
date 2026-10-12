/** 早安「客户跟进」卡片的纯逻辑: 过滤已关的卡、拼「让小鲶起草」的请求 (10/12, SALES-FOLLOWUP-PLAN §3)。 */
import type { ActiveClosure } from "./advisor_closed";
import type { RadarCard } from "./tauri_sales";

/** 关卡复用早安「关闭账本」, 按卡片 uid 匹配 (uid 由 customer_radar.py 生成, 同一件事稳定不变)。 */
export function filterClosedCards(cards: RadarCard[], closures: ActiveClosure[]): RadarCard[] {
  const closed = new Set(closures.map((c) => c.taskUid).filter(Boolean));
  return cards.filter((c) => !closed.has(c.uid));
}

const TAIL = "先用 catfish_customer get 看这个客户的档案和最近往来; 用我的口吻起草, 只起草不要替我发送。";

/** 每类卡片交给小鲶的请求。原话照引, 让小鲶有依据、员工能核对。 */
export function draftPrompt(c: RadarCard): string {
  const who = `客户「${c.customer}」`;
  switch (c.rule) {
    case "reply":
      return `${who}在${c.channel ?? ""}里问: “${c.quote}”(${c.age_days} 天前), 我还没回。帮我起草回复。${TAIL}`;
    case "promise":
      return `我答应${who}的事到期了: “${c.quote}”(截止 ${c.due ?? ""})。帮我看看现在怎么兑现, 需要的话起草给客户的消息; 兑现后用 catfish_customer done 标记。${TAIL}`;
    case "quote":
      return `给${who}的报价“${c.quote}”发出 ${c.age_days} 天没回音。起草一条跟进消息: 不施压, 给对方一个容易回答的问题。${TAIL}`;
    case "cold":
      return `${who}(${c.stage ?? ""}) ${c.age_days} 天没动静了${c.quote ? `, 对方上次说: “${c.quote}”` : ""}。起草一条自然的跟进消息, 再建议下一步。${TAIL}`;
  }
}
