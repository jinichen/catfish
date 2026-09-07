import { useEffect, useMemo, useState } from "react";
import {
  Books,
  CheckCircle,
  Copy,
  LinkSimple,
  ListChecks,
  MagnifyingGlass,
  WarningCircle,
} from "@phosphor-icons/react";
import { useWikiStore } from "../../store/wiki";
import WikiTree from "./WikiTree";
import {
  buildWikiRelationshipTasks,
  type WikiRelationshipTaskKind,
} from "./wikiRelationshipTasks";

export type WikiWorkspaceMode = "organize" | "browse";

const TASK_META: Record<
  WikiRelationshipTaskKind,
  { label: string; icon: typeof CheckCircle }
> = {
  pending: { label: "待确认", icon: CheckCircle },
  missing: { label: "缺少关系", icon: LinkSimple },
  duplicate: { label: "可能重复", icon: Copy },
};

export default function WikiOrganizer({
  mode,
  onModeChange,
}: {
  mode: WikiWorkspaceMode;
  onModeChange: (mode: WikiWorkspaceMode) => void;
}) {
  const files = useWikiStore((state) => state.files);
  const filesLoading = useWikiStore((state) => state.filesLoading);
  const filesError = useWikiStore((state) => state.filesError);
  const selectedPath = useWikiStore((state) => state.selectedPath);
  const loadFiles = useWikiStore((state) => state.loadFiles);
  const selectFile = useWikiStore((state) => state.selectFile);
  const [search, setSearch] = useState("");
  const [kindFilter, setKindFilter] = useState<WikiRelationshipTaskKind | "all">("all");

  useEffect(() => {
    if (files.length === 0 && !filesLoading) void loadFiles();
  }, [files.length, filesLoading, loadFiles]);

  const tasks = useMemo(() => buildWikiRelationshipTasks(files), [files]);
  const counts = useMemo(
    () => ({
      pending: tasks.filter((task) => task.kind === "pending").length,
      missing: tasks.filter((task) => task.kind === "missing").length,
      duplicate: tasks.filter((task) => task.kind === "duplicate").length,
    }),
    [tasks],
  );
  const visibleTasks = useMemo(() => {
    const query = search.trim().toLocaleLowerCase("zh-CN");
    return tasks.filter((task) => {
      if (kindFilter !== "all" && task.kind !== kindFilter) return false;
      return !query || `${task.title} ${task.detail}`.toLocaleLowerCase("zh-CN").includes(query);
    });
  }, [kindFilter, search, tasks]);

  useEffect(() => {
    if (mode !== "organize" || selectedPath || tasks.length === 0) return;
    void selectFile(tasks[0].file.rel_path);
  }, [mode, selectFile, selectedPath, tasks]);

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
      </div>

      {mode === "browse" ? (
        <div className="wiki-organizer__browse"><WikiTree /></div>
      ) : (
        <>
          <label className="wiki-organizer__search">
            <MagnifyingGlass size={20} aria-hidden="true" />
            <span className="sr-only">搜索整理任务</span>
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索关系任务"
            />
          </label>

          <div className="wiki-organizer__summary" aria-label="关系健康概览">
            {(Object.keys(TASK_META) as WikiRelationshipTaskKind[]).map((kind) => {
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
                  <Icon size={20} aria-hidden="true" />
                  <span>{TASK_META[kind].label}</span>
                  <strong>{counts[kind]}</strong>
                </button>
              );
            })}
          </div>

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
              return (
                <button
                  type="button"
                  key={task.id}
                  data-active={selectedPath === task.file.rel_path}
                  data-kind={task.kind}
                  onClick={() => void selectFile(task.file.rel_path)}
                >
                  <span className="wiki-organizer__task-icon">
                    <Icon size={20} aria-hidden="true" />
                  </span>
                  <span className="wiki-organizer__task-copy">
                    <strong>{task.title}</strong>
                    <small>{task.detail}</small>
                  </span>
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
