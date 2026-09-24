import type { RelatedRef, WikiConflict, WikiFileInfo } from "../../lib/tauri_wiki";
import { resolveWikiRef } from "../../lib/wikiResolve";

/**
 * 9/24 (鸿波「没有关系的还要强制确定关系, 不是很乱吗」) 重排:
 *   · 删掉「缺少关系」—— 关系是可选的, 三条写入产线也不再因为没关系判 pending
 *   · 同一个名字指向不明 (ambiguous) 合成一条任务: 「中电福富」一个撞名曾经
 *     报成 24 条"关系异常", 其实只要选一次它指谁
 *   · 待确认条目自己的关系问题在工作台里逐条标出, 不再另开 broken 任务重复列
 */
export type WikiRelationshipTaskKind = "conflict" | "pending" | "ambiguous" | "broken" | "duplicate";

export interface WikiRelationshipTask {
  id: string;
  kind: WikiRelationshipTaskKind;
  title: string;
  detail: string;
  file: WikiFileInfo;
  duplicatePaths?: string[];
  relationName?: string;
  /** kind === "conflict" 时: 哪个字段、两个值 */
  conflict?: WikiConflict;
  /** kind === "ambiguous" 时: 哪些条目写了这个名字 / 它可能指的条目 */
  refs?: WikiFileInfo[];
  candidates?: WikiFileInfo[];
}

/** 一条 frontmatter 关系的问题; 正文引用和没问题的返回 null。 */
export function relationIssue(relation: RelatedRef, files: WikiFileInfo[]): string | null {
  if (relation.source === "body") return null;
  const name = relation.name.trim();
  if (!name) return null;
  if (!relation.rel?.trim()) return "没写关系类型";
  const resolution = resolveWikiRef(name, files);
  if (resolution.kind === "miss") return "找不到这个条目";
  if (resolution.kind === "ambiguous") return `有 ${resolution.candidates.length} 个条目都叫这个名字`;
  return null;
}

/** `entity_type` → 类型; `rel:中电福富` → 与「中电福富」的关系 */
export function conflictFieldLabel(field: string): string {
  if (field.startsWith("rel:")) return `与「${field.slice(4)}」的关系`;
  if (field === "entity_type" || field === "concept_type") return "类型";
  return field;
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
  const isPending = (file: WikiFileInfo) => (file.ontology_status ?? "active") === "pending";

  const pending = files.filter(isPending).map((file): WikiRelationshipTask => {
    const typed = file.related.filter((relation) => relation.source !== "body");
    const issues = typed.filter((relation) => relationIssue(relation, files)).length;
    return {
      id: `pending:${file.rel_path}`,
      kind: "pending",
      title: `确认「${file.title}」`,
      detail: typed.length === 0
        ? "小鲶新建的条目，没有写关系（关系可选），看一眼就能确认"
        : issues > 0
          ? `小鲶写了 ${typed.length} 条关系，其中 ${issues} 条要改`
          : `小鲶写了 ${typed.length} 条关系，没问题就确认`,
      file,
    };
  });

  // 指向不明: 按名字合并。待确认条目也算进引用方 —— 选一次, 所有写这个名字的一起改。
  const ambiguousByName = new Map<string, { refs: WikiFileInfo[]; candidates: WikiFileInfo[] }>();
  const broken: WikiRelationshipTask[] = [];
  for (const file of files) {
    for (const relation of file.related) {
      if (relation.source === "body") continue;
      const name = relation.name.trim();
      if (!name) continue;
      const resolution = relation.rel?.trim() ? resolveWikiRef(name, files) : null;
      if (resolution?.kind === "ambiguous") {
        const group = ambiguousByName.get(name) ?? { refs: [], candidates: resolution.candidates };
        if (!group.refs.includes(file)) group.refs.push(file);
        ambiguousByName.set(name, group);
        continue;
      }
      if (isPending(file)) continue; // 在它自己的待确认任务里逐条标出
      const issue = relationIssue(relation, files);
      if (!issue) continue;
      broken.push({
        id: `broken:${file.rel_path}:${name}`,
        kind: "broken",
        title: `「${file.title}」的一条关系要改`,
        detail: `“${name}”：${issue}`,
        file,
        relationName: name,
      });
    }
  }
  const ambiguous = [...ambiguousByName.entries()].map(([name, group]): WikiRelationshipTask => ({
    id: `ambiguous:${name}`,
    kind: "ambiguous",
    title: `「${name}」指的是哪一个`,
    detail: `${group.refs.length} 处关系写了这个名字，${group.candidates.length} 个条目都叫它；选一次，全部一起改`,
    file: group.refs[0],
    relationName: name,
    refs: group.refs,
    candidates: group.candidates,
  }));

  const duplicates = duplicateGroups(files).map((group): WikiRelationshipTask => ({
    id: `duplicate:${group.map((file) => file.rel_path).sort().join("|")}`,
    kind: "duplicate",
    title: `核对「${group.map((file) => file.title).join(" / ")}」`,
    detail: `${group.length} 个名称或别名相同的条目，本轮只提示、不自动合并`,
    file: group[0],
    duplicatePaths: group.map((file) => file.rel_path),
  }));

  // 9/17 (semantica 第 2 条): 蒸馏跟已有内容打架 —— 盘上保留了旧值, 新值等员工定。
  // 排最前: 这是"两个说法哪个对", 比"还没定"更需要人。
  const conflicts = files.flatMap((file): WikiRelationshipTask[] =>
    (file.conflicts ?? []).map((conflict) => ({
      id: `conflict:${file.rel_path}:${conflict.field}`,
      kind: "conflict",
      title: `「${file.title}」的${conflictFieldLabel(conflict.field)}有两个说法`,
      detail: `现在是“${conflict.current}”，小鲶${conflict.seen ? `根据 ${conflict.seen} ` : ""}认为是“${conflict.proposed}”；选一个`,
      file,
      conflict,
    })),
  );

  return [...conflicts, ...ambiguous, ...pending, ...broken, ...duplicates];
}

/** 待确认里「没有要改的关系」的那些 —— 可以一次全部确认。 */
export function cleanPendingFiles(files: WikiFileInfo[]): WikiFileInfo[] {
  return files.filter(
    (file) =>
      (file.ontology_status ?? "active") === "pending" &&
      (file.conflicts?.length ?? 0) === 0 &&
      file.related.every((relation) => !relationIssue(relation, files)),
  );
}

/** 把关系里写的名字 from 换成 to (指向不明 → 选定的那个条目), 其它关系原样。 */
export function renameRelationTarget(relations: RelatedRef[], from: string, to: string): RelatedRef[] {
  return relations.map((relation) =>
    relation.source !== "body" && relation.name.trim() === from.trim() ? { ...relation, name: to } : relation,
  );
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
  /** 9/24: 只改关系、不代表员工已核对 (删一条 / 加一条 / 批量改名) → 状态原样 */
  options: { keepStatus?: boolean } = {},
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
      return options.keepStatus ? line : "ontology_status: active";
    }
    return line;
  });
  if (!foundRelated) nextLines.push(`related: [${serialized}]`);
  if (!foundStatus && !options.keepStatus) nextLines.push("ontology_status: active");
  return `---\n${nextLines.join("\n")}\n---${normalized.slice(end + closing[0].length)}`;
}
