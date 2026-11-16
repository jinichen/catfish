/**
 * 对话卡片里的数据图表 (VegaChart, 11/16) —— 画之前的检查和整理, 不碰 DOM (好测)。
 *
 * 组件照上游官方扩展目录 catalogs/vegachart/v1; 规则跟 tool-bridge a2ui_chart.py 一致 (那边出卡 / 改卡时查过一遍,
 * 这里画之前再查一遍 —— 卡片文件在本机, 也可能是老版本出的):
 *   · 只认 Vega-Lite, 不认完整的 Vega (signals / marks / scales)
 *   · 不许 url / href 键、image 标记 (卡片是模型写的, 不能让它从网上取东西)
 *   · 数据最多 500 行、描述最多 64KB
 * 整理 (prepareSpec) 照上游 transformSpec: 合并默认样式 (颜色取鲶鱼主题, 深浅色跟着变)、宽度跟卡片、autosize fit。
 */

export const CHART_MAX_ROWS = 500;
export const CHART_MAX_BYTES = 64 * 1024;
export const CHART_DEFAULT_HEIGHT = 290;
const FORBIDDEN_KEYS = new Set(["url", "href"]);
const VEGA_ONLY_KEYS = ["signals", "marks", "scales", "axes", "legends", "projections"];

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);

/** 不合格返回原因 (中文), 合格返回 null */
export function chartProblem(spec: unknown): string | null {
  if (!isObj(spec)) return "图表数据不是一个 Vega-Lite 对象";
  const schema = typeof spec.$schema === "string" ? spec.$schema : "";
  if ((schema && !schema.includes("vega-lite")) || VEGA_ONLY_KEYS.some((k) => k in spec)) return "只支持 Vega-Lite 图表";
  let size = 0;
  try {
    size = new TextEncoder().encode(JSON.stringify(spec)).length;
  } catch {
    return "图表数据格式不对";
  }
  if (size > CHART_MAX_BYTES) return "图表数据太大";
  const stack: unknown[] = [spec];
  while (stack.length) {
    const node = stack.pop();
    if (Array.isArray(node)) {
      stack.push(...node);
      continue;
    }
    if (!isObj(node)) continue;
    for (const [k, v] of Object.entries(node)) {
      if (FORBIDDEN_KEYS.has(k)) return "图表不能从网址取数据或带链接";
      if (k === "mark" && (v === "image" || (isObj(v) && v.type === "image"))) return "图表不能加载图片";
      if (k === "values" && Array.isArray(v) && v.length > CHART_MAX_ROWS) return `图表数据超过 ${CHART_MAX_ROWS} 行`;
      stack.push(v);
    }
  }
  return null;
}

export interface ChartTheme { text: string; muted: string; grid: string; palette: string[] }

/** 从鲶鱼的主题变量取颜色 (深浅色各一套, 由 CSS 变量决定) */
export function readTheme(el: Element | null = typeof document !== "undefined" ? document.documentElement : null): ChartTheme {
  const css = el ? getComputedStyle(el) : null;
  const v = (name: string, fallback: string) => css?.getPropertyValue(name).trim() || fallback;
  return {
    text: v("--catfish-text", "#1f2328"),
    muted: v("--catfish-text-muted", "#6b7280"),
    grid: v("--catfish-border", "#e5e7eb"),
    palette: [v("--catfish-cyan", "#0E6169"), "#F0703A", "#6B5BD2", "#2F8F4E", "#B5476B", "#8A6D1F"],
  };
}

/** 文字 / 轴线 / 网格 / 背景的颜色由鲶鱼主题定 (深浅色要跟着变); 模型写死的这些颜色去掉。
 *  11/16 真模型评估: 专员写了一套深色专用的颜色 (标题、刻度字接近白色), 浅色模式下几乎看不见。数据的颜色 (mark / range) 保留。 */
const THEME_COLOR_KEYS = new Set(["labelColor", "titleColor", "domainColor", "tickColor", "gridColor", "background", "subtitleColor"]);

function stripThemeColors(node: unknown, inTitle = false): void {
  if (Array.isArray(node)) {
    node.forEach((n) => stripThemeColors(n, inTitle));
    return;
  }
  if (!isObj(node)) return;
  for (const k of Object.keys(node)) {
    if (THEME_COLOR_KEYS.has(k) || (inTitle && k === "color")) {
      delete node[k];
      continue;
    }
    stripThemeColors(node[k], k === "title");
  }
}

const ORDER_CHANNELS = ["x", "y", "color", "theta", "row", "column"];

