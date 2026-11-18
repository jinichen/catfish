/**
 * 学习路线图 (11/18): 「先懂 A 才能懂 B」—— 按层排, 最上面一层是基础, 越往下越接近目标。
 * 当前在讲的节点高亮, 问过的打勾。不画连线 (节点不多, 层次 + 「要先懂」提示够看), 鼠标停上去显示前置是哪几个。
 */
import type { CSSProperties } from "react";

import type { Plan } from "../../lib/tauri_learn";

/** 每个节点的层: 没有前置的是 0, 其余 = 最长前置链 + 1 */
export function planLayers(plan: Plan): string[][] {
  const ids = plan.nodes.map((n) => n.id);
  const pre = new Map<string, string[]>(ids.map((i) => [i, []]));
  for (const [a, b] of plan.edges) pre.get(b)?.push(a);
  const depth = new Map<string, number>();
  const visit = (id: string, stack: Set<string>): number => {
    const known = depth.get(id);
    if (known !== undefined) return known;
    if (stack.has(id)) return 0;   // 有环 (后端已经拦了, 这里只防死循环)
    stack.add(id);
    const d = Math.max(-1, ...(pre.get(id) ?? []).map((p) => visit(p, stack))) + 1;
    stack.delete(id);
    depth.set(id, d);
    return d;
  };
  ids.forEach((i) => visit(i, new Set()));
  const layers: string[][] = [];
  for (const i of ids) (layers[depth.get(i) ?? 0] ??= []).push(i);
  return layers.filter(Boolean);
}

const pill = (state: "current" | "visited" | "todo"): CSSProperties => ({
  padding: "4px 10px",
  borderRadius: 999,
  fontSize: 12,
  border: `1px solid ${state === "current" ? "var(--catfish-cyan)" : "var(--catfish-border)"}`,
  background: state === "current" ? "var(--catfish-cyan)" : state === "visited" ? "var(--catfish-bg-cream)" : "transparent",
  color: state === "current" ? "#fff" : state === "visited" ? "var(--catfish-text)" : "var(--catfish-text-muted)",
  whiteSpace: "nowrap",
});

export default function PlanMap({ plan, current, visited }: { plan: Plan; current?: string; visited?: string[] }) {
  const title = new Map(plan.nodes.map((n) => [n.id, n.title]));
  const pre = (id: string) => plan.edges.filter(([, b]) => b === id).map(([a]) => title.get(a) ?? a);
  const done = new Set(visited ?? []);
  return (
    <div aria-label="学习路线图" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {planLayers(plan).map((layer, i) => (
        <div key={i} style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
          <span style={{ fontSize: 11, color: "var(--catfish-text-muted)", width: 40, flexShrink: 0, whiteSpace: "nowrap" }}>{i === 0 ? "基础" : `第${i + 1}层`}</span>
          {layer.map((id) => {
            const st = id === current ? "current" : done.has(id) ? "visited" : "todo";
            const p = pre(id);
            return (
              <span key={id} style={pill(st)} title={p.length ? `要先懂: ${p.join("、")}` : "基础"}>
                {st === "visited" ? "✓ " : ""}{title.get(id)}
              </span>
            );
          })}
        </div>
      ))}
    </div>
  );
}
