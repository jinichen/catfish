/** 对话卡片里的数据图表 (11/16): 上游官方扩展目录 catalogs/vegachart/v1 的 VegaChart, React 版。
 *
 * 上游给的是 Lit 版 (typescript/catalogs/vegachart, 没发到 npm), 这里照它的做法写:
 *   · vega-embed 动态加载 (只有卡片里有图表才下载那几百 KB), SVG 渲染, 不要右上角的操作菜单
 *   · 宽度跟卡片 (ResizeObserver), 高度 height 默认 290; 默认样式垫在模型写的 config 下面
 * 鲶鱼另外收紧的 (lib/a2uiChart.ts):
 *   · 画之前再查一遍: 只认 Vega-Lite、不许网址 / 链接 / 图片、数据最多 500 行
 *   · 表达式用 vega-interpreter 解释执行 (ast: true) —— Companion 的 CSP 不许 eval, 也不该许
 *   · loader 一律拒: 就算漏过检查也取不了网上的东西
 *   · 颜色取鲶鱼主题变量, 深浅色切换时重画
 */
import { useEffect, useRef, useState } from "react";
import { ChartBar } from "@phosphor-icons/react";
import { Ico } from "../../components/icons";
import { z } from "zod"; // 运行时被 vite.config.ts 的别名指到 zod/v3, 跟 @a2ui/* 同一套
import { createComponentImplementation } from "@a2ui/react";
import { DynamicNumberSchema, DynamicValueSchema } from "@a2ui/web_core/v1_0";

import { CHART_DEFAULT_HEIGHT, asSpec, chartProblem, prepareSpec, readTheme } from "../../lib/a2uiChart";

export const VegaChartApi = {
  name: "VegaChart",
  schema: z.object({
    spec: DynamicValueSchema,
    height: DynamicNumberSchema.optional(),
    weight: z.number().optional(),
  }).strict(),
};

const BLOCKED = () => Promise.reject(new Error("卡片里的图表不能从网上或本机文件取数据"));
/** vega 的 loader 接口: load / sanitize / http / file 全部拒 */
const blockedLoader = { load: BLOCKED, sanitize: BLOCKED, http: BLOCKED, file: BLOCKED };

/** 深浅色切换 (Companion 改 <html data-theme> 或系统外观变) → 变一次 */
function useThemeTick(): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const bump = () => setTick((t) => t + 1);
    const mo = typeof MutationObserver !== "undefined" ? new MutationObserver(bump) : null;
    mo?.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class", "style"] });
    const mq = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    mq?.addEventListener?.("change", bump);
    return () => { mo?.disconnect(); mq?.removeEventListener?.("change", bump); };
  }, []);
  return tick;
}

type Embed = (el: HTMLElement, spec: object, opts: object) => Promise<{ finalize: () => void }>;
/** 测试里替成假的 (jsdom 画不了 SVG) */
export const chartRuntime: { load: () => Promise<{ embed: Embed; expr: unknown }> } = {
  async load() {
    const [embedMod, interp] = await Promise.all([import("vega-embed"), import("vega-interpreter")]);
    return { embed: (embedMod.default ?? embedMod) as unknown as Embed, expr: interp.expressionInterpreter };
  },
};

export function ChartView({ spec, height }: { spec: unknown; height?: number }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const theme = useThemeTick();
  const h = Number(height) > 0 ? Math.min(Number(height), 800) : CHART_DEFAULT_HEIGHT;
  const parsed = asSpec(spec);
  const key = JSON.stringify(parsed ?? null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setWidth(Math.floor(el.getBoundingClientRect().width));
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const problem = chartProblem(parsed);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    const host = box.current;
    if (!host) return;
    let cancelled = false;
    let view: { finalize: () => void } | undefined;
    void (async () => {
      const { embed, expr } = await chartRuntime.load();
      if (cancelled) return;
      const target = document.createElement("div");
      host.replaceChildren(target);
      const res = await embed(target, prepareSpec(parsed as Record<string, unknown>, width || 400, h, readTheme()), {
        actions: false, renderer: "svg", ast: true, expr, defaultStyle: false, loader: blockedLoader,
      });
      if (cancelled) res.finalize();
      else view = res;
    })().catch((e) => { if (!cancelled) setError(`图表画不出来: ${e instanceof Error ? e.message : String(e)}`); });
    return () => {
      cancelled = true;
      view?.finalize();
    };
  }, [key, width, h, theme]);   // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div data-a2ui="VegaChart" style={{ width: "100%", minWidth: 0 }}>
      <div ref={box} style={{ width: "100%", minHeight: error ? 0 : h }} />
      {error && <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}><Ico as={ChartBar} />{error}</div>}
    </div>
  );
}

export const VegaChart = createComponentImplementation(VegaChartApi, ({ props }) => (
  <ChartView spec={props.spec} height={typeof props.height === "number" ? props.height : undefined} />
));