/** 每个编码 (顶层 + 各图层) 里的 x / y / color …, 交给 fn */
function eachEncoding(node: unknown, fn: (enc: Obj) => void): void {
  if (Array.isArray(node)) {
    node.forEach((n) => eachEncoding(n, fn));
    return;
  }
  if (!isObj(node)) return;
  if (isObj(node.encoding)) fn(node.encoding);
  for (const k of ["layer", "spec", "concat", "hconcat", "vconcat"]) if (k in node) eachEncoding(node[k], fn);
}

/** 分类轴按数据原来的顺序 (Vega-Lite 默认按字母排: 周一 周三 周二…、10月 11月 7月…, 11/16 真模型评估三张图都这样)。
 *  某一层写了顺序 (sort: [...]) 的, 同一字段别的层也用它; 都没写的用 null (= 数据顺序)。 */
function keepDataOrder(spec: Obj): void {
  const explicit = new Map<string, unknown>();
  eachEncoding(spec, (enc) => {
    for (const ch of ORDER_CHANNELS) {
      const d = enc[ch];
      if (isObj(d) && typeof d.field === "string" && d.sort !== undefined) explicit.set(`${ch}:${d.field}`, d.sort);
    }
  });
  eachEncoding(spec, (enc) => {
    for (const ch of ORDER_CHANNELS) {
      const d = enc[ch];
      if (!isObj(d) || typeof d.field !== "string" || d.sort !== undefined) continue;
      if (d.type !== "nominal" && d.type !== "ordinal") continue;
      const same = explicit.get(`${ch}:${d.field}`);
      d.sort = same !== undefined ? structuredClone(same) : null;
    }
  });
}

/** 数值标签 (text 标记) 的字色跟主题走: 模型常写成浅色, 浅色模式下看不见 */
function themeTextMarks(node: unknown): void {
  if (Array.isArray(node)) {
    node.forEach(themeTextMarks);
    return;
  }
  if (!isObj(node)) return;
  const m = node.mark;
  if (isObj(m) && m.type === "text") {
    delete m.color;
    delete m.fill;
  }
  for (const k of ["layer", "spec", "concat", "hconcat", "vconcat"]) if (k in node) themeTextMarks(node[k]);
}

/** 上游 transformSpec 的做法: 默认样式垫在模型写的 config 下面 (颜色除外, 颜色以鲶鱼主题为准); 宽度跟容器, 高度用 height (默认 290)。不改原对象。 */
export function prepareSpec(spec: Obj, width: number, height: number, theme: ChartTheme): Obj {
  const out = structuredClone(spec) as Obj;
  stripThemeColors(out);
  keepDataOrder(out);
  themeTextMarks(out);
  const cfg = isObj(out.config) ? out.config : {};
  const sub = (k: string) => (isObj(cfg[k]) ? (cfg[k] as Obj) : {});
  out.config = {
    font: "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif",
    range: { category: theme.palette },
    // 单一系列 (没按类别上色) 的柱子 / 线 / 面积也用鲶鱼主色, 不是 Vega 默认的蓝
    mark: { color: theme.palette[0] },
    ...cfg,
    background: "transparent",
    // 横轴: 「周一」「7月」这种短中文标签 Vega 默认会竖过来, 改成横着放, 挤不下的自己隐掉几个
    axisX: { labelAngle: 0, labelOverlap: true, ...sub("axisX") },
    axis: { labelFontSize: 11, titleFontSize: 12, ...sub("axis"),
            domainColor: theme.grid, gridColor: theme.grid, tickColor: theme.grid, labelColor: theme.muted, titleColor: theme.text },
    legend: { labelFontSize: 11, titleFontSize: 12, ...sub("legend"), labelColor: theme.muted, titleColor: theme.text },
    title: { fontSize: 13, ...sub("title"), color: theme.text, subtitleColor: theme.muted },
    text: { color: theme.text, ...sub("text") },
    view: { stroke: "transparent", ...sub("view") },
  };
  const faceted = "facet" in out || "row" in out || "column" in out || "repeat" in out || "concat" in out
    || "hconcat" in out || "vconcat" in out;
  if (!faceted) {
    out.autosize = { type: "fit", contains: "padding" };
    out.width = width > 0 ? width : "container";
    out.height = height || CHART_DEFAULT_HEIGHT;
  }
  return out;
}

/** card_update 推过来的 / 绑定的 spec 有时是 JSON 字符串: 认成对象 */
export function asSpec(v: unknown): unknown {
  if (typeof v !== "string") return v;
  try {
    return JSON.parse(v);
  } catch {
    return v;
  }
}
