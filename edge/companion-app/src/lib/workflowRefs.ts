/**
 * 工作流编辑器里删掉一步 / 一个参数时, 把后面引用它的地方一起清掉 (11/08 鸿波删了「生成 Word」那步,
 * 最后一步还写着 ${steps.deliver_docx.docx_path}, 只剩一行红字, 一般人不知道去哪儿找)。
 *
 *   条件 (when) 拿它来比 → 条件去掉 (总是做)
 *   参数 / 给它看的数据 整个就是它 → 清空
 *   夹在一段话里 → 连同前面的小标签 (「文件：」) 一起删
 *   for_each 逐条做的列表就是它 → 删不掉, 留给校验标红 (那一步本来就没法做了)
 * 返回改过的工作流和动过哪几步 (从 1 数, 删完之后的序号), 编辑器提示员工看一眼。
 */
import type { Workflow, WorkflowStep } from "./tauri_workflows";

function esc(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function tokenRe(target: string): RegExp {
  // 可选的前置小标签 (「；文件：」「, 路径:」) + ${target} / ${target.x} / ${target[0]}
  return new RegExp(`(?:[；;，,、]?\\s*[^；;，,、\${}:：\\s]{1,10}[:：]\\s*)?\\$\\{${esc(target)}(?:[.[][^}]*)?\\}`, "g");
}

function exactRe(target: string): RegExp {
  return new RegExp(`^\\s*\\$\\{${esc(target)}(?:[.[][^}]*)?\\}\\s*$`);
}

function cleanText(v: string, target: string): string {
  if (exactRe(target).test(v)) return "";
  return v.replace(tokenRe(target), "").replace(/^[\s；;，,、]+|[\s；;，,、]+$/g, "");
}

function mentions(v: unknown, target: string): boolean {
  if (typeof v === "string") return new RegExp(`\\$\\{${esc(target)}(?:[.[}])`).test(v);
  if (Array.isArray(v)) return v.some((x) => mentions(x, target));
  if (v && typeof v === "object") return Object.values(v).some((x) => mentions(x, target));
  return false;
}

function cleanValue(v: unknown, target: string): unknown {
  if (typeof v === "string") return cleanText(v, target);
  if (Array.isArray(v)) return v.map((x) => cleanValue(x, target));
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, cleanValue(x, target)]));
  return v;
}

function cleanStep(s: WorkflowStep, target: string): WorkflowStep {
  const out: WorkflowStep = { ...s };
  if (s.when && mentions(s.when.of, target)) delete out.when;
  if (s.params && mentions(s.params, target)) out.params = cleanValue(s.params, target) as Record<string, unknown>;
  if (typeof s.input === "string" && mentions(s.input, target)) {
    const v = cleanText(s.input, target);
    if (v) out.input = v;
    else delete out.input;
  }
  if (typeof s.xiaonian === "string" && mentions(s.xiaonian, target)) out.xiaonian = cleanText(s.xiaonian, target);
  if (s.expect && mentions(s.expect, target)) delete out.expect;
  return out;
}

function dropRefs(steps: WorkflowStep[], target: string): { steps: WorkflowStep[]; touched: number[] } {
  const touched: number[] = [];
  const next = steps.map((s, i) => {
    const { for_each: _each, ...rest } = s;
    if (!mentions(rest, target)) return s;
    touched.push(i + 1);
    return cleanStep(s, target);
  });
  return { steps: next, touched };
}

export function removeStep(wf: Workflow, index: number): { wf: Workflow; touched: number[] } {
  const gone = wf.steps[index];
  const rest = wf.steps.filter((_, j) => j !== index);
  if (!gone?.id) return { wf: { ...wf, steps: rest }, touched: [] };
  const r = dropRefs(rest, `steps.${gone.id}`);
  return { wf: { ...wf, steps: r.steps }, touched: r.touched };
}

export function removeParam(wf: Workflow, key: string): { wf: Workflow; touched: number[] } {
  const params = { ...(wf.params ?? {}) };
  delete params[key];
  const r = dropRefs(wf.steps, `params.${key}`);
  return { wf: { ...wf, params, steps: r.steps }, touched: r.touched };
}
