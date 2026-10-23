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
import { buildWikiRelationshipTasks, type WikiRelationshipTask } from "./wikiRelationshipTasks";

/** 这些任务在条目页上就地处理; 其余的给「去关系整理」按钮 */
const RELATION_KINDS = new Set(["pending", "broken"]);
/** 一条任务管很多条目 (批量整理), 不在单个条目页上列 */
const BATCH_KINDS = new Set(["untyped", "duplicate"]);

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
        <strong>这一条还有 {mine.length} 件事要处理</strong>
      </div>
      {relation.map((t) => (
        <p key={t.id} className="wiki-entry-issues__why">{t.title}：{t.detail}</p>
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
