/** 钓鱼标注 (11/07): 按发件地址认; 标了马上按标注显示; 撤销后旧的标注结果先不显示 (等重扫)。 */
import { describe, expect, it } from "vitest";

import { markOf, sameSender, senderAddress, withMark } from "./phishingMarks";
import type { PhishingMarks, PhishingScanResult } from "./tauri_services";

const NIC = "信息安全中心 <FF_NIC@chinatelecom.cn>";
const flagged: PhishingScanResult = {
  messageId: "m1", scannedAt: "", highestSeverity: "high", llmVerdict: "suspicious",
  flags: [{ ruleId: "PHISH-001-display-name-mismatch", severity: "high", category: "sender_spoofing", reason: "", matchedText: null }],
} as PhishingScanResult;
const marks = (v: "safe" | "phishing"): PhishingMarks => ({ senders: { "ff_nic@chinatelecom.cn": { verdict: v, at: "", subject: "", name: "" } } });

describe("phishingMarks", () => {
  it("认发件地址 (大小写、显示名都不影响)", () => {
    expect(senderAddress(NIC)).toBe("ff_nic@chinatelecom.cn");
    expect(senderAddress("a@b.cn")).toBe("a@b.cn");
    expect(senderAddress("没有地址")).toBe("");
    expect(markOf(marks("safe"), "x <ff_nic@ChinaTelecom.cn>")).toBe("safe");
  });

  it("标过不是钓鱼: 不再报, 规则记录留着", () => {
    const r = withMark(flagged, NIC, marks("safe"))!;
    expect(r.highestSeverity).toBe("none");
    expect(r.userMark).toBe("safe");
    expect(r.flags).toHaveLength(1);
  });

  it("标过是钓鱼: 没扫过的也按钓鱼报", () => {
    expect(withMark(null, NIC, marks("phishing"))).toMatchObject({ highestSeverity: "high", userMark: "phishing" });
  });

  it("没标: 原样; 撤销了但结果还是标注时改的 → 先不显示, 等重扫", () => {
    expect(withMark(flagged, NIC, { senders: {} })).toBe(flagged);
    expect(withMark({ ...flagged, userMark: "safe", highestSeverity: "none" }, NIC, { senders: {} })).toBeNull();
    expect(withMark(undefined, NIC, null)).toBeNull();
  });

  it("同一个发件人的那几封", () => {
    const list = [{ id: "1", sender: NIC }, { id: "2", sender: "ff_nic@chinatelecom.cn" }, { id: "3", sender: "x@y.cn" }];
    expect(sameSender(list, NIC).map((x) => x.id)).toEqual(["1", "2"]);
  });
});
