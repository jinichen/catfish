import { useMemo, useState } from "react";
import {
  CheckCircle,
  ArrowsLeftRight,
  ClockCounterClockwise,
  FileText,
  LinkSimple,
  SpinnerGap,
  WarningCircle,
} from "@phosphor-icons/react";
import { wikiResolveConflict, wikiUpdateFile } from "../../lib/tauri_wiki";
import { useWikiStore } from "../../store/wiki";
import { buildWikiRelationshipTasks, conflictFieldLabel, markReviewedContent, statusLines } from "./wikiRelationshipTasks";
import WikiActionPanel from "./WikiActionPanel";
import WikiAmbiguousPanel from "./WikiAmbiguousPanel";
import WikiRelationsEditor from "./WikiRelationsEditor";
import WikiUntypedBatch from "./WikiUntypedBatch";
import { useKeptUntyped } from "../../lib/wikiUntypedKept";
import { customHierarchy, useRelationVocab } from "../../lib/wikiRelationVocab";
import WikiHierarchyIssuePanel from "./WikiHierarchyIssuePanel";
import WikiUnverifiedPanel from "./WikiUnverifiedPanel";
import WikiDuplicatePanel from "./WikiDuplicatePanel";
import { BUILTIN_HIERARCHY } from "./wikiHierarchy";
import { wikiSourceLabel } from "./wikiLabels";

function evidenceExcerpt(body: string): string {
  return body
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\[\[([^\]]+)\]\]/g, "$1")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 240);
}

const HEADER_HINT: Record<string, string> = {
  conflict: "小鲶后来读到的跟现在记的不一样，选一个。",
  pending: "小鲶新建的条目。看一眼关系对不对，对就确认；关系可以没有。",
  broken: "有一条关系要处理：改类型、删掉，或给对方新建条目。",
  skiplevel: "已经挂在中间一级下面，又直挂了更上一级 —— 直挂的那条多余，层级图上会多出一条斜线。",
  multiparent: "挂在好几个上级下面。确认哪个才是它的上级，其余去掉（或者到条目里改成别的关系）。",
  untyped: "这些关系只写了「关联」，说不清是什么关系。让小鲶一次建议，看过再采纳；拿不准的点「保持关联」，以后不再列。不点就不写入。",
  stale: "这条记着进度，但很久没更新了。核对正文里的进度还对不对。",
  duplicate: "几条条目名字（或别名）一样，看一眼是不是同一个东西：是就合并，不是就改名。",
};

