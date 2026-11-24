import { describe, expect, it } from "vitest";
import { extractFilePaths } from "./path_detect";

describe("extractFilePaths", () => {
  it("反引号里带空格的路径整段取出 (11/24 ITSS 简介.docx 右栏没弹)", () => {
    const text = "已保存到 `/Users/a/.catfish/outputs/2026-11-24/ITSS 简介.docx`（39,967 字节）";
    expect(extractFilePaths(text)).toEqual(["/Users/a/.catfish/outputs/2026-11-24/ITSS 简介.docx"]);
  });
  it("JSON 双引号 / 「」里带空格的也行", () => {
    expect(extractFilePaths('{"path": "/tmp/q 3 报告.xlsx"}')).toEqual(["/tmp/q 3 报告.xlsx"]);
    expect(extractFilePaths("文件「~/Desktop/年度 总结.pptx」好了")).toEqual(["~/Desktop/年度 总结.pptx"]);
  });
  it("没包起来的照旧到空格为止, 不误吞后面的字", () => {
    expect(extractFilePaths("见 /tmp/a.docx 和 /tmp/b.pdf 两份")).toEqual(["/tmp/a.docx", "/tmp/b.pdf"]);
  });
});
