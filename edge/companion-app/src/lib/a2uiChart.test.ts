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
