/** 模型配色板 (8/1).
 *
 * ## 为什么把「图表颜色」和「列表圆点」合成一个选择
 *
 * 它们是**同一个模型在两个视图里的样子**：颜色用在审计页的图表，圆点用在
 * 模型列表和聊天页的选择器。配不一致的话，同一个模型在图表里是紫的、在
 * 列表里是绿的 —— 而这两处不会同时出现在一屏，所以没人会立刻发现。
 *
 * 改之前是两个自由文本框，各填各的。现有 7 个模型的配对其实是一致的
 * （`#7c3aed`↔🟣 / `#f59e0b`↔🟠 / `#dc2626`↔🔴 / `#6b7280`↔⚫），
 * 但那是**填的人小心**，不是有东西拦着。而模型改成可在界面上增删之后，
 * 每加一个模型都要重新小心一次。
 *
 * 选一个配色，两个值一起定 —— 不一致这件事在结构上就不可能发生。
 *
 * ## 为什么是这 9 个
 *
 * 圆点只能用 emoji，而 emoji 的实心圆点**总共就这么多颜色**
 * （🔴🟠🟡🟢🔵🟣🟤⚫⚪）。所以调色板是围着它们建的，而不是先挑好看的颜色
 * 再去找对应的 emoji —— 反过来的话必然有几个颜色配不到圆点。
 *
 * 十六进制值取自 Tailwind 的 500/600 档，这套色在浅底上区分度够，
 * 而且跟仓里现有的值大部分对得上（见上）。
 */

export interface PaletteEntry {
  /** 给人看的名字，出现在下拉框里 */
  label: string;
  /** 图表用。CSS color。 */
  color: string;
  /** 列表行首圆点。 */
  dot: string;
}

export const MODEL_PALETTE: readonly PaletteEntry[] = [
  { label: "紫色", color: "#7c3aed", dot: "🟣" },
  { label: "蓝色", color: "#2563eb", dot: "🔵" },
  { label: "绿色", color: "#16a34a", dot: "🟢" },
  { label: "橙色", color: "#f59e0b", dot: "🟠" },
  { label: "黄色", color: "#eab308", dot: "🟡" },
  { label: "红色", color: "#dc2626", dot: "🔴" },
  { label: "棕色", color: "#92400e", dot: "🟤" },
  { label: "深灰", color: "#4b5563", dot: "⚫" },
  { label: "浅灰", color: "#9ca3af", dot: "⚪" },
] as const;

/** 当前的 (color, dot) 落在调色板的哪一项上。
 *
 * 只按颜色匹配，**不要求圆点也一致** —— 老数据里
 * `#6b7280`/`#4b5563` 都配 ⚫、`#dc2626`/`#ef4444` 都配 🔴，
 * 按两者都匹配的话大部分老模型会被判成"自定义"，而它们其实就是灰和红。
 *
 * 匹配不上返 null，表单据此显示「自定义」并把原值原样保留 —— 客户可能
 * 用的是自己的品牌色，不该被这次改动冲掉。
 */
export function matchPalette(
  color: string | null | undefined,
): PaletteEntry | null {
  if (!color) return null;
  const c = color.trim().toLowerCase();
  return MODEL_PALETTE.find((p) => p.color.toLowerCase() === c) ?? null;
}

/** 合法的 CSS 十六进制颜色。
 *
 * 填错的后果是静默的：图表拿到一个非法颜色会退回浏览器默认（通常是黑），
 * 而那个模型在图表里就跟别的黑色模型混在一起了，没有任何报错。
 */
export function isHexColor(v: string): boolean {
  return /^#[0-9a-fA-F]{6}$/.test(v.trim());
}
