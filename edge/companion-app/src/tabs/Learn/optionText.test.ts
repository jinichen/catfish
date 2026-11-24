import { describe, expect, it } from "vitest";

import { optionText } from "./QuestionCard";

describe("optionText: 选项文字开头重复的编号去掉", () => {
  it("「a) …」「A. …」「（b）…」「c、…」跟 value 一样就去掉", () => {
    expect(optionText("a", "a) 直接买一等座, 全额报销")).toBe("直接买一等座, 全额报销");
    expect(optionText("a", "A. 直接买一等座")).toBe("直接买一等座");
    expect(optionText("b", "（b）买机票经济舱")).toBe("买机票经济舱");
    expect(optionText("c", "c、买一等座")).toBe("买一等座");
  });
  it("编号跟 value 不一样、或正文本来就以字母开头, 不动", () => {
    expect(optionText("a", "b) 别的")).toBe("b) 别的");
    expect(optionText("a", "API 网关要先配好")).toBe("API 网关要先配好");
    expect(optionText("d", "直接写的内容")).toBe("直接写的内容");
  });
});
