import { describe, expect, it } from "vitest";
import { cdpModifiers, planKey } from "./cdp";

const ev = (key: string, o: Partial<{ code: string; altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; isComposing: boolean }> = {}) =>
  ({ key, code: o.code ?? "", altKey: false, ctrlKey: false, metaKey: false, shiftKey: false, ...o });

describe("planKey", () => {
  it("普通文字 / 空格 / 输入法拼字中 → 交给 insertText (null)", () => {
    expect(planKey(ev("a"), true)).toBeNull();
    expect(planKey(ev("鲶"), true)).toBeNull();
    expect(planKey(ev(" "), true)).toBeNull();
    expect(planKey(ev("Enter", { isComposing: true }), true)).toBeNull(); // 输入法里按回车是选词, 不是提交
    expect(planKey(ev("Process"), false)).toBeNull();
  });

  it("Enter 带 text (表单才会提交); 退格 / 方向键带虚拟键码", () => {
    expect(planKey(ev("Enter", { code: "Enter" }), true)!.down).toMatchObject({ type: "keyDown", text: "\r", windowsVirtualKeyCode: 13 });
    expect(planKey(ev("Backspace"), true)!.down).toMatchObject({ type: "rawKeyDown", windowsVirtualKeyCode: 8 });
    expect(planKey(ev("ArrowLeft", { shiftKey: true }), true)!.down).toMatchObject({ windowsVirtualKeyCode: 37, modifiers: 8 });
    expect(planKey(ev("Backspace"), true)!.up).toMatchObject({ type: "keyUp" });
  });

  it("mac ⌘ / Windows Ctrl 组合键带编辑命令 (全选 / 复制 / 粘贴)", () => {
    expect(planKey(ev("a", { metaKey: true, code: "KeyA" }), true)!.down).toMatchObject({ commands: ["selectAll"], modifiers: 4, windowsVirtualKeyCode: 65 });
    expect(planKey(ev("v", { ctrlKey: true }), false)!.down).toMatchObject({ commands: ["paste"], modifiers: 2 });
    expect(planKey(ev("r", { metaKey: true }), true)!.down).not.toHaveProperty("commands");
    // mac 上 Ctrl+A 不是全选 (是行首), 不当命令转
    expect(planKey(ev("a", { ctrlKey: true }), true)).toBeNull();
  });

  it("修饰键位", () => {
    expect(cdpModifiers({ altKey: true, ctrlKey: true, metaKey: true, shiftKey: true })).toBe(15);
  });
});
