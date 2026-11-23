/** 11/16 卡片里的数据图表: 画之前的检查 (跟 tool-bridge a2ui_chart.py 一致) 和整理 (照上游 transformSpec)。 */
import { describe, expect, it } from "vitest";

import { CHART_MAX_ROWS, asSpec, chartProblem, prepareSpec } from "./a2uiChart";

const BAR = { mark: "bar", data: { values: [{ day: "周一", n: 12 }, { day: "周二", n: 30 }] },
              encoding: { x: { field: "day", type: "nominal" }, y: { field: "n", type: "quantitative" } } };
const THEME = { text: "#111", muted: "#666", grid: "#ddd", palette: ["#0E6169", "#F0703A"] };

describe("chartProblem", () => {
  it("合格的 Vega-Lite 过; 各种不许的说清原因", () => {
    expect(chartProblem(BAR)).toBeNull();
    expect(chartProblem({ ...BAR, $schema: "https://vega.github.io/schema/vega-lite/v6.json" })).toBeNull();
    expect(chartProblem("bar")).toMatch(/不是一个 Vega-Lite 对象/);
    expect(chartProblem({ $schema: "https://vega.github.io/schema/vega/v5.json", marks: [] })).toMatch(/只支持 Vega-Lite/);
    expect(chartProblem({ ...BAR, data: { url: "https://evil.example.com/d.json" } })).toMatch(/网址/);
    expect(chartProblem({ ...BAR, encoding: { ...BAR.encoding, href: { field: "x" } } })).toMatch(/网址/);
    expect(chartProblem({ ...BAR, layer: [{ mark: { type: "image" } }] })).toMatch(/图片/);
    expect(chartProblem({ ...BAR, data: { values: Array.from({ length: CHART_MAX_ROWS + 1 }, (_, i) => ({ n: i })) } })).toMatch(/超过/);
  });
});

describe("prepareSpec", () => {
  it("默认样式垫在模型写的 config 下面; 宽高跟容器; 不改原对象", () => {
    const spec = { ...BAR, config: { axis: { labelFontSize: 14 } } };
    const out = prepareSpec(spec, 360, 0, THEME) as Record<string, any>;
    expect(out.width).toBe(360);
    expect(out.height).toBe(290);
    expect(out.autosize).toEqual({ type: "fit", contains: "padding" });
    expect(out.config.axis.labelFontSize).toBe(14);
    expect(out.config.axis.labelColor).toBe("#666");
    expect(out.config.range.category).toEqual(THEME.palette);
    expect(spec).not.toHaveProperty("width");
  });
  it("分面 / 拼接的图不动宽高 (每格的宽高 Vega-Lite 自己排)", () => {
    const out = prepareSpec({ hconcat: [BAR, BAR] }, 360, 200, THEME);
    expect(out).not.toHaveProperty("width");
    expect(out).not.toHaveProperty("autosize");
  });
  it("字符串形式的 spec 认成对象", () => {
    expect(asSpec(JSON.stringify(BAR))).toEqual(BAR);
    expect(asSpec("不是 JSON")).toBe("不是 JSON");
  });
});

describe("prepareSpec 颜色以鲶鱼主题为准", () => {
  it("模型写死的深色专用颜色 (标题 / 轴字 / 背景) 去掉, 数据颜色留着", () => {
    const spec = { ...BAR, background: "#111", title: { text: "进度", color: "#e8eaed" },
                   config: { axis: { labelColor: "#a0a4ab", gridColor: "#3a3f47", labelFontSize: 14 } },
                   encoding: { ...BAR.encoding, x: { ...BAR.encoding.x, axis: { title: "月", labelColor: "#fff" } } },
                   layer: [{ mark: { type: "line", color: "#4fc3f7" } }] };
    const out = prepareSpec(spec, 300, 200, THEME) as Record<string, any>;
    expect(out).not.toHaveProperty("background");
    expect(out.title).toEqual({ text: "进度" });
    expect(out.config.axis).toMatchObject({ labelColor: "#666", gridColor: "#ddd", labelFontSize: 14 });
    expect(out.config.background).toBe("transparent");
    expect(out.encoding.x.axis).toMatchObject({ title: "月" });
    expect(out.encoding.x.axis).not.toHaveProperty("labelColor");
    expect(out.layer[0].mark.color).toBe("#4fc3f7");
  });
});

