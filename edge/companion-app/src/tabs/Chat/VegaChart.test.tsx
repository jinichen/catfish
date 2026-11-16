// @vitest-environment jsdom
/** 11/16 卡片里的数据图表 (React 版 VegaChart): 用假的 vega-embed (jsdom 画不了 SVG), 查给它的参数和出错时的样子。 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChartView, chartRuntime } from "./VegaChart";

const BAR = { mark: "bar", data: { values: [{ day: "周一", n: 12 }] },
              encoding: { x: { field: "day", type: "nominal" }, y: { field: "n", type: "quantitative" } } };

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function fakeRuntime() {
  const finalize = vi.fn();
  const embed = vi.fn(async (el: HTMLElement) => { el.textContent = "画好了"; return { finalize }; });
  vi.spyOn(chartRuntime, "load").mockResolvedValue({ embed, expr: "INTERP" });
  return { embed, finalize };
}

describe("ChartView", () => {
  it("SVG、不要操作菜单、表达式走解释器、取数据的 loader 全拒; 卸载时释放", async () => {
    const { embed, finalize } = fakeRuntime();
    const { unmount } = render(<ChartView spec={BAR} height={200} />);
    await act(async () => {});
    expect(screen.getByText("画好了")).toBeTruthy();
    const [, spec, opts] = embed.mock.calls[0] as unknown as [HTMLElement, Record<string, any>, Record<string, any>];
    expect(spec.height).toBe(200);
    expect(spec.config.background).toBe("transparent");
    expect(opts).toMatchObject({ actions: false, renderer: "svg", ast: true, expr: "INTERP", defaultStyle: false });
    await expect(opts.loader.load("https://evil.example.com")).rejects.toThrow(/不能从网上/);
    await expect(opts.loader.sanitize("x")).rejects.toThrow();
    unmount();
    expect(finalize).toHaveBeenCalled();
  });

  it("不合格的不画, 说原因; 字符串 spec 照样画", async () => {
    const { embed } = fakeRuntime();
    render(<ChartView spec={{ ...BAR, data: { url: "https://evil.example.com/d.json" } }} />);
    await act(async () => {});
    expect(embed).not.toHaveBeenCalled();
    expect(screen.getByText(/图表不能从网址取数据/)).toBeTruthy();
    cleanup();
    render(<ChartView spec={JSON.stringify(BAR)} />);
    await act(async () => {});
    expect(embed).toHaveBeenCalledTimes(1);
  });

  it("vega 报错: 显示「图表画不出来」, 不崩", async () => {
    vi.spyOn(chartRuntime, "load").mockResolvedValue({ embed: vi.fn().mockRejectedValue(new Error("bad encoding")), expr: null });
    render(<ChartView spec={BAR} />);
    await act(async () => {});
    expect(screen.getByText(/图表画不出来: bad encoding/)).toBeTruthy();
  });
});

describe("VegaChart 在卡片里 (真的 A2UI 处理器)", () => {
  // 小鲶改的哪怕是 /chart/data/values, tool-bridge 也会换成整份 /chart 发过来 (a2ui_chart.lift_chart_updates):
  // 上游 @a2ui/react 0.15 绑在 /chart 的组件收不到子路径的更新 (11/16 实测), 整份替换才重画
  it("绑在 $/chart 的图: card_update 推整份 /chart (新数据), 原地重画", async () => {
    const { embed } = fakeRuntime();
    const { default: A2uiCard } = await import("./A2uiCard");
    const base = [{ version: "1.0", createSurface: { surfaceId: "main", catalogId: "https://catfish.local/a2ui/catalogs/catfish/v1.json",
      components: [{ id: "root", component: "VegaChart", spec: { "@path": "/chart" } }], dataModel: { chart: BAR } } }];
    const { rerender } = render(<A2uiCard cardId="c_20261116_120000_aaaaaa" messages={base} interactive submitted={false} />);
    await act(async () => {});
    expect(embed).toHaveBeenCalledTimes(1);
    const more = [{ day: "周一", n: 12 }, { day: "周二", n: 30 }, { day: "周三", n: 7 }];
    const upd = [...base, { version: "1.0", updateDataModel: { surfaceId: "main", path: "/chart", value: { ...BAR, data: { values: more } } } }];
    rerender(<A2uiCard cardId="c_20261116_120000_aaaaaa" messages={upd} interactive submitted={false} />);
    await act(async () => {});
    expect(embed).toHaveBeenCalledTimes(2);
    const spec = embed.mock.calls[1][1] as unknown as { data: { values: unknown[] } };
    expect(spec.data.values).toEqual(more);
  });
});
