import { describe, expect, it } from "vitest";
import { draftPrompt, filterClosedCards } from "./customerRadar";
import type { RadarCard } from "./tauri_sales";

const card = (rule: RadarCard["rule"], extra: Partial<RadarCard> = {}): RadarCard => ({
  uid: `cust:南岭建设:${rule}:2026-10-09`, rule, label: "", customer: "南岭建设", stage: "已报价",
  rel_path: "wiki/entities/南岭建设.md", quote: "能先试用吗?", age_days: 3, ...extra,
});

describe("customerRadar", () => {
  it("按 uid 过滤已关的卡, 别的卡不受影响", () => {
    const cards = [card("reply"), card("cold")];
    const out = filterClosedCards(cards, [{ status: "done", taskUid: cards[0].uid, titleKey: "", subjectKeys: [], ts: "" }]);
    expect(out.map((c) => c.rule)).toEqual(["cold"]);
  });

  it("起草请求带客户名、原话, 并且明确只起草不发送", () => {
    const p = draftPrompt(card("reply", { channel: "微信" }));
    expect(p).toContain("客户「南岭建设」在微信里问: “能先试用吗?”");
    expect(p).toContain("只起草不要替我发送");
    expect(draftPrompt(card("promise", { due: "2026-10-09", quote: "下周给部署方案" }))).toContain("catfish_customer done");
    expect(draftPrompt(card("cold", { quote: "" }))).not.toContain("对方上次说");
  });
});
