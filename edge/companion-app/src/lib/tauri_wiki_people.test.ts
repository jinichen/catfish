import { describe, expect, it } from "vitest";

import { applySuggestion, filledEmails, lastToken, suggestPeople, type WikiPerson } from "./tauri_wiki_people";

const P: WikiPerson[] = [
  { name: "迟小璐", aliases: [], email: "cxl@ex.cn", sources: ["对照"] },
  { name: "陈秀平", aliases: ["秀平"], email: "cxp@ex.cn", sources: ["对照"] },
  { name: "张三", aliases: ["张总"], email: "zhang@ex.cn", sources: ["张三"] },
];

describe("收件人候选", () => {
  it("lastToken / filledEmails", () => {
    expect(lastToken("a@x.cn, 迟")).toBe("迟");
    expect(lastToken("a@x.cn；陈")).toBe("陈");
    expect([...filledEmails("A@X.cn, b@y.cn,")]).toEqual(["a@x.cn", "b@y.cn"]);
  });
  it("一个汉字就给候选, 名字开头的排前面, 已填的不给, 别名 / 邮箱也能搜", () => {
    expect(suggestPeople(P, "陈").map((p) => p.name)).toEqual(["陈秀平"]);
    expect(suggestPeople(P, "cxp@ex.cn, 陈")).toEqual([]);
    expect(suggestPeople(P, "张总").map((p) => p.name)).toEqual(["张三"]);
    expect(suggestPeople(P, "zh").map((p) => p.email)).toEqual(["zhang@ex.cn"]);
    expect(suggestPeople(P, "z")).toEqual([]);
    expect(suggestPeople(P, "")).toEqual([]);
  });
  it("选了候选: 换掉正在打的那段, 补「, 」", () => {
    expect(applySuggestion("迟", "cxl@ex.cn")).toBe("cxl@ex.cn, ");
    expect(applySuggestion("a@x.cn, 陈", "cxp@ex.cn")).toBe("a@x.cn, cxp@ex.cn, ");
    expect(applySuggestion("a@x.cn；陈", "cxp@ex.cn")).toBe("a@x.cn, cxp@ex.cn, ");
  });
});
