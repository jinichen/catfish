/** 对话里的 A2UI 卡片 (11/15 试点) —— 解析、缺组件检查、提交那句话的来回。 */
import { describe, expect, it } from "vitest";

import {
  blockedFunctions, buildRetryRequest, buildSubmission, cardKey, cardRetryAllowed, parseRetryRequest, cardTitle, missingComponents, unknownComponents, parseCardSource, parseSubmission, submissionLines, submittedCardKeys,
} from "./a2uiCard";

const create = { version: "1.0", createSurface: { surfaceId: "jjh", catalogId: "catfish:cards/v1" } };
const comps = (components: unknown[]) => ({ version: "1.0", updateComponents: { surfaceId: "jjh", components } });

describe("parseCardSource", () => {
  it("JSON 数组 / 单个对象 / 一行一个都认", () => {
    expect(parseCardSource(JSON.stringify([create]))).toEqual({ ok: true, messages: [create] });
    expect(parseCardSource(JSON.stringify(create))).toEqual({ ok: true, messages: [create] });
    expect(parseCardSource(`${JSON.stringify(create)}\n${JSON.stringify(comps([]))}`)).toEqual({ ok: true, messages: [create, comps([])] });
  });

  it("坏的说原因 (流式输出到一半就是这样)", () => {
    const r = parseCardSource('[{"version":"1.0","createSurface":{"surf');
    expect(r.ok).toBe(false);
    expect(parseCardSource("   ")).toEqual({ ok: false, reason: "代码块是空的" });
    expect(parseCardSource("[]")).toEqual({ ok: false, reason: "消息列表是空的" });
    const big = JSON.stringify([create, comps(Array.from({ length: 2000 }, (_, i) => ({ id: `t${i}`, component: "Text", text: "很长的一段字" })))]);
    expect(parseCardSource(big)).toMatchObject({ ok: false, reason: expect.stringContaining("卡片太大") });
  });
});

describe("unknownComponents", () => {
  it("白名单外的 (Image / Video / Modal / 编的) 都报; 白名单里的不报", () => {
    expect(unknownComponents([create, comps([{ id: "root", component: "Card", child: "a" }, { id: "a", component: "Image", url: "https://x" },
      { id: "b", component: "Modal" }, { id: "c", component: "Fancy" }])])).toEqual(["Image", "Modal", "Fancy"]);
    expect(unknownComponents([create, comps([{ id: "root", component: "TextField", label: "x", value: "" }])])).toEqual([]);
  });
});

describe("blockedFunctions", () => {
  it("按钮里调 openUrl (打开任意网址) 要拦; 校验函数不拦", () => {
    const btn = { id: "root", component: "Button", child: "l", action: { functionCall: { "@call": "openUrl", args: { url: "https://x" } } } };
    expect(blockedFunctions([create, comps([btn])])).toEqual(["openUrl"]);
    const tf = { id: "root", component: "TextField", label: "x", value: "", checks: [{ condition: { "@call": "required", args: { value: "" } } }] };
    expect(blockedFunctions([create, comps([tf])])).toEqual([]);
  });
});

describe("missingComponents", () => {
  it("children 引用了没定义的 → 报出来 (11/15 实测模型漏过整行)", () => {
    const msgs = [create, comps([
      { id: "root", component: "Card", child: "body" },
      { id: "body", component: "Column", children: ["title", "row3"] },
      { id: "title", component: "Text", text: "x" },
    ])];
    expect(missingComponents(msgs)).toEqual(["row3"]);
  });

  it("齐全 → 空; 没有 root → 报 root", () => {
    expect(missingComponents([create, comps([{ id: "root", component: "Text", text: "hi" }])])).toEqual([]);
    expect(missingComponents([create, comps([{ id: "a", component: "Text", text: "hi" }])])).toEqual(["root"]);
  });
});

describe("提交那句话", () => {
  const sub = { title: "记一条爆款", key: "k1", name: "jjh_record", surfaceId: "jjh",
    context: { record: { 视频链接: "https://www.douyin.com/video/1", 点赞: "3.2万", 标签: ["痛点型"], 评论: "" } } };

  it("拼出来再认回去, 一字不差", () => {
    const text = buildSubmission(sub);
    expect(text.startsWith("（填好了卡片「记一条爆款」）")).toBe(true);
    expect(parseSubmission(text)).toEqual(sub);
  });

  it("员工自己打的话不当成提交", () => {
    expect(parseSubmission("记一条爆款")).toBeNull();
    expect(parseSubmission("（填好了卡片「x」） 但是没有代码块")).toBeNull();
  });

  it("会话里交过的卡片 key; 气泡上只列填了的", () => {
    const keys = submittedCardKeys([
      { role: "assistant", content: buildSubmission({ ...sub, key: "not-user" }) },
      { role: "user", content: buildSubmission(sub) },
      { role: "user", content: "普通的话" },
    ]);
    expect([...keys]).toEqual(["k1"]);
    expect(submissionLines(sub.context)).toEqual([["视频链接", "https://www.douyin.com/video/1"], ["点赞", "3.2万"], ["标签", "痛点型"]]);
  });
});

describe("cardKey / cardTitle", () => {
  it("同样的内容同一个 key (前后空白不算), 不同内容不同 key", () => {
    expect(cardKey(" abc \n")).toBe(cardKey("abc"));
    expect(cardKey("abc")).not.toBe(cardKey("abd"));
  });

  it("标题取第一个 Text, 去掉粗体和「· 后半句」", () => {
    const msgs = [create, comps([{ id: "root", component: "Card", child: "t" }, { id: "t", component: "Text", text: "**记一条爆款** · 写进「爆款记录」" }])];
    expect(cardTitle(msgs, "卡片")).toBe("记一条爆款");
    expect(cardTitle([create], "卡片")).toBe("卡片");
  });
});

describe("画不出来 → 自动让小鲶重出 (每张卡一次, 不连着要)", () => {
  const card = (id: string) => ({ id, role: "assistant", content: "```a2ui\n坏的\n```" });
  const user = (id: string, content: string) => ({ id, role: "user", content });

  it("那句话拼出来再认回去", () => {
    const text = buildRetryRequest("k9", "卡片缺了一部分 (row3)");
    expect(text.startsWith("（卡片没画出来）")).toBe(true);
    expect(parseRetryRequest(text)).toEqual({ key: "k9", reason: "卡片缺了一部分 (row3)" });
    expect(parseRetryRequest("（卡片没画出来）是什么意思")).toEqual({ key: "", reason: "是什么意思" });
    expect(parseRetryRequest("普通的话")).toBeNull();
  });

  it("最后一条、员工上一句是正常的话 → 可以要", () => {
    expect(cardRetryAllowed([user("u1", "帮我约会"), card("a1")], "a1", "k1")).toBe(true);
  });

  it("员工已经接着说话了 (卡片不是最后一条) → 不要", () => {
    expect(cardRetryAllowed([user("u1", "x"), card("a1"), user("u2", "算了")], "a1", "k1")).toBe(false);
  });

  it("这张已经要过一次 / 上一句就是「请重出」(重出的又坏了) → 不要, 不连着要", () => {
    expect(cardRetryAllowed([user("u1", "x"), card("a1"), user("u2", buildRetryRequest("k1", "坏")), card("a2")], "a2", "k2")).toBe(false);
    expect(cardRetryAllowed([user("u2", buildRetryRequest("k1", "坏")), card("a2")], "a2", "k1")).toBe(false);
  });
});
