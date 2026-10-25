/** 加附件失败的提示 (10/25): 从微信直接拖进来的文件 macOS 不让读, 浏览器只报一句英文。 */
import { describe, expect, it } from "vitest";
import { attachErrorText } from "./attachmentHelpers";

const notReadable = Object.assign(new Error("The I/O read operation failed."), { name: "NotReadableError" });

describe("attachErrorText", () => {
  it("mac 上读不到文件内容: 说清原因和两个办法, 不再是那句英文", () => {
    const text = attachErrorText(notReadable, "macos");
    expect(text).toContain("微信");
    expect(text).toContain("另存为");
    expect(text).toContain("完全磁盘访问权限");
    expect(text).not.toContain("I/O");
  });

  it("Windows 上不提 macOS 的设置", () => {
    const text = attachErrorText(notReadable, "windows");
    expect(text).toContain("读不到文件内容");
    expect(text).not.toContain("完全磁盘访问权限");
  });

  it("别的错误照原话", () => {
    expect(attachErrorText(new Error("文件太大"), "macos")).toBe("文件太大");
    expect(attachErrorText("字符串错误", "macos")).toBe("字符串错误");
  });
});
