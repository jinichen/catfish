/** 条目页上的「这一条还有几件事要处理」(10/23)。
 *
 * 鸿波 10/23: 浏览页左边一排「待确认」, 点进去看不出为什么待确认, 也没有确认按钮 ——
 * 处理入口全在「关系整理」, 得换个页面再找到这一条。这里把跟本条目有关的任务直接摆出来:
 *   · 没对上原文的事实 → WikiUnverifiedPanel (逐条加回 / 丢掉)
 *   · 待确认 / 关系要改 → WikiRelationsEditor (改关系 + 「确认」按钮), 跟关系整理里同一个面板
 *   · 别的 (两个说法、指向不明、跨级 …) → 说明 + 「去关系整理处理」直接选中那条任务
 */
import { useMemo } from "react";
import { ArrowRight, WarningCircle } from "@phosphor-icons/react";
import { customHierarchy, useRelationVocab } from "../../lib/wikiRelationVocab";
import { useKeptUntyped } from "../../lib/wikiUntypedKept";
import type { WikiFileFull } from "../../lib/tauri_wiki";
import { useWikiStore } from "../../store/wiki";
import WikiRelationsEditor from "./WikiRelationsEditor";
import WikiUnverifiedPanel from "./WikiUnverifiedPanel";
import { buildWikiRelationshipTasks, RELATION_MISSING, type WikiRelationshipTask } from "./wikiRelationshipTasks";

/** 这些任务在条目页上就地处理; 其余的给「去关系整理」按钮 */
const RELATION_KINDS = new Set(["pending", "broken"]);
/** 一条任务管很多条目 (批量整理), 不在单个条目页上列 */
const BATCH_KINDS = new Set(["untyped", "duplicate"]);

/** 10/23: 关系问题合成一句 —— 中电福富一张卡 47 条「…的一条关系要改」逐条列出来是一面字墙,
 *  下面的关系编辑器本来就把有问题的那几行标出来了。待确认的那条说明照旧单列。 */
export function relationSummary(tasks: WikiRelationshipTask[]): string[] {
  const out = tasks.filter((t) => t.kind === "pending").map((t) => `${t.title}：${t.detail}`);
  const broken = tasks.filter((t) => t.kind === "broken");
  if (broken.length === 0) return out;
  const missing = broken.filter((t) => t.detail.includes(RELATION_MISSING)).length;
  const untyped = broken.filter((t) => t.detail.includes("没写关系类型")).length;
  const parts = [
    missing && `${missing} 条对方还没有条目`,
    untyped && `${untyped} 条没写关系类型`,
    broken.length - missing - untyped && `${broken.length - missing - untyped} 条指向不明`,
  ].filter(Boolean);
  out.push(`有 ${broken.length} 条关系要处理（${parts.join("、")}）：在下面「关系」里标出来的那几行逐条处理`);
  return out;
}

export function entryTasks(tasks: WikiRelationshipTask[], relPath: string): WikiRelationshipTask[] {
  return tasks.filter((t) => !BATCH_KINDS.has(t.kind) && t.file.rel_path === relPath);
}

export default function WikiEntryIssues({
  selectedFile,
  onOpenTask,
}: {
  selectedFile: WikiFileFull;
  onOpenTask?: (taskId: string) => void;
}) {
  const files = useWikiStore((state) => state.files);
  const keptUntyped = useKeptUntyped();
  const customRelations = useRelationVocab((state) => state.custom);
  const mine = useMemo(
    () => entryTasks(
      buildWikiRelationshipTasks(files, new Date(), keptUntyped, customHierarchy(customRelations)),
      selectedFile.info.rel_path,
    ),
    [files, keptUntyped, customRelations, selectedFile.info.rel_path],
  );
  if (mine.length === 0 || selectedFile.info.rel_path.startsWith("wiki-shared/")) return null;

  const unverified = mine.find((t) => t.kind === "unverified");
  const relation = mine.filter((t) => RELATION_KINDS.has(t.kind));
  const others = mine.filter((t) => t.kind !== "unverified" && !RELATION_KINDS.has(t.kind));

  return (
    <section className="wiki-entry-issues" aria-label="这一条待处理的事">
      <div className="wiki-entry-issues__head">
        <WarningCircle size={20} aria-hidden="true" />
        <strong>这一条还有 {(relation.length > 0 ? 1 : 0) + (unverified ? 1 : 0) + others.length} 件事要处理</strong>
      </div>
      {relationSummary(relation).map((line) => (
        <p key={line} className="wiki-entry-issues__why">{line}</p>
      ))}
      {relation.length > 0 && (
        <WikiRelationsEditor key={selectedFile.info.rel_path} selectedFile={selectedFile} onLater={null} />
      )}
      {unverified && <WikiUnverifiedPanel key={unverified.id} task={unverified} onLater={null} />}
      {others.map((t) => (
        <div key={t.id} className="wiki-entry-issues__other">
          <span><strong>{t.title}</strong><small>{t.detail}</small></span>
          {onOpenTask && (
            <button type="button" className="wiki-workbench__secondary" onClick={() => onOpenTask(t.id)}>
              去关系整理处理 <ArrowRight size={16} aria-hidden="true" />
            </button>
          )}
        </div>
      ))}
    </section>
  );
}
