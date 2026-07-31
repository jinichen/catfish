/** 模型配置页共用的紧凑样式 + 两个小工具.
 *
 * 7/30 按 CLAUDE.md 军规 §1 从 ModelConfigPage.tsx 拆出来 —— 那个文件到了
 * 1010 行 (红线 800)。这一轮改造把它从 827 又加了 183 行, 而军规第 2 条要求
 * "改任何 .tsx 文件后都跑一遍 check_file_sizes.sh", 我这两天一次都没跑。
 *
 * 这些常量列表页和表单页都用, 单独放一份避免两边各写各的 —— 那正是
 * DataTable.tsx 文件头说的"7 张表六种行高"的成因。
 */

import type { CSSProperties } from "react";

export const BOX: CSSProperties = {
  background: "var(--bg-elev)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-md)",
  padding: 10,
};
export const INPUT: CSSProperties = {
  padding: "3px 6px",
  border: "1px solid var(--border)",
  borderRadius: 3,
  fontSize: 12,
  width: "100%",
  boxSizing: "border-box",
  fontFamily: "inherit",
};
export const MONO: CSSProperties = { ...INPUT, fontFamily: "monospace" };
export const LABEL: CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  display: "block",
  marginBottom: 2,
};
export const HINT: CSSProperties = {
  fontSize: 10,
  color: "var(--text-muted)",
  marginTop: 2,
  lineHeight: 1.4,
};

/** 这个值是不是个 ${VAR} 占位符.
 *
 * models.yaml 里内网地址是故意写成 ${INTERNAL_LLM_BASE_*} 的 —— 真实地址在
 * .env 里, 不进配置文件、不进 git、不进数据库。7/30 模型入库时一度把它插值
 * 之后才播种, 于是真实地址被烤进了库 (改 .env 从此不生效, 而且不报错)。
 * 已经修好并做了回迁, 但界面上得说清楚: 把占位符改成写死的地址是有代价的。
 */
export function isEnvPlaceholder(v: string | null | undefined): boolean {
  return typeof v === "string" && /\$\{[A-Za-z_][A-Za-z0-9_]*(:-[^}]*)?\}/.test(v);
}

/** 128000 → "128K", 1000000 → "1M". 给输入框旁边的换算提示用 ——
 *  填 context_window 时人是按"12 万还是 128 万"想的, 而输入框里是一串 0。 */
export function fmtCompact(n: number): string {
  if (n >= 1_000_000) {
    const v = n / 1_000_000;
    return `${Number.isInteger(v) ? v : v.toFixed(1)}M`;
  }
  if (n >= 1000) {
    const v = n / 1000;
    return `${Number.isInteger(v) ? v : v.toFixed(0)}K`;
  }
  return String(n);
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label style={LABEL}>{label}</label>
      {children}
      {hint ? <div style={HINT}>{hint}</div> : null}
    </div>
  );
}
