import { useEffect, useState } from "react";
import { Check, Lightning, Play, SpinnerGap, X } from "@phosphor-icons/react";
import { wikiUpdateFile } from "../../lib/tauri";
import {
  wikiExecuteAction,
  wikiListActions,
  wikiResolveActions,
  parseWikiActionRefs,
  writeWikiActionRefs,
  type WikiActionBinding,
} from "../../lib/wikiActions";
import { useWikiStore } from "../../store/wiki";
import type { WikiFileFull } from "../../lib/tauri_wiki";

export default function WikiActionPanel({
  selectedFile,
  readOnly = false,
}: {
  selectedFile: WikiFileFull;
  readOnly?: boolean;
}) {
  const loadFiles = useWikiStore((state) => state.loadFiles);
  const selectFile = useWikiStore((state) => state.selectFile);
  const [actionRefs, setActionRefs] = useState<string[]>([]);
  const [actionCatalog, setActionCatalog] = useState<WikiActionBinding[]>([]);
  const [actionBindings, setActionBindings] = useState<WikiActionBinding[]>([]);
  const [actionCatalogLoading, setActionCatalogLoading] = useState(true);
  const [selectedActionId, setSelectedActionId] = useState("");
  const [actionsLoading, setActionsLoading] = useState(false);
  const [actionSaving, setActionSaving] = useState(false);
  const [actionRunId, setActionRunId] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  useEffect(() => {
    setActionRefs(parseWikiActionRefs(selectedFile.frontmatter));
    setSelectedActionId("");
    setActionBindings([]);
    setActionMessage(null);
  }, [selectedFile.info.rel_path, selectedFile.frontmatter]);

  useEffect(() => {
    let cancelled = false;
    setActionCatalogLoading(true);
    void wikiListActions()
      .then((actions) => { if (!cancelled) setActionCatalog(actions); })
      .catch((error) => {
        if (!cancelled) setActionMessage(`行动目录读取失败：${String(error)}`);
      })
      .finally(() => { if (!cancelled) setActionCatalogLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (actionRefs.length === 0) {
      setActionBindings([]);
      setActionsLoading(false);
      return () => { cancelled = true; };
    }
    setActionsLoading(true);
    void wikiResolveActions(actionRefs)
      .then((bindings) => { if (!cancelled) setActionBindings(bindings); })
      .catch((error) => {
        if (!cancelled) setActionMessage(`行动注册表读取失败：${String(error)}`);
      })
      .finally(() => { if (!cancelled) setActionsLoading(false); });
    return () => { cancelled = true; };
  }, [actionRefs]);

  const saveActionRefs = async (nextRefs: string[], successMessage: string) => {
    setActionSaving(true);
    setActionMessage(null);
    try {
      const content = writeWikiActionRefs(selectedFile.content, nextRefs);
      await wikiUpdateFile(selectedFile.info.rel_path, content);
      await loadFiles();
      await selectFile(selectedFile.info.rel_path);
      setActionMessage(successMessage);
    } catch (error) {
      setActionMessage(String(error));
    } finally {
      setActionSaving(false);
    }
  };

  const addAction = () => {
    if (!selectedActionId || actionRefs.includes(selectedActionId) || actionSaving) return;
    const action = actionCatalog.find((item) => item.id === selectedActionId);
    if (!action?.available) return;
    const nextRefs = [...actionRefs, selectedActionId];
    setActionRefs(nextRefs);
    setSelectedActionId("");
    void saveActionRefs(nextRefs, `已绑定行动：${action.label}`);
  };

  const removeAction = (action: WikiActionBinding) => {
    const nextRefs = actionRefs.filter((ref) => ref !== action.id);
    setActionRefs(nextRefs);
    void saveActionRefs(nextRefs, `已解绑行动：${action.label}`);
  };

  const runAction = async (action: WikiActionBinding) => {
    if (!action.available || actionRunId) return;
    if (action.approval === "required" && !window.confirm(`确认执行“${action.label}”吗？`)) return;
    setActionRunId(action.id);
    setActionMessage(null);
    try {
      const run = await wikiExecuteAction(action.id, true);
      if (!run.result.ok) throw new Error(run.result.error || "行动执行失败");
      setActionMessage(`行动已完成：${run.label}`);
    } catch (error) {
      setActionMessage(`行动执行失败：${String(error)}`);
    } finally {
      setActionRunId(null);
    }
  };

  return (
    <section className="wiki-workbench__section wiki-workbench__section--actions">
      <div className="wiki-workbench__section-title">
        <Lightning size={22} aria-hidden="true" />
        <div><h3>关联行动</h3><p>选择一个行动，系统会自动完成关联；无需填写配置。</p></div>
      </div>
      {!readOnly && (
        <div className="wiki-action-selector">
          <select
            value={selectedActionId}
            onChange={(event) => setSelectedActionId(event.target.value)}
            disabled={actionCatalogLoading || actionSaving}
            aria-label="选择要关联的行动"
          >
            <option value="">选择一个行动…</option>
            {actionCatalog.map((action) => (
              <option
                key={action.id}
                value={action.id}
                disabled={!action.available || actionRefs.includes(action.id)}
              >
                {action.label}{!action.available ? `（${action.reason || "暂不可用"}）` : ""}
              </option>
            ))}
          </select>
          <button type="button" className="wiki-workbench__secondary" onClick={addAction} disabled={!selectedActionId || actionSaving}>
            {actionSaving ? <SpinnerGap className="wiki-spin" size={18} /> : <Check size={18} />}
            关联行动
          </button>
        </div>
      )}
      {readOnly && <div className="wiki-workbench__quiet">部门知识只读，不能修改行动绑定。</div>}
      {!readOnly && actionCatalogLoading && <div className="wiki-workbench__quiet">正在加载可用行动…</div>}
      {!readOnly && !actionCatalogLoading && actionCatalog.length === 0 && (
        <div className="wiki-workbench__quiet">当前没有可关联行动。</div>
      )}
      {actionsLoading && <div className="wiki-workbench__quiet">正在解析行动注册表…</div>}
      {!actionsLoading && actionBindings.length === 0 && (
        <div className="wiki-workbench__quiet">尚未绑定行动；绑定后才会出现执行入口。</div>
      )}
      <div className="wiki-actions-list">
        {actionBindings.map((action) => (
          <div className={`wiki-action-card${action.available ? "" : " wiki-action-card--disabled"}`} key={action.id}>
            <div>
              <strong>{action.label}</strong>
              {action.description && <small>{action.description}</small>}
              {!action.available && <small className="wiki-workbench__error">{action.reason || "当前不可执行"}</small>}
            </div>
            <div className="wiki-action-card__buttons">
              {!readOnly && <button type="button" className="wiki-workbench__secondary" onClick={() => removeAction(action)} disabled={actionSaving || actionRunId !== null} title="解除这个行动关联"><X size={18} />解除</button>}
              <button type="button" className="wiki-workbench__primary" onClick={() => void runAction(action)} disabled={!action.available || actionRunId !== null}>
                {actionRunId === action.id ? <SpinnerGap className="wiki-spin" size={18} /> : <Play size={18} />}
                执行
              </button>
            </div>
          </div>
        ))}
      </div>
      {actionMessage && <div className="wiki-workbench__success">{actionMessage}</div>}
    </section>
  );
}
