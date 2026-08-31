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

export function wikiSourceLabel(source: string): string {
  const normalized = source.replace(/\\/g, "/");
  const basename = normalized.split("/").filter(Boolean).at(-1) ?? source;
  return basename.replace(/[_-]?\d{10,}(?=\.[^.]+$)/, "");
}
