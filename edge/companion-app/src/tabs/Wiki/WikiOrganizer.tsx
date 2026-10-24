import { useEffect, useMemo, useState } from "react";
import {
  ArrowsLeftRight,
  Books,
  CheckCircle,
  ClockCounterClockwise,
  SealWarning,
  Copy,
  LinkSimple,
  ListChecks,
  MagnifyingGlass,
  Tag,
  TreeStructure,
  WarningCircle,
} from "@phosphor-icons/react";
import { useWikiStore } from "../../store/wiki";
import {
  wikiMigrateLegacyRelations,
  wikiReadFile,
  wikiUpdateFile,
  type WikiRelationMigrationResult,
} from "../../lib/tauri_wiki";
import WikiTree from "./WikiTree";
import { useKeptUntyped } from "../../lib/wikiUntypedKept";
import { customHierarchy, useRelationVocab } from "../../lib/wikiRelationVocab";
import WikiRelationTypesModal from "./WikiRelationTypesModal";
import { useHierarchyModel } from "./WikiHierarchyView";
import {
  buildConfirmedWikiContent,
  buildWikiRelationshipTasks,
  cleanPendingFiles,
  hasLegacyWikiRelations,
  type WikiRelationshipTaskKind,
} from "./wikiRelationshipTasks";

export type WikiWorkspaceMode = "organize" | "browse" | "hierarchy";

const TASK_META: Record<
  WikiRelationshipTaskKind,
  { label: string; icon: typeof CheckCircle }
> = {
  conflict: { label: "两个说法", icon: ArrowsLeftRight },
  unverified: { label: "没对上原文", icon: SealWarning },
  ambiguous: { label: "指向不明", icon: LinkSimple },
  pending: { label: "待确认", icon: CheckCircle },
  broken: { label: "关系要改", icon: WarningCircle },
  untyped: { label: "没标类型", icon: Tag },
  skiplevel: { label: "跨级重复", icon: TreeStructure },
  multiparent: { label: "多个上级", icon: TreeStructure },
  stale: { label: "进度待核对", icon: ClockCounterClockwise },
  duplicate: { label: "可能重复", icon: Copy },
};

