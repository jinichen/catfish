import type { WikiFileInfo } from "../../lib/tauri";
import { resolveWikiRefOrNull } from "../../lib/wikiResolve";

export const DEFAULT_GRAPH_NEIGHBOR_LIMIT = 24;
export const GRAPH_NEIGHBOR_STEP = 20;

export interface WikiGraphRelation {
  sourcePath: string;
  targetPath: string;
  relation: string;
}

export interface WikiGraphModel {
  fileByPath: Map<string, WikiFileInfo>;
  relations: WikiGraphRelation[];
  outgoingByPath: Map<string, WikiGraphRelation[]>;
  incomingByPath: Map<string, WikiGraphRelation[]>;
  degreeByPath: Map<string, number>;
}

export interface WikiGraphOverviewItem {
  targetPath: string;
  title: string;
  relation: string;
  direction: "outgoing" | "incoming";
}

export function buildWikiGraphModel(files: WikiFileInfo[]): WikiGraphModel {
  const fileByPath = new Map(files.map((file) => [file.rel_path, file]));
  const relations: WikiGraphRelation[] = [];
  const outgoingByPath = new Map<string, WikiGraphRelation[]>();
  const incomingByPath = new Map<string, WikiGraphRelation[]>();
  const degreeByPath = new Map<string, number>();

  for (const file of files) {
    for (const related of file.related) {
      const target = resolveWikiRefOrNull(related.name, files);
      if (!target || target.rel_path === file.rel_path) continue;
      const relation: WikiGraphRelation = {
        sourcePath: file.rel_path,
        targetPath: target.rel_path,
        relation: related.rel?.trim() || "关联",
      };
      relations.push(relation);
      appendRelation(outgoingByPath, relation.sourcePath, relation);
      appendRelation(incomingByPath, relation.targetPath, relation);
      degreeByPath.set(relation.sourcePath, (degreeByPath.get(relation.sourcePath) ?? 0) + 1);
      degreeByPath.set(relation.targetPath, (degreeByPath.get(relation.targetPath) ?? 0) + 1);
    }
  }

  return { fileByPath, relations, outgoingByPath, incomingByPath, degreeByPath };
}

export function collectEgoPaths(model: WikiGraphModel, anchorPath: string): Set<string> {
  const paths = new Set<string>([anchorPath]);
  for (const relation of model.outgoingByPath.get(anchorPath) ?? []) {
    paths.add(relation.targetPath);
  }
  for (const relation of model.incomingByPath.get(anchorPath) ?? []) {
    paths.add(relation.sourcePath);
  }
  return paths;
}

export function limitWikiGraphPaths(
  model: WikiGraphModel,
  candidates: Set<string>,
  anchorPath: string | null,
  neighborLimit: number,
): Set<string> {
  const anchorIncluded = Boolean(anchorPath && candidates.has(anchorPath));
  const maxVisible = neighborLimit + (anchorIncluded ? 1 : 0);
  if (candidates.size <= maxVisible) return new Set(candidates);

  const directOutgoing = new Set(
    (anchorPath ? model.outgoingByPath.get(anchorPath) : undefined)?.map((item) => item.targetPath) ?? [],
  );
  const directIncoming = new Set(
    (anchorPath ? model.incomingByPath.get(anchorPath) : undefined)?.map((item) => item.sourcePath) ?? [],
  );
  const collator = new Intl.Collator("zh-CN");
  const ranked = [...candidates]
    .filter((path) => path !== anchorPath)
    .sort((left, right) => {
      const scoreDifference =
        graphPathPriority(model, right, directOutgoing, directIncoming) -
        graphPathPriority(model, left, directOutgoing, directIncoming);
      if (scoreDifference !== 0) return scoreDifference;
      return collator.compare(
        model.fileByPath.get(left)?.title ?? left,
        model.fileByPath.get(right)?.title ?? right,
      );
    });

  const visible = new Set<string>();
  if (anchorIncluded && anchorPath) visible.add(anchorPath);
  for (const path of ranked.slice(0, neighborLimit)) visible.add(path);
  return visible;
}

export function buildWikiGraphOverview(
  model: WikiGraphModel,
  anchorPath: string | null,
  visiblePaths: Set<string>,
  limit = 6,
): WikiGraphOverviewItem[] {
  if (!anchorPath) return [];
  const items: WikiGraphOverviewItem[] = [];
  const seen = new Set<string>();

  for (const relation of model.outgoingByPath.get(anchorPath) ?? []) {
    addOverviewItem(items, seen, model, visiblePaths, relation.targetPath, relation.relation, "outgoing");
  }
  for (const relation of model.incomingByPath.get(anchorPath) ?? []) {
    addOverviewItem(items, seen, model, visiblePaths, relation.sourcePath, relation.relation, "incoming");
  }
  return items.slice(0, limit);
}

function appendRelation(
  index: Map<string, WikiGraphRelation[]>,
  path: string,
  relation: WikiGraphRelation,
) {
  const current = index.get(path);
  if (current) current.push(relation);
  else index.set(path, [relation]);
}

function graphPathPriority(
  model: WikiGraphModel,
  path: string,
  directOutgoing: Set<string>,
  directIncoming: Set<string>,
): number {
  const directionScore = directOutgoing.has(path) ? 20_000 : directIncoming.has(path) ? 10_000 : 0;
  const kind = model.fileByPath.get(path)?.kind;
  const kindScore = kind === "concept" ? 200 : kind === "entity" ? 100 : 0;
  return directionScore + kindScore + (model.degreeByPath.get(path) ?? 0);
}

function addOverviewItem(
  items: WikiGraphOverviewItem[],
  seen: Set<string>,
  model: WikiGraphModel,
  visiblePaths: Set<string>,
  targetPath: string,
  relation: string,
  direction: "outgoing" | "incoming",
) {
  if (!visiblePaths.has(targetPath) || seen.has(targetPath)) return;
  const file = model.fileByPath.get(targetPath);
  if (!file) return;
  seen.add(targetPath);
  items.push({ targetPath, title: file.title, relation, direction });
}
