/** 11/25 鸿波「改, 两处都改」—— A2UI 官方组件 (Lit, 画在 light DOM 里) 的两处行为补丁 (样式在 styles/artifacts.css):
 *
 * 1. 还没填就报错: 组件一画出来就把 checks 的结果 (「请选择日期」) 红字摆上 —— 看着像已经填错了。
 *    改成: 错误提示先藏着, 员工碰过这一项 (点 / 输入 / 离开) 才显示 (宿主元素标 data-touched);
 *    点按钮交卡片时还有没通过的, 全部显示出来、不交。
 * 2. 选项字太粗: 见 artifacts.css 的 .a2ui-option-label。 */

const isA2ui = (n: EventTarget): n is HTMLElement => n instanceof HTMLElement && n.tagName.toLowerCase().startsWith("a2ui-");

/** 员工碰过的那个组件: 事件路径上最里层的 a2ui-* 元素 (点单选项 → a2ui-choicepicker) */
export function touchedHost(e: Event): HTMLElement | null {
  return (e.composedPath().find(isA2ui) as HTMLElement | undefined) ?? null;
}

/** 卡片里还有没通过的检查 (组件只在有错时才画 .a2ui-validation-errors) */
export function hasInvalid(root: Element): boolean {
  return !!root.querySelector(".a2ui-validation-errors");
}

/** 交卡片没通过: 每一项都算碰过, 提示全亮出来 */
export function touchAll(root: Element): void {
  for (const el of Array.from(root.querySelectorAll("*")).filter(isA2ui)) el.setAttribute("data-touched", "");
}
