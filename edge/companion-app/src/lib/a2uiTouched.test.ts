// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { hasInvalid, touchAll, touchedHost } from "./a2uiTouched";

function card(html: string): HTMLElement {
  const el = document.createElement("div");
  el.className = "a2ui-card";
  el.innerHTML = html;
  document.body.appendChild(el);
  return el;
}

describe("卡片检查提示: 碰过才显示 (11/25)", () => {
  it("点到单选项, 记在它所在的 a2ui 组件上", () => {
    const el = card(`<a2ui-choicepicker><label class="a2ui-option-label"><input type="radio"></label></a2ui-choicepicker>`);
    const input = el.querySelector("input")!;
    let host: HTMLElement | null = null;
    el.addEventListener("click", (e) => { host = touchedHost(e); });
    input.click();
    expect(host).toBe(el.querySelector("a2ui-choicepicker"));
  });

  it("还有没通过的检查就算不能交; 交的时候每一项都亮出来", () => {
    const el = card(`<a2ui-choicepicker><div class="a2ui-validation-errors"><div class="a2ui-error-message">请选择日期</div></div></a2ui-choicepicker>
                     <a2ui-basic-textfield></a2ui-basic-textfield>`);
    expect(hasInvalid(el)).toBe(true);
    touchAll(el);
    expect([...el.querySelectorAll("[data-touched]")].map((n) => n.tagName.toLowerCase()))
      .toEqual(["a2ui-choicepicker", "a2ui-basic-textfield"]);
    expect(hasInvalid(card(`<a2ui-choicepicker></a2ui-choicepicker>`))).toBe(false);
  });
});
