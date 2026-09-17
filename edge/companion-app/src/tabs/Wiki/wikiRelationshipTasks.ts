import type { RelatedRef, WikiFileInfo } from "../../lib/tauri_wiki";
import { resolveWikiRef } from "../../lib/wikiResolve";

export type WikiRelationshipTaskKind = "pending" | "missing" | "duplicate" | "broken";

export interface WikiRelationshipTask {
  id: string;
  kind: WikiRelationshipTaskKind;
  title: string;
  detail: string;
  file: WikiFileInfo;
  duplicatePaths?: string[];
  relationName?: string;
}

/** 只识别 frontmatter 里的旧关系；正文 wikilink 不属于迁移范围。 */
export function hasLegacyWikiRelations(files: WikiFileInfo[]): boolean {
  return files.some((file) =>
    file.related.some((relation) => relation.source !== "body" && !relation.rel?.trim()),
  );
}

// 9/17: 关系词表是闭合的, 跟 edge/contracts/wiki_relation_vocab.json 一份
// (Python 蒸馏侧运行时读, Rust include_str, 这里手抄 + 测试钉住三份一致)。
// 顺序 = 下拉顺序; 兜底「关联」放最后 —— 它只说明有关系, 不说明是什么关系,
// 写入侧按"没类型"处理 (pending), 所以不能是默认选中的第一项。
export const RELATION_VOCAB = [
  "隶属",
  "包含",
  "负责",
  "参与",
  "协作",
  "依据",
  "遵循",
  "使用",
  "持有",
  "认证",
  "对标",
  "配套",
  "替代",
  "前置",
  "同类",
] as const;
export const RELATION_FALLBACK = "关联";
const DEFAULT_RELATION_TYPES: string[] = [...RELATION_VOCAB, RELATION_FALLBACK];

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
        ? `小鲶写了 ${file.related.length} 条候选关系，还没人核对；对的话一键确认`
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

  const broken = files.flatMap((file): WikiRelationshipTask[] =>
    file.related.flatMap((relation) => {
      const name = relation.name.trim();
      if (!name) return [];
      // 正文 wikilink 用于导航/图谱，不等于用户声明的语义关系。
      if (relation.source === "body") return [];
      const resolution = resolveWikiRef(name, files);
      const reason = !relation.rel?.trim()
        ? "缺少关系类型"
        : resolution.kind === "miss"
          ? "找不到目标条目"
          : resolution.kind === "ambiguous"
            ? `有 ${resolution.candidates.length} 个可能目标`
            : null;
      if (!reason) return [];
      return [{
        id: `broken:${file.rel_path}:${name}`,
        kind: "broken",
        title: `修复「${file.title}」的关系`,
        detail: `“${name}”：${reason}`,
        file,
        relationName: name,
      }];
    }),
  );

  const duplicates = duplicateGroups(files).map((group): WikiRelationshipTask => ({
    id: `duplicate:${group.map((file) => file.rel_path).sort().join("|")}`,
    kind: "duplicate",
    title: `核对「${group.map((file) => file.title).join(" / ")}」`,
    detail: `${group.length} 个名称或别名相同的条目，本轮只提示、不自动合并`,
    file: group[0],
    duplicatePaths: group.map((file) => file.rel_path),
  }));

  return [...pending, ...broken, ...missing, ...duplicates];
}

/**
 * 下拉选项 = 词表 + 兜底。9/17 之前还会把库里已出现的 rel 一并列出来 —— 那正是
 * 自造词 (持有主体/采用口径/不同条目) 越滚越多的入口; 现在写入侧会把表外值归成
 * 「关联」, 再列出来只会让员工选一个存不进去的词。
 */
export function relationTypeOptions(_files: WikiFileInfo[]): string[] {
  return [...DEFAULT_RELATION_TYPES];
}

function quoteYaml(value: string): string {
  return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

type WikiRelationFileMetadata = Pick<
  WikiFileInfo,
  "kind" | "title" | "subtype" | "tags" | "sources" | "aliases"
>;

function serializeList(values: string[]): string {
  return `[${values.map((value) => quoteYaml(value)).join(", ")}]`;
}

function buildLegacyFrontmatter(metadata: WikiRelationFileMetadata, relations: RelatedRef[]): string {
  const lines = [
    `type: ${metadata.kind}`,
    `title: ${quoteYaml(metadata.title)}`,
  ];
  if (metadata.subtype?.trim()) {
    lines.push(`${metadata.kind === "concept" ? "concept_type" : "entity_type"}: ${quoteYaml(metadata.subtype.trim())}`);
  }
  if (metadata.kind === "entity" && metadata.aliases.length > 0) {
    lines.push(`aliases: ${serializeList(metadata.aliases)}`);
  }
  lines.push(`tags: ${serializeList(metadata.tags)}`);
  lines.push(`related: [${relations
    .filter((relation) => relation.source !== "body")
    .map((relation) => {
      const rel = relation.rel?.trim();
      return rel
        ? `{name: ${quoteYaml(relation.name)}, rel: ${quoteYaml(rel)}}`
        : quoteYaml(relation.name);
    })
    .join(", ")}]`);
  lines.push(`sources: ${serializeList(metadata.sources)}`);
  lines.push("ontology_status: active");
  return lines.join("\n");
}

/**
 * 更新关系时兼容旧 Markdown：读取端允许 BOM、空白和 CRLF，写入端也必须如此。
 * 没有 frontmatter 的历史条目只在用户点击“确认关系”后补齐最小元数据。
 */
export function buildConfirmedWikiContent(
  content: string,
  relations: RelatedRef[],
  metadata?: WikiRelationFileMetadata,
): string {
  const normalized = content.replace(/^\uFEFF/, "");
  const opening = normalized.match(/^[\t ]*---\r?\n/);
  if (!opening) {
    if (!metadata) throw new Error("该条目缺少标准 frontmatter，无法确定条目类型");
    return `---\n${buildLegacyFrontmatter(metadata, relations)}\n---\n\n${content}`;
  }

  const frontmatterStart = opening[0].length;
  const remainder = normalized.slice(frontmatterStart);
  const closing = /\r?\n---(?=\r?\n|$)/.exec(remainder);
  if (!closing || closing.index === undefined) throw new Error("该条目的 frontmatter 不完整");
  const end = frontmatterStart + closing.index;

  const serialized = relations
    .filter((relation) => relation.source !== "body")
    .map((relation) => {
      const rel = relation.rel?.trim();
      return rel
        ? `{name: ${quoteYaml(relation.name)}, rel: ${quoteYaml(rel)}}`
        : quoteYaml(relation.name);
    })
    .join(", ");
  const lines = normalized.slice(frontmatterStart, end).replace(/\r\n/g, "\n").split("\n");
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
  return `---\n${nextLines.join("\n")}\n---${normalized.slice(end + closing[0].length)}`;
}