export default function WikiOrganizer({
  mode,
  onModeChange,
  taskId,
  onSelectTask,
  hierarchyRoot = null,
  onHierarchyRoot,
}: {
  mode: WikiWorkspaceMode;
  onModeChange: (mode: WikiWorkspaceMode) => void;
  taskId: string | null;
  onSelectTask: (taskId: string | null) => void;
  /** 10/23「层级」: 选中的顶层 */
  hierarchyRoot?: string | null;
  onHierarchyRoot?: (path: string) => void;
}) {
  const files = useWikiStore((state) => state.files);
  const filesLoading = useWikiStore((state) => state.filesLoading);
  const filesError = useWikiStore((state) => state.filesError);
  const loadFiles = useWikiStore((state) => state.loadFiles);
  const selectFile = useWikiStore((state) => state.selectFile);
  const [search, setSearch] = useState("");
  const [kindFilter, setKindFilter] = useState<WikiRelationshipTaskKind | "all">("all");
  const [migration, setMigration] = useState<WikiRelationMigrationResult | null>(null);
  const [migrationRunning, setMigrationRunning] = useState(false);
  const [migrationError, setMigrationError] = useState<string | null>(null);

  useEffect(() => {
    if (files.length === 0 && !filesLoading) void loadFiles();
  }, [files.length, filesLoading, loadFiles]);

  const keptUntyped = useKeptUntyped();
  const hierarchy = useHierarchyModel();
  // 10/23: 关系类型 (内置 + 自定义); 打开知识体系就加载一次, 下拉 / 建议都从 store 拿
  const customRelations = useRelationVocab((state) => state.custom);
  const loadRelationVocab = useRelationVocab((state) => state.load);
  const [showRelationTypes, setShowRelationTypes] = useState(false);
  useEffect(() => {
    void loadRelationVocab();
  }, [loadRelationVocab]);
  const tasks = useMemo(
    () => buildWikiRelationshipTasks(files, new Date(), keptUntyped, customHierarchy(customRelations)),
    [files, keptUntyped, customRelations],
  );
  const hasLegacyRelations = useMemo(() => hasLegacyWikiRelations(files), [files]);
  const counts = useMemo(() => {
    const out: Record<WikiRelationshipTaskKind, number> = { conflict: 0, unverified: 0, ambiguous: 0, pending: 0, broken: 0, untyped: 0, skiplevel: 0, multiparent: 0, stale: 0, duplicate: 0 };
    for (const task of tasks) out[task.kind] += 1;
    return out;
  }, [tasks]);
  const visibleKinds = (Object.keys(TASK_META) as WikiRelationshipTaskKind[]).filter((kind) => counts[kind] > 0);
  const cleanPending = useMemo(() => cleanPendingFiles(files), [files]);
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);

  // 9/24: 待确认里关系都没问题的 (含一条关系都没写的) —— 一次确认完, 不用逐条点。
  const confirmAllClean = async () => {
    setBulkRunning(true);
    setBulkError(null);
    const failed: string[] = [];
    for (const file of cleanPending) {
      try {
        const full = await wikiReadFile(file.rel_path);
        await wikiUpdateFile(file.rel_path, buildConfirmedWikiContent(full.content, full.info.related, full.info));
      } catch (error) {
        failed.push(`${file.title}: ${String(error)}`);
      }
    }
    await loadFiles();
    setBulkRunning(false);
    if (failed.length > 0) setBulkError(failed.join("\n"));
  };
  const visibleTasks = useMemo(() => {
    const query = search.trim().toLocaleLowerCase("zh-CN");
    return tasks.filter((task) => {
      if (kindFilter !== "all" && task.kind !== kindFilter) return false;
      return !query || `${task.title} ${task.detail}`.toLocaleLowerCase("zh-CN").includes(query);
    });
  }, [kindFilter, search, tasks]);

  const previewLegacyMigration = async () => {
    setMigrationRunning(true);
    setMigrationError(null);
    try {
      setMigration(await wikiMigrateLegacyRelations(true));
    } catch (error) {
      setMigrationError(String(error));
    } finally {
      setMigrationRunning(false);
    }
  };

  const executeLegacyMigration = async () => {
    if (!migration || migration.converted_relations === 0 || migrationRunning) return;
    setMigrationRunning(true);
    setMigrationError(null);
    try {
      setMigration(await wikiMigrateLegacyRelations(false));
      await loadFiles();
    } catch (error) {
      setMigrationError(String(error));
    } finally {
      setMigrationRunning(false);
    }
  };

  useEffect(() => {
    if (mode !== "organize" || filesLoading) return;
    if (tasks.length === 0) {
      if (taskId) onSelectTask(null);
      return;
    }
    if (taskId && tasks.some((task) => task.id === taskId)) return;
    const first = tasks[0];
    onSelectTask(first.id);
    void selectFile(first.file.rel_path);
  }, [filesLoading, mode, onSelectTask, selectFile, taskId, tasks]);

  return (
    <div className="wiki-organizer">
      <div className="wiki-organizer__switch" role="tablist" aria-label="知识体系视图">
        <button
          type="button"
          role="tab"
          aria-selected={mode === "organize"}
          data-active={mode === "organize"}
          onClick={() => onModeChange("organize")}
        >
          <ListChecks size={18} aria-hidden="true" />
          关系整理
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "browse"}
          data-active={mode === "browse"}
          onClick={() => onModeChange("browse")}
        >
          <Books size={18} aria-hidden="true" />
          全部知识
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "hierarchy"}
          data-active={mode === "hierarchy"}
          onClick={() => onModeChange("hierarchy")}
        >
          <TreeStructure size={18} aria-hidden="true" />
          层级
        </button>
      </div>

      {mode === "browse" ? (
        <div className="wiki-organizer__browse"><WikiTree /></div>
      ) : mode === "hierarchy" ? (
        <div className="wiki-organizer__roots" aria-label="层级的顶层">
          {hierarchy.roots.length === 0 && <p className="wiki-organizer__roots-empty">还没有上下级关系</p>}
          {hierarchy.roots.map((r) => (
            <button
              type="button"
              key={r.path}
              data-active={(hierarchyRoot ?? hierarchy.roots[0]?.path) === r.path}
              onClick={() => onHierarchyRoot?.(r.path)}
            >
              <span>{hierarchy.byPath.get(r.path)?.title ?? r.path}</span>
              <small>{r.size}</small>
            </button>
          ))}
        </div>
      ) : (
        <>
          {/* 10/24: 搜索框和「关系类型」放一行 (原来各占一行) */}
          <div className="wiki-organizer__searchrow">
            <label className="wiki-organizer__search">
              <MagnifyingGlass size={18} aria-hidden="true" />
              <span className="sr-only">搜索整理任务</span>
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜索关系任务"
              />
            </label>
            <button
              type="button"
              className="wiki-organizer__vocab"
              onClick={() => setShowRelationTypes(true)}
              title={`关系类型${customRelations.length > 0 ? `（含 ${customRelations.length} 个自定义）` : ""}`}
            >
              <Tag size={16} aria-hidden="true" />
              类型
            </button>
          </div>
          {showRelationTypes && <WikiRelationTypesModal onClose={() => setShowRelationTypes(false)} />}

          <div className="wiki-organizer__summary" aria-label="关系健康概览">
            {visibleKinds.map((kind) => {
              const Icon = TASK_META[kind].icon;
              return (
                <button
                  type="button"
                  key={kind}
                  data-active={kindFilter === kind}
                  data-kind={kind}
                  onClick={() => setKindFilter(kindFilter === kind ? "all" : kind)}
                  aria-pressed={kindFilter === kind}
                >
                  <Icon size={15} aria-hidden="true" />
                  <span>{TASK_META[kind].label}</span>
                  <strong>{counts[kind]}</strong>
                </button>
              );
            })}
          </div>

          {cleanPending.length > 1 && (
            <div className="wiki-organizer__bulk">
              <span>{cleanPending.length} 条待确认的关系都没问题</span>
              <button type="button" onClick={() => void confirmAllClean()} disabled={bulkRunning}>
                {bulkRunning ? "确认中…" : "全部确认"}
              </button>
              {bulkError && <small className="wiki-organizer__migration-error">{bulkError}</small>}
            </div>
          )}

          {hasLegacyRelations && (
            <section className="wiki-organizer__migration" aria-label="旧关系格式整理">
              <div>
                <strong>发现旧关系格式</strong>
                <span>只补通用“关联”，不改变目标或猜测具体语义。</span>
              </div>
              <button type="button" onClick={() => void previewLegacyMigration()} disabled={migrationRunning}>
                {migrationRunning ? "处理中…" : "检查旧关系"}
              </button>
              {migration && (
                <div className="wiki-organizer__migration-result" aria-live="polite">
                  <span>
                    {migration.dry_run
                      ? `扫描 ${migration.scanned_files} 个文件，发现 ${migration.converted_relations} 条可整理关系。`
                      : `已整理 ${migration.converted_relations} 条旧关系，修改 ${migration.changed_files} 个文件。`}
                  </span>
                  {migration.dry_run && migration.converted_relations > 0 && (
                    <button type="button" onClick={() => void executeLegacyMigration()} disabled={migrationRunning}>
                      一次性整理
                    </button>
                  )}
                  {!migration.dry_run && migration.backup_dir && <small>原文件已备份：{migration.backup_dir}</small>}
                </div>
              )}
              {migrationError && <span className="wiki-organizer__migration-error">整理失败：{migrationError}</span>}
            </section>
          )}

          <div className="wiki-organizer__task-heading">
            <div>
              <h3>待处理任务</h3>
              <span>{visibleTasks.length} 项</span>
            </div>
            {kindFilter !== "all" && (
              <button type="button" onClick={() => setKindFilter("all")}>查看全部</button>
            )}
          </div>

          {filesLoading && <div className="wiki-organizer__status">正在整理知识关系…</div>}
          {filesError && (
            <div className="wiki-organizer__status wiki-organizer__status--error">
              <WarningCircle size={20} aria-hidden="true" />
              {filesError}
            </div>
          )}
          {!filesLoading && !filesError && visibleTasks.length === 0 && (
            <div className="wiki-organizer__complete">
              <CheckCircle size={32} weight="duotone" aria-hidden="true" />
              <strong>当前关系已整理完成</strong>
              <span>新知识进入后，会自动出现在这里。</span>
              <button type="button" onClick={() => onModeChange("browse")}>
                去全部知识关联行动
              </button>
            </div>
          )}

          <div className="wiki-organizer__tasks">
            {visibleTasks.map((task) => {
              const Icon = TASK_META[task.kind].icon;
              const open = () => { onSelectTask(task.id); void selectFile(task.file.rel_path); };
              // 10/24: 不用 <button> —— WKWebView 里按钮装多行文字 (两行截断的标题 + 一行灰字) 时
              // 高度算短, 灰字掉到卡片框外 (换 grid 也没用); 普通块元素一定随内容长高
              return (
                <div
                  role="button"
                  tabIndex={0}
                  key={task.id}
                  data-active={taskId === task.id}
                  data-kind={task.kind}
                  onClick={open}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } }}
                >
                  <span className="wiki-organizer__task-icon">
                    <Icon size={20} aria-hidden="true" />
                  </span>
                  <span className="wiki-organizer__task-copy">
                    <strong>{task.title}</strong>
                    <small>{task.detail}</small>
                  </span>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