describe("prepareSpec 顺序和数值标签", () => {
  it("分类轴按数据顺序; 某层写了顺序, 同字段的别的层跟着用; 数值字是主题色", () => {
    const months = ["7月", "8月", "9月", "10月"];
    const spec = { layer: [
      { mark: "line", encoding: { x: { field: "m", type: "ordinal", sort: months }, y: { field: "p", type: "quantitative" } } },
      { mark: { type: "text", color: "#e8eaed", dy: -8 }, encoding: { x: { field: "m", type: "ordinal" }, text: { field: "p" } } },
    ] };
    const out = prepareSpec(spec, 300, 200, THEME) as Record<string, any>;
    expect(out.layer[1].encoding.x.sort).toEqual(months);
    expect(out.layer[1].mark).toEqual({ type: "text", dy: -8 });
    expect(out.config.text.color).toBe("#111");
    const bar = prepareSpec(BAR, 300, 200, THEME) as Record<string, any>;
    expect(bar.encoding.x.sort).toBeNull();
    expect(bar.encoding.y).not.toHaveProperty("sort");          // 数值轴不动
    expect((prepareSpec({ ...BAR, encoding: { ...BAR.encoding, x: { ...BAR.encoding.x, sort: "-y" } } }, 300, 200, THEME) as any)
      .encoding.x.sort).toBe("-y");                               // 模型写了排序就照它
  });
});

describe("prepareSpec 分组柱状图 (11/16 鸿波: 7 个资质名斜着放、隐掉一半, 对不准)", () => {
  const items = ["应急处理一级", "信息系统安全运维一级", "信息系统安全集成一级", "信息安全风险评估一级", "软件安全开发一级", "信息安全应急处理二级", "信息系统安全运维二级"];
  const rows = items.flatMap((item) => [{ item, vendor: "金智达", price: 240000 }, { item, vendor: "上海擎标", price: 56500 }]);
  const quote = { data: { values: rows }, layer: [{ mark: "bar" }, { mark: { type: "text", dy: -4 }, encoding: { text: { field: "price", format: ",.0f" } } }],
                  encoding: { x: { field: "item", type: "nominal", sort: null, axis: { labelAngle: -30 } }, y: { field: "price", type: "quantitative" },
                              color: { field: "vendor", type: "nominal" }, xOffset: { field: "vendor" } } };

  it("宽: 分类名全显示、横着放、折行居中; 柱顶数写成万", () => {
    const out = prepareSpec(quote, 1050, 290, THEME) as Record<string, any>;
    expect(out.encoding.x.axis).toMatchObject({ labelAngle: 0, labelOverlap: false, labelAlign: "center" });
    expect(out.encoding.x.axis.labelExpr).toMatch(/^\[substring\(datum\.label, 0, \d+\)/);
    const text = out.layer[1];
    expect(text.encoding.text).toEqual({ field: "__catfish_label", type: "nominal" });
    expect(text.transform[0].calculate).toContain("'万'");
    expect(text.mark.fontSize).toBe(10);
    expect(out.config.legend.orient).toBeUndefined();
  });

  it("窄: 图例挪到下面; 柱子太窄就不标数; 名字折更多行不截断", () => {
    const out = prepareSpec(quote, 420, 290, THEME) as Record<string, any>;
    expect(out.config.legend.orient).toBe("bottom");
    expect(out.layer).toHaveLength(1);
    const lines = (out.encoding.x.axis.labelExpr.match(/substring/g) || []).length;
    const per = Number(/substring\(datum\.label, 0, (\d+)\)/.exec(out.encoding.x.axis.labelExpr)![1]);
    expect(lines * per).toBeGreaterThanOrEqual(10);    // 「信息系统安全运维一级」10 个字放得下
  });

  it("不分组的普通柱状图不动数值标签", () => {
    const out = prepareSpec({ ...BAR, layer: [{ mark: "bar" }, { mark: "text", encoding: { text: { field: "n" } } }] }, 600, 200, THEME) as Record<string, any>;
    expect(out.layer[1].encoding.text).toEqual({ field: "n" });
  });
});

describe("数值字段写了文字范围 (11/24 上周邮件分类饼图画不出来)", () => {
  it("theta = count (数值) 却给了类别名当 domain → 去掉; 类别字段的 domain 照留", () => {
    const pie = {
      data: { values: [{ category: "本人自留", count: 7 }, { category: "订阅资讯", count: 6 }] },
      mark: { type: "arc", innerRadius: 60 },
      encoding: {
        theta: { field: "count", type: "quantitative", scale: { domain: ["本人自留", "订阅资讯"] } },
        color: { field: "category", type: "nominal", scale: { domain: ["本人自留", "订阅资讯"] } },
      },
    };
    const out = prepareSpec(pie, 360, 0, THEME) as Record<string, any>;
    expect(out.encoding.theta.scale.domain).toBeUndefined();
    expect(out.encoding.color.scale.domain).toEqual(["本人自留", "订阅资讯"]);
    expect((pie.encoding.theta.scale as Record<string, unknown>).domain).toBeTruthy();   // 原对象不动
  });
  it("数值 domain 写数字的、时间 domain 写日期的照留", () => {
    const bar = { mark: "bar", encoding: {
      y: { field: "n", type: "quantitative", scale: { domain: [0, 100] } },
      x: { field: "d", type: "temporal", scale: { domain: ["2026-11-16", "2026-11-22"] } },
    } };
    const out = prepareSpec(bar, 360, 0, THEME) as Record<string, any>;
    expect(out.encoding.y.scale.domain).toEqual([0, 100]);
    expect(out.encoding.x.scale.domain).toEqual(["2026-11-16", "2026-11-22"]);
  });
});
