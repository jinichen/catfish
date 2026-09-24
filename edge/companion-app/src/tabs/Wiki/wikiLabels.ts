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
  doc: "文档",
  data: "数据",
  notification: "通知",
  scope: "口径",
  category: "分类",
  framework: "框架",
  method: "方法",
};

/** 左侧目录的类型分组顺序 (9/24)。不在表里的类型排在这些之后, 「未分类」最后。 */
export const ENTITY_TYPE_ORDER = ["org", "department", "person", "project", "cert", "standard", "doc", "data", "notification", "system"];
export const CONCEPT_TYPE_ORDER = ["system", "process", "rule", "principle", "standard", "scope", "method", "category", "framework"];

export function groupByType<T extends { subtype: string | null; kind: string; title: string }>(
  files: T[],
  order: string[],
): Array<[string, T[]]> {
  const buckets = new Map<string, T[]>();
  for (const f of files) {
    const key = f.subtype?.trim().toLowerCase() || "";
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key)!.push(f);
  }
  const rank = (key: string) => (key === "" ? 1e6 : order.indexOf(key) === -1 ? 1e5 : order.indexOf(key));
  return [...buckets.entries()]
    .sort(([a, la], [b, lb]) => rank(a) - rank(b) || lb.length - la.length || a.localeCompare(b))
    .map(([key, list]): [string, T[]] => [
      key === "" ? "未分类" : wikiSubtypeLabel(key, list[0].kind),
      [...list].sort((x, y) => x.title.localeCompare(y.title, "zh-CN")),
    ]);
}

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
