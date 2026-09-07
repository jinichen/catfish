import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  Check,
  CheckCircle,
  FileText,
  LinkSimple,
  SpinnerGap,
  WarningCircle,
} from "@phosphor-icons/react";
import { wikiUpdateFile, type RelatedRef } from "../../lib/tauri";
import { resolveWikiRefOrNull } from "../../lib/wikiResolve";
import { useWikiStore } from "../../store/wiki";
import {
  buildConfirmedWikiContent,
  buildWikiRelationshipTasks,
  relationTypeOptions,
} from "./wikiRelationshipTasks";
import WikiActionPanel from "./WikiActionPanel";

function evidenceExcerpt(body: string): string {
  return body
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\[\[([^\]]+)\]\]/g, "$1")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 240);
}

export default function WikiRelationshipWorkbench() {
  const files = useWikiStore((state) => state.files);
  const selectedFile = useWikiStore((state) => state.selectedFile);
  const selectedLoading = useWikiStore((state) => state.selectedLoading);
  const selectedError = useWikiStore((state) => state.selectedError);
  const selectFile = useWikiStore((state) => state.selectFile);
  const loadFiles = useWikiStore((state) => state.loadFiles);
  const [relationType, setRelationType] = useState("关联");
  const [targetPath, setTargetPath] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const tasks = useMemo(() => buildWikiRelationshipTasks(files), [files]);
  const selectedTask = useMemo(
    () => tasks.find(
      (task) =>
        task.file.rel_path === selectedFile?.info.rel_path ||
        task.duplicatePaths?.includes(selectedFile?.info.rel_path ?? ""),
    ),
    [selectedFile?.info.rel_path, tasks],
  );
  const relationTypes = useMemo(() => relationTypeOptions(files), [files]);
  const targets = useMemo(
    () => files.filter(
      (file) =>
        file.rel_path !== selectedFile?.info.rel_path &&
        (file.ontology_status ?? "active") === "active" &&
        file.kind !== "query",
    ),
    [files, selectedFile?.info.rel_path],
  );

  useEffect(() => {
    setSaveError(null);
    setSaved(false);
    const first = selectedFile?.info.related[0];
    setRelationType(first?.rel?.trim() || "关联");
    const target = first ? resolveWikiRefOrNull(first.name, files) : null;
    setTargetPath(target?.rel_path ?? targets[0]?.rel_path ?? "");
  }, [files, selectedFile?.frontmatter, selectedFile?.info.rel_path, selectedFile?.info.related, targets]);

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
        <strong>从左侧选择一项关系任务</strong>
        <span>逐条确认后，知识图谱会自动变得清晰。</span>
      </div>
    );
  }

  const info = selectedFile.info;
  const selectedTarget = targets.find((file) => file.rel_path === targetPath);
  const duplicateFiles = selectedTask?.duplicatePaths
    ?.map((path) => files.find((file) => file.rel_path === path))
    .filter(Boolean) ?? [];

  const confirmRelation = async () => {
    if (!selectedTarget || !relationType.trim()) return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const nextRelation: RelatedRef = { name: selectedTarget.title, rel: relationType.trim() };
      const existing = info.related.filter((relation) => {
        const target = resolveWikiRefOrNull(relation.name, files);
        return target?.rel_path !== selectedTarget.rel_path;
      });
      const content = buildConfirmedWikiContent(selectedFile.content, [...existing, nextRelation]);
      await wikiUpdateFile(info.rel_path, content);
      await loadFiles();
      await selectFile(info.rel_path);
      setSaved(true);
    } catch (error) {
      setSaveError(String(error));
    } finally {
      setSaving(false);
    }
  };

  const selectNextTask = () => {
    const next = tasks.find((task) => task.id !== selectedTask?.id);
    if (next) void selectFile(next.file.rel_path);
  };

  return (
    <div className="wiki-workbench">
      <header className="wiki-workbench__header">
        <span className={`wiki-kind-badge wiki-kind-badge--${info.kind}`}>
          {info.kind === "entity" ? "实体" : info.kind === "concept" ? "概念" : "记录"}
        </span>
        <h2>{info.title}</h2>
        <p>
          {selectedTask?.kind === "duplicate"
            ? "名称相近的条目需要人工核对，避免错误合并。"
            : "核对这条知识与人员、部门、项目或制度之间的关系。"}
        </p>
      </header>

      {selectedTask?.kind === "duplicate" ? (
        <section className="wiki-workbench__section">
          <div className="wiki-workbench__section-title">
            <WarningCircle size={22} aria-hidden="true" />
            <div><h3>可能重复的条目</h3><p>本轮不会自动合并，请逐条查看内容后再决定。</p></div>
          </div>
          <div className="wiki-workbench__duplicate-list">
            {duplicateFiles.map((file) => file && (
              <button type="button" key={file.rel_path} onClick={() => void selectFile(file.rel_path)}>
                <span><strong>{file.title}</strong><small>{file.subtype || "未标注类型"}</small></span>
                <ArrowRight size={18} aria-hidden="true" />
              </button>
            ))}
          </div>
        </section>
      ) : (
        <section className="wiki-workbench__section wiki-workbench__section--editor">
          <div className="wiki-workbench__section-title">
            <LinkSimple size={22} aria-hidden="true" />
            <div><h3>编辑关系</h3><p>用一句话确认，系统会写入知识文件并更新图谱。</p></div>
          </div>
          <div className="wiki-relation-editor">
            <div className="wiki-relation-editor__subject" title={info.title}>{info.title}</div>
            <select value={relationType} onChange={(event) => setRelationType(event.target.value)} aria-label="关系类型">
              {relationTypes.map((type) => <option key={type} value={type}>{type}</option>)}
            </select>
            <select value={targetPath} onChange={(event) => setTargetPath(event.target.value)} aria-label="关系目标">
              {targets.length === 0 && <option value="">暂无可关联条目</option>}
              {targets.map((target) => <option key={target.rel_path} value={target.rel_path}>{target.title}</option>)}
            </select>
          </div>
          <div className="wiki-relation-editor__actions">
            {saveError && <span className="wiki-workbench__error">{saveError}</span>}
            {saved && <span className="wiki-workbench__success"><CheckCircle size={18} />关系已更新</span>}
            <button type="button" className="wiki-workbench__secondary" onClick={selectNextTask} disabled={tasks.length < 2}>
              稍后处理
            </button>
            <button type="button" className="wiki-workbench__primary" disabled={!selectedTarget || saving} onClick={() => void confirmRelation()}>
              {saving ? <SpinnerGap className="wiki-spin" size={20} /> : <Check size={20} />}
              确认关系
            </button>
          </div>
        </section>
      )}

      <section className="wiki-workbench__section">
        <div className="wiki-workbench__section-title">
          <CheckCircle size={22} aria-hidden="true" />
          <div><h3>当前关系</h3><p>{info.related.length > 0 ? `已记录 ${info.related.length} 条关系` : "尚未记录关系"}</p></div>
        </div>
        {info.related.length > 0 ? (
          <div className="wiki-workbench__relations">
            {info.related.map((relation, index) => (
              <div key={`${relation.name}:${relation.rel ?? index}`}>
                <span>{relation.rel || "关联"}</span><strong>{relation.name}</strong>
              </div>
            ))}
          </div>
        ) : <div className="wiki-workbench__quiet">完成上面的确认后，这里会出现第一条关系。</div>}
      </section>

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
