const KIND_LABELS: Record<string, string> = {
  entity: "对象",
  concept: "主题",
  query: "记录",
};

const SUBTYPE_LABELS: Record<string, string> = {
  org: "组织",
  organization: "组织",
  department: "部门",
  person: "人员",
  project: "项目",
  system: "知识体系",
  cert: "证书",
  certificate: "证书",
  policy: "制度",
  process: "流程",
  rule: "规则",
  principle: "原则",
  standard: "标准",
  product: "产品",
  vendor: "供应商",
};

export function wikiKindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? "知识";
}

export function wikiSubtypeLabel(subtype: string | null, kind: string): string {
  if (!subtype) return wikiKindLabel(kind);
  return SUBTYPE_LABELS[subtype.toLowerCase()] ?? subtype;
}

/**
 * 来源显示成员工能回查的话。9/17 起 sources 只有三种 (写入侧收敛):
 *   journal:2026-07-14                      → 7月14日日志
 *   raw/sources/1783325914-企业资质列表(4)  → 资料「企业资质列表(4)」
 *   manual                                  → 手工录入
 * 老条目里的 employee_journal 显示成「日志（未标日期）」, 一眼看出它没来源。
 */
export function wikiSourceLabel(source: string): string {
  const raw = source.trim().replace(/^["']|["']$/g, "");
  const journal = /^journal:\s*(\d{4})-(\d{2})-(\d{2})$/.exec(raw);
  if (journal) {
    return `${Number(journal[2])}月${Number(journal[3])}日日志`;
  }
  if (raw === "manual") return "手工录入";
  if (raw === "employee_journal") return "日志（未标日期）";
  const normalized = raw.replace(/\\/g, "/");
  const basename = normalized.split("/").filter(Boolean).at(-1) ?? raw;
  const name = basename
    .replace(/\.md$/i, "")
    .replace(/^\d{10,}[_-]/, "")
    .replace(/[_-]?\d{10,}(?=\.[^.]+$)/, "");
  return normalized.includes("raw/sources/") ? `资料「${name}」` : name;
}
