import type { RelatedRef, WikiFileInfo } from "../../lib/tauri_wiki";

export type WikiRelationshipTaskKind = "pending" | "missing" | "duplicate";

export interface WikiRelationshipTask {
  id: string;
  kind: WikiRelationshipTaskKind;
  title: string;
  detail: string;
  file: WikiFileInfo;
  duplicatePaths?: string[];
}

const DEFAULT_RELATION_TYPES = [
  "关联",
  "负责人",
  "所属部门",
  "依据",
  "协作部门",
  "包含",
];

function normalizeName(value: string): string {
  return value
    .normalize("NFKC")
    .toLocaleLowerCase("zh-CN")
    .replace(/[\s·•,，.。()（）\-_]/g, "");
}

function duplicateGroups(files: WikiFileInfo[]): WikiFileInfo[][] {
  const candidates = files.filter((file) => file.kind !== "query");
  const namesByPath = new Map<string, Set<string>>();
  const pathsByName = new Map<string, Set<string>>();

  for (const file of candidates) {
    const names = new Set(
      [file.title, ...file.aliases]
        .map(normalizeName)
        .filter((name) => name.length >= 2),
    );
    namesByPath.set(file.rel_path, names);
    for (const name of names) {
      const paths = pathsByName.get(name) ?? new Set<string>();
      paths.add(file.rel_path);
      pathsByName.set(name, paths);
    }
  }

  const duplicatePaths = new Set<string>();
  for (const paths of pathsByName.values()) {
    if (paths.size > 1) paths.forEach((path) => duplicatePaths.add(path));
  }

  const groups: WikiFileInfo[][] = [];
  const visited = new Set<string>();
  for (const file of candidates) {
    if (!duplicatePaths.has(file.rel_path) || visited.has(file.rel_path)) continue;
    const groupPaths = new Set<string>([file.rel_path]);
    const queue = [file.rel_path];
    while (queue.length > 0) {
      const path = queue.shift()!;
      for (const name of namesByPath.get(path) ?? []) {
        for (const neighbor of pathsByName.get(name) ?? []) {
          if (!groupPaths.has(neighbor)) {
            groupPaths.add(neighbor);
            queue.push(neighbor);
          }
        }
      }
    }
    groupPaths.forEach((path) => visited.add(path));
    const group = candidates.filter((candidate) => groupPaths.has(candidate.rel_path));
    if (group.length > 1) groups.push(group);
  }
  return groups;
}

export function buildWikiRelationshipTasks(files: WikiFileInfo[]): WikiRelationshipTask[] {
  const pending = files
    .filter((file) => (file.ontology_status ?? "active") === "pending")
    .map((file): WikiRelationshipTask => ({
      id: `pending:${file.rel_path}`,
      kind: "pending",
      title: `确认「${file.title}」的关系`,
      detail: file.related.length > 0
        ? `已有 ${file.related.length} 条候选关系，需要核对`
        : "尚未确认它与谁有关",
      file,
    }));

  const missing = files
    .filter(
      (file) =>
        file.kind !== "query" &&
        (file.ontology_status ?? "active") === "active" &&
        file.related.length === 0,
    )
    .map((file): WikiRelationshipTask => ({
      id: `missing:${file.rel_path}`,
      kind: "missing",
      title: `补充「${file.title}」的关系`,
      detail: "至少补充一条关键关系，图谱才便于理解",
      file,
    }));

  const duplicates = duplicateGroups(files).map((group): WikiRelationshipTask => ({
    id: `duplicate:${group.map((file) => file.rel_path).sort().join("|")}`,
    kind: "duplicate",
    title: `核对「${group.map((file) => file.title).join(" / ")}」`,
    detail: `${group.length} 个名称或别名相同的条目，本轮只提示、不自动合并`,
    file: group[0],
    duplicatePaths: group.map((file) => file.rel_path),
  }));

  return [...pending, ...missing, ...duplicates];
}

export function relationTypeOptions(files: WikiFileInfo[]): string[] {
  const discovered = files.flatMap((file) =>
    file.related.map((relation) => relation.rel?.trim()).filter(Boolean) as string[],
  );
  return [...new Set([...DEFAULT_RELATION_TYPES, ...discovered])];
}

function quoteYaml(value: string): string {
  return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

export function buildConfirmedWikiContent(content: string, relations: RelatedRef[]): string {
  if (!content.startsWith("---\n")) {
    throw new Error("该条目缺少标准 frontmatter，暂不能在关系工作台修改");
  }
  const end = content.indexOf("\n---", 4);
  if (end < 0) throw new Error("该条目的 frontmatter 不完整");

  const serialized = relations
    .map((relation) => {
      const rel = relation.rel?.trim();
      return rel
        ? `{name: ${quoteYaml(relation.name)}, rel: ${quoteYaml(rel)}}`
        : quoteYaml(relation.name);
    })
    .join(", ");
  const lines = content.slice(4, end).split("\n");
  let foundRelated = false;
  let foundStatus = false;
  const nextLines = lines.map((line) => {
    if (/^\s*related\s*:/.test(line)) {
      foundRelated = true;
      return `related: [${serialized}]`;
    }
    if (/^\s*ontology_status\s*:/.test(line)) {
      foundStatus = true;
      return "ontology_status: active";
    }
    return line;
  });
  if (!foundRelated) nextLines.push(`related: [${serialized}]`);
  if (!foundStatus) nextLines.push("ontology_status: active");
  return `---\n${nextLines.join("\n")}\n---${content.slice(end + 4)}`;
}