/** 9/24 重做: 任务按 id 选中 (一个名字指向不明 = 一条任务, 不再跟着某个文件走)。 */
export default function WikiRelationshipWorkbench({
  taskId,
  onSelectTask,
}: {
  taskId: string | null;
  onSelectTask: (taskId: string) => void;
}) {
  const files = useWikiStore((state) => state.files);
  const selectedFile = useWikiStore((state) => state.selectedFile);
  const selectedLoading = useWikiStore((state) => state.selectedLoading);
  const selectedError = useWikiStore((state) => state.selectedError);
  const selectFile = useWikiStore((state) => state.selectFile);
  const loadFiles = useWikiStore((state) => state.loadFiles);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const keptUntyped = useKeptUntyped();
  const customRelations = useRelationVocab((state) => state.custom);
  const tasks = useMemo(
    () => buildWikiRelationshipTasks(files, new Date(), keptUntyped, customHierarchy(customRelations)),
    [files, keptUntyped, customRelations],
  );
  const selectedTask = useMemo(
    () => tasks.find((task) => task.id === taskId)
      ?? tasks.find((task) =>
        task.kind !== "ambiguous" && task.kind !== "untyped" &&
        (task.file.rel_path === selectedFile?.info.rel_path ||
          task.duplicatePaths?.includes(selectedFile?.info.rel_path ?? ""))),
    [selectedFile?.info.rel_path, taskId, tasks],
  );
  const next = tasks.find((task) => task.id !== selectedTask?.id);
  const onLater = next ? () => { onSelectTask(next.id); void selectFile(next.file.rel_path); } : null;

  if (selectedTask?.kind === "ambiguous") {
    return (
      <div className="wiki-workbench">
        <header className="wiki-workbench__header">
          <h2>「{selectedTask.relationName}」指的是哪一个</h2>
          <p>同一个名字被几个条目占着，写了这个名字的关系都连不上。</p>
        </header>
        <WikiAmbiguousPanel key={selectedTask.id} task={selectedTask} onLater={onLater} />
      </div>
    );
  }
  if (selectedTask?.kind === "untyped") {
    return (
      <div className="wiki-workbench">
        <header className="wiki-workbench__header">
          <h2>{selectedTask.title}</h2>
          <p>{HEADER_HINT.untyped}</p>
        </header>
        <WikiUntypedBatch task={selectedTask} />
      </div>
    );
  }
  if (selectedLoading) {
    return <div className="wiki-workbench__empty"><SpinnerGap className="wiki-spin" size={30} />正在读取关系…</div>;
  }
  if (selectedError) {
    return <div className="wiki-workbench__empty wiki-workbench__empty--error"><WarningCircle size={30} />{selectedError}</div>;
  }
  if (!selectedFile) {
    return (
      <div className="wiki-workbench__empty">
        <LinkSimple size={36} weight="duotone" aria-hidden="true" />
        <strong>从左侧选一项</strong>
        <span>逐条处理后，知识图谱会自动变得清晰。</span>
      </div>
    );
  }

  const info = selectedFile.info;

  // 9/17 (semantica 第 2 条): 蒸馏想改类型/关系, 但跟盘上已有的不一样 —— 写入侧
  // 保留了旧值、把新值记进 conflicts。这里员工二选一, 后端改文件 + 清掉那条冲突。
  const resolveConflict = async (field: string, takeProposed: boolean) => {
    setSaving(true);
    setSaveError(null);
    try {
      await wikiResolveConflict(info.rel_path, field, takeProposed);
      await loadFiles();
      await selectFile(info.rel_path);
    } catch (error) {
      setSaveError(String(error));
    } finally {
      setSaving(false);
    }
  };

  // 9/24: 进度核对 —— "还对" 只把 updated 改成今天, 正文不动
  const markReviewed = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      await wikiUpdateFile(info.rel_path, markReviewedContent(selectedFile.content));
      await loadFiles();
      await selectFile(info.rel_path);
    } catch (error) {
      setSaveError(String(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="wiki-workbench">
      <header className="wiki-workbench__header">
        <span className={`wiki-kind-badge wiki-kind-badge--${info.kind}`}>
          {info.kind === "entity" ? "实体" : info.kind === "concept" ? "概念" : "记录"}
        </span>
        <h2>{info.title}</h2>
        <p>{HEADER_HINT[selectedTask?.kind ?? ""] ?? "这条知识和其它条目的关系。"}</p>
        {saveError && <p className="wiki-workbench__error">{saveError}</p>}
      </header>

      {(info.conflicts?.length ?? 0) > 0 && (
        <section className="wiki-workbench__section">
          <div className="wiki-workbench__section-title">
            <ArrowsLeftRight size={22} aria-hidden="true" />
            <div><h3>两个说法</h3><p>小鲶后来读到的跟现在记的不一样。文件里保留的是现在的值，选一个。</p></div>
          </div>
          <div className="wiki-workbench__conflicts">
            {info.conflicts!.map((conflict) => {
              // 10/23: 包含 ↔ 隶属 这种上下级方向正好反过来的, 多半是小鲶把对方卡上的关系写到了这张卡上
              const kinds = { ...BUILTIN_HIERARCHY, ...customHierarchy(customRelations) };
              const reversed = conflict.field.startsWith("rel:") &&
                new Set([kinds[conflict.current], kinds[conflict.proposed]]).size === 2 &&
                [kinds[conflict.current], kinds[conflict.proposed]].every(Boolean);
              return (
              <div className="wiki-workbench__conflict" key={conflict.field}>
                <div className="wiki-workbench__conflict-field">{conflictFieldLabel(conflict.field)}</div>
                <button
                  type="button"
                  className="wiki-workbench__secondary"
                  disabled={saving}
                  onClick={() => void resolveConflict(conflict.field, false)}
                  title="保留现在的值"
                >
                  现在：<strong>{conflict.current}</strong>
                  {reversed && <small className="wiki-workbench__conflict-tip">推荐：新说法上下级写反了</small>}
                </button>
                <button
                  type="button"
                  className="wiki-workbench__secondary"
                  disabled={saving}
                  onClick={() => void resolveConflict(conflict.field, true)}
                  title={conflict.seen ? `来自 ${conflict.seen}` : "小鲶提出的新值"}
                >
                  改成：<strong>{conflict.proposed}</strong>
                  {conflict.seen && <small>来自{wikiSourceLabel(conflict.seen)}</small>}
                </button>
              </div>
              );
            })}
          </div>
        </section>
      )}

      {selectedTask?.kind === "stale" && (
        <section className="wiki-workbench__section">
          <div className="wiki-workbench__section-title">
            <ClockCounterClockwise size={22} aria-hidden="true" />
            <div>
              <h3>核对进度</h3>
              <p>
                上次更新是 {info.updated}（{selectedTask.staleDays} 天前）。正文里记的进度如下;
                不对就到「全部知识」里打开这条编辑, 还对就点确认。
              </p>
            </div>
          </div>
          <ul className="wiki-workbench__status-lines">
            {statusLines(selectedFile.body).map((line) => <li key={line}>{line}</li>)}
          </ul>
          <div className="wiki-relation-editor__actions">
            {onLater && <button type="button" className="wiki-workbench__secondary" onClick={onLater}>稍后处理</button>}
            <button type="button" className="wiki-workbench__primary" disabled={saving} onClick={() => void markReviewed()}>
              {saving ? <SpinnerGap className="wiki-spin" size={20} /> : <CheckCircle size={20} />}
              进度还对，标记已核对
            </button>
          </div>
        </section>
      )}

      {selectedTask && (selectedTask.kind === "skiplevel" || selectedTask.kind === "multiparent") ? (
        <WikiHierarchyIssuePanel key={selectedTask.id} task={selectedTask} onLater={onLater} />
      ) : selectedTask?.kind === "unverified" ? (
        <WikiUnverifiedPanel key={selectedTask.id} task={selectedTask} onLater={onLater} />
      ) : selectedTask?.kind === "duplicate" ? (
        <WikiDuplicatePanel key={selectedTask.id} task={selectedTask} onLater={onLater} />
      ) : (
        <WikiRelationsEditor key={info.rel_path} selectedFile={selectedFile} onLater={onLater} />
      )}

      <WikiActionPanel selectedFile={selectedFile} />

      <section className="wiki-workbench__section">
        <div className="wiki-workbench__section-title">
          <FileText size={22} aria-hidden="true" />
          <div><h3>证据与来源</h3><p>确认关系前，可以先核对原始文字。</p></div>
        </div>
        <div className="wiki-workbench__evidence">
          <div>
            <strong>{info.sources[0] || info.title}</strong>
            <span>{info.authored_by === "employee" ? "已经人工核对" : "建议人工核对"}</span>
          </div>
          <blockquote>{evidenceExcerpt(selectedFile.body) || "该条目暂无正文证据。"}</blockquote>
        </div>
      </section>
    </div>
  );
}
