/**
 * 10/8「期中固定资产盘点整改」关了又出来 —— 已关闭台账的三把钥匙。
 *
 * 用的标题/主题是那次真实卡片的形状 (虚构化处理过的公司名不涉及, 只有通用业务词)。
 */
import { describe, expect, it } from "vitest";
import {
  filterClosedTasks,
  foldLedger,
  matchClosed,
  normSubject,
  normTitle,
  subjectFromRef,
  type ClosedLedgerEntry,
} from "./advisor_closed";
import type { AdvisorResult, MainTask } from "./briefing_advisor_common";

const REF_2890 =
  "邮件：【提醒】【整改工作要求】 回复: 关于开展2026年期中固定资产（含无形资产）盘点工作的通知（邮件 ID: apple_mail|Corp|2890；时间: 2026-10-08T00:37:53+00:00）";
const REF_2704 =
  "邮件：【整改工作要求】 回复: 关于开展2026年期中固定资产（含无形资产）盘点工作的通知（邮件 ID: apple_mail|Corp|2704；时间: 2026-09-23T02:00:00+00:00）";

function card(taskUid: string, title: string, contextRefs: string[] = []): MainTask {
  return {
    id: 1, taskUid, title, urgency: "medium", options: [],
    complianceFlags: [], politicalFlags: [], contextRefs,
  };
}
function result(...tasks: MainTask[]): AdvisorResult {
  return { mainTasks: tasks, handledSilently: [] } as unknown as AdvisorResult;
}
const NOW = new Date("2026-10-09T01:00:00Z");
const close = (e: Partial<ClosedLedgerEntry>): ClosedLedgerEntry => ({
  op: "close", status: "done", ts: "2026-10-08T04:40:00Z", by: "chat", ...e,
});

describe("normTitle (跟 tool-bridge advisor_close._norm_title 同一组期望值)", () => {
  it("去括号内容 + 标点空白, 小写", () => {
    expect(normTitle("期中固定资产（含无形资产）盘点整改工作要求")).toBe("期中固定资产盘点整改工作要求");
    expect(normTitle("【提醒】ITSS 智能-运维, 项目!")).toBe("itss智能运维项目");
  });
});

describe("subject 规范化", () => {
  it("剥掉【提醒】/回复: 前缀, 主题里的全角括号不当截断点", () => {
    expect(subjectFromRef(REF_2890)).toBe(
      "【提醒】【整改工作要求】 回复: 关于开展2026年期中固定资产（含无形资产）盘点工作的通知",
    );
    expect(normSubject(subjectFromRef(REF_2890))).toBe(normSubject(subjectFromRef(REF_2704)));
  });
  it("不是邮件引用 → 空", () => {
    expect(subjectFromRef("日程：周一例会")).toBe("");
  });
});

describe("matchClosed 三把钥匙", () => {
  const closures = foldLedger(
    [close({ taskUid: "f3g7h9", title: "期中固定资产（含无形资产）盘点整改工作要求", refs: [REF_2890] })],
    NOW,
  );
  it("uid", () => {
    expect(matchClosed(card("f3g7h9", "随便"), closures)?.by).toBe("uid");
  });
  it("换 uid + 标题改写 (去括号后包含)", () => {
    expect(matchClosed(card("zz9999", "期中固定资产盘点整改"), closures)?.by).toBe("title");
  });
  it("换 uid + 换标题, 但来自同一条邮件线 (9/23 原通知)", () => {
    expect(matchClosed(card("qq1111", "存放地J列填报", [REF_2704]), closures)?.by).toBe("subject");
  });
  it("无关卡不误伤 (短标题不做包含匹配)", () => {
    expect(matchClosed(card("aa0000", "ITSS 智能运维项目"), closures)).toBeNull();
    expect(matchClosed(card("aa0001", "盘点"), closures)).toBeNull();
  });
});

describe("foldLedger", () => {
  it("reopen 撤销 close (按标题, 即使 uid 已换)", () => {
    const rows = [
      close({ taskUid: "u1", title: "期中固定资产盘点整改工作要求" }),
      { op: "reopen", taskUid: "u2", title: "期中固定资产盘点整改工作要求", ts: "2026-10-08T05:00:00Z" } as ClosedLedgerEntry,
    ];
    expect(foldLedger(rows, NOW)).toEqual([]);
  });
  it("snoozed 过了 until 失效; done 过 30 天失效", () => {
    const rows = [
      close({ taskUid: "s1", title: "今天先不看的事情项目", status: "snoozed", until: "2026-10-09T00:00:00Z" }),
      close({ taskUid: "d1", title: "很久以前完成的事情项目", ts: "2026-08-01T00:00:00Z" }),
      close({ taskUid: "d2", title: "刚完成的事情项目名称" }),
    ];
    expect(foldLedger(rows, NOW).map((c) => c.taskUid)).toEqual(["d2"]);
  });
  it("坏行 (缺 op / 缺钥匙) 跳过", () => {
    const rows = [{ ts: "x" }, { op: "close", ts: "2026-10-08T00:00:00Z" }] as ClosedLedgerEntry[];
    expect(foldLedger(rows, NOW)).toEqual([]);
  });
});

describe("filterClosedTasks", () => {
  it("命中的挪进已处理, 其余重排 id", () => {
    const closures = foldLedger([close({ taskUid: "f3g7h9", title: "期中固定资产盘点整改工作要求", refs: [REF_2890] })], NOW);
    const out = filterClosedTasks(
      result(card("x1", "ITSS 运维服务能力成熟度一级"), card("new123", "期中固定资产（含无形资产）盘点整改", [REF_2890])),
      closures,
    );
    expect(out.mainTasks.map((t) => [t.id, t.taskUid])).toEqual([[1, "x1"]]);
    expect(out.handledSilently).toHaveLength(1);
  });
});
