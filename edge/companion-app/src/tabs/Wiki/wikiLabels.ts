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
  customer: "客户",
  doc: "文档",
  data: "数据",
  notification: "通知",
  scope: "口径",
  category: "分类",
  framework: "框架",
  method: "方法",
  // 11/09: 后台整理会写出这些英文类型, 目录里原样显示成 culture / strategy / roster
  software: "软件产品",
  document: "文档",
  company: "公司",
  platform: "平台",
  program: "专项计划",
  strategy: "战略",
  roster: "名单",
  person_group: "团队",
  culture: "文化",
  event: "活动",
};

/** 左侧目录的类型分组顺序 (9/24)。不在表里的类型排在这些之后, 「未分类」最后。 */
export const ENTITY_TYPE_ORDER = ["org", "department", "person", "customer", "project", "cert", "standard", "doc", "data", "notification", "system"];
export const CONCEPT_TYPE_ORDER = ["system", "process", "rule", "principle", "standard", "scope", "method", "category", "framework"];

export function groupByType<T extends { subtype: string | null; kind: string; title: string }>(
  files: T[],
  order: string[],
): Array<[string, T[]]> {
  // 11/09: 按显示名分组 —— org / organization 都是「组织」, software / 软件产品 都是「软件产品」, 以前各占一组
  const buckets = new Map<string, { keys: Set<string>; list: T[] }>();
  for (const f of files) {
    const key = f.subtype?.trim().toLowerCase() || "";
    const label = key === "" ? "未分类" : wikiSubtypeLabel(key, f.kind);
    if (!buckets.has(label)) buckets.set(label, { keys: new Set(), list: [] });
    const b = buckets.get(label)!;
    b.keys.add(key);
    b.list.push(f);
  }
  const rankKey = (key: string) => (key === "" ? 1e6 : order.indexOf(key) === -1 ? 1e5 : order.indexOf(key));
  const rank = (keys: Set<string>) => Math.min(...[...keys].map(rankKey));
  return [...buckets.entries()]
    .sort(([a, x], [b, y]) => rank(x.keys) - rank(y.keys) || y.list.length - x.list.length || a.localeCompare(b))
    .map(([label, b]): [string, T[]] => [label, [...b.list].sort((x, y) => x.title.localeCompare(y.title, "zh-CN"))]);
}

/** 11/09 改分类的候选: 同一类 (对象 / 主题) 里已经在用的类型, 按显示名去重, 每个显示名取用得最多的那个写法。 */
export function subtypeOptions(
  files: Array<{ subtype: string | null; kind: string }>,
  kind: string,
): Array<{ key: string; label: string; count: number }> {
  const byLabel = new Map<string, Map<string, number>>();
  for (const f of files) {
    if (f.kind !== kind) continue;
    const key = f.subtype?.trim() || "";
    if (!key) continue;
    const label = wikiSubtypeLabel(key, kind);
    const m = byLabel.get(label) ?? new Map<string, number>();
    m.set(key, (m.get(key) ?? 0) + 1);
    byLabel.set(label, m);
  }
  return [...byLabel.entries()]
    .map(([label, m]) => {
      const [key] = [...m.entries()].sort((a, b) => b[1] - a[1])[0];
      return { key, label, count: [...m.values()].reduce((a, b) => a + b, 0) };
    })
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, "zh-CN"));
}

/** 改 frontmatter 里一个单值字段 (没有就加在末尾); 没有 frontmatter 就补一个。正文不动。 */
export function setFrontmatterScalar(content: string, field: string, value: string): string {
  const m = content.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  const line = `${field}: ${value}`;
  if (!m) return `---\n${line}\n---\n\n${content.replace(/^\s+/, "")}`;
  const lines = m[1].split(/\r?\n/);
  const at = lines.findIndex((l) => l.startsWith(`${field}:`));
  if (at >= 0) lines[at] = line;
  else lines.push(line);
  return `---\n${lines.join("\n")}\n---${content.slice(m[0].length)}`;
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
 *   session:20261101_091002_aaa111          → 11月1日 09:10 的对话 (11/02 起)
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
  // 11/02: 精确到哪次对话 (id 形如 20261101_091002_aaa111 → 11月1日 09:10 的对话)
  const session = /^session:\s*(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})/.exec(raw);
  if (session) {
    return `${Number(session[2])}月${Number(session[3])}日 ${session[4]}:${session[5]} 的对话`;
  }
  if (raw.startsWith("session:")) return "一次对话";
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
