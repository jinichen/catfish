/** 一个条目的关系: 逐条看、改类型、删掉, 需要时再加一条 (9/24 重做)。
 *
 * 以前是一个带默认值的「编辑关系」表单 + 下面一张只读列表: 目标下拉在找不到
 * 候选目标时默认选中列表第一项 (中国电信海南公司), 醒目的「确认关系」一点就写进
 * 一条瞎配的关系。现在:
 *   · 现有关系一行一条, 有问题的标红, 就地改类型或删掉
 *   · 加关系收起来, 类型和目标都不预选, 两个都选了才能加
 *   · 待确认条目的主按钮是「确认」—— 关系可以一条都没有
 * 改类型 / 删 / 加 只改关系, 不改待确认状态; 只有「确认」才算员工核对过。
 */
import { useMemo, useState } from "react";
import { Check, CheckCircle, Plus, SpinnerGap, Trash, WarningCircle } from "@phosphor-icons/react";
import { wikiUpdateFile, type RelatedRef } from "../../lib/tauri";
import type { WikiFileFull } from "../../lib/tauri_wiki";
import { useWikiStore } from "../../store/wiki";
import {
  buildConfirmedWikiContent,
  relationIssue,
  relationTypeOptions,
} from "./wikiRelationshipTasks";

export default function WikiRelationsEditor({
  selectedFile,
  onLater,
}: {
  selectedFile: WikiFileFull;
  onLater: (() => void) | null;
}) {
  const files = useWikiStore((state) => state.files);
  const loadFiles = useWikiStore((state) => state.loadFiles);
  const selectFile = useWikiStore((state) => state.selectFile);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [newRel, setNewRel] = useState("");
  const [newTarget, setNewTarget] = useState("");

  const info = selectedFile.info;
  const typed = info.related.filter((relation) => relation.source !== "body");
  const bodyRefs = info.related.filter((relation) => relation.source === "body");
  const relationTypes = useMemo(() => relationTypeOptions(files), [files]);
  const targets = useMemo(
    () => [...new Set(files
      .filter((file) =>
        file.rel_path !== info.rel_path &&
        file.kind !== "query" &&
        !["rejected", "deprecated"].includes((file.ontology_status ?? "active").toLowerCase()))
      .map((file) => file.title))]
      .sort((a, b) => a.localeCompare(b, "zh-CN")),
    [files, info.rel_path],
  );
  const issues = typed.map((relation) => relationIssue(relation, files));
  const issueCount = issues.filter(Boolean).length;
  const isPending = (info.ontology_status ?? "active") === "pending";
  const targetValid = targets.includes(newTarget.trim());

  const save = async (next: RelatedRef[], confirm: boolean, message: string) => {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      // 正文引用原样留在正文里; buildConfirmedWikiContent 只写 frontmatter 关系
      const content = buildConfirmedWikiContent(selectedFile.content, [...bodyRefs, ...next], info, {
        keepStatus: !confirm,
      });
      await wikiUpdateFile(info.rel_path, content);
      await loadFiles();
      await selectFile(info.rel_path);
      setNotice(message);
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  const changeType = (index: number, rel: string) =>
    void save(typed.map((relation, i) => (i === index ? { ...relation, rel } : relation)), false, "已改关系类型");
  const remove = (index: number) =>
    void save(typed.filter((_, i) => i !== index), false, `已删除与「${typed[index].name}」的关系`);
  const add = () => {
    const name = newTarget.trim();
    if (!newRel || !targetValid) return;
    const rest = typed.filter((relation) => relation.name.trim() !== name);
    void save([...rest, { name, rel: newRel }], false, `已加上：${newRel}「${name}」`).then(() => {
      setAdding(false);
      setNewRel("");
      setNewTarget("");
    });
  };

  return (
    <section className="wiki-workbench__section">
      <div className="wiki-workbench__section-title">
        <CheckCircle size={22} aria-hidden="true" />
        <div>
          <h3>关系</h3>
          <p>
            {typed.length === 0
              ? "没有写关系。关系是可选的，没有也可以。"
              : issueCount > 0
                ? `${typed.length} 条，其中 ${issueCount} 条要改（标红的）：改类型或删掉。`
                : `${typed.length} 条。`}
          </p>
        </div>
      </div>

      {typed.length > 0 && (
        <div className="wiki-relations">
          {typed.map((relation, index) => {
            const rel = relation.rel?.trim() ?? "";
            const options = rel && !relationTypes.includes(rel) ? [rel, ...relationTypes] : relationTypes;
            return (
              <div className="wiki-relations__row" data-issue={Boolean(issues[index])} key={`${relation.name}:${index}`}>
                <select
                  value={rel}
                  disabled={saving}
                  aria-label={`与「${relation.name}」的关系类型`}
                  onChange={(event) => changeType(index, event.target.value)}
                >
                  {!rel && <option value="">选类型…</option>}
                  {options.map((type) => <option key={type} value={type}>{type}</option>)}
                </select>
                <span className="wiki-relations__name">
                  <strong>{relation.name}</strong>
                  {issues[index] && <small><WarningCircle size={14} aria-hidden="true" />{issues[index]}</small>}
                </span>
                <button
                  type="button"
                  className="wiki-relations__remove"
                  disabled={saving}
                  onClick={() => remove(index)}
                  title="只删这条关系，不删条目和正文"
                  aria-label={`删除与「${relation.name}」的关系`}
                >
                  <Trash size={16} />
                </button>
              </div>
            );
          })}
        </div>
      )}

      {bodyRefs.length > 0 && (
        <p className="wiki-relations__body-refs">
          正文里还提到：{bodyRefs.map((relation) => relation.name).join("、")}（只用于跳转，不算关系）
        </p>
      )}

      {adding ? (
        <div className="wiki-relations__add">
          <select value={newRel} onChange={(event) => setNewRel(event.target.value)} aria-label="关系类型">
            <option value="">关系类型…</option>
            {relationTypes.map((type) => <option key={type} value={type}>{type}</option>)}
          </select>
          <input
            list="wiki-relation-targets"
            value={newTarget}
            placeholder="输入或选择条目…"
            aria-label="关系目标"
            onChange={(event) => setNewTarget(event.target.value)}
          />
          <datalist id="wiki-relation-targets">
            {targets.map((title) => <option key={title} value={title} />)}
          </datalist>
          <button type="button" className="wiki-workbench__secondary" onClick={() => setAdding(false)}>取消</button>
          <button type="button" className="wiki-workbench__primary" disabled={saving || !newRel || !targetValid} onClick={add}>
            加上
          </button>
        </div>
      ) : (
        <button type="button" className="wiki-relations__add-toggle" onClick={() => setAdding(true)} disabled={saving}>
          <Plus size={16} aria-hidden="true" />再加一条关系
        </button>
      )}

      <div className="wiki-relation-editor__actions">
        {error && <span className="wiki-workbench__error">{error}</span>}
        {notice && <span className="wiki-workbench__success"><CheckCircle size={18} />{notice}</span>}
        {isPending && issueCount > 0 && <span className="wiki-workbench__hint">先改掉标红的关系再确认</span>}
        {onLater && (
          <button type="button" className="wiki-workbench__secondary" onClick={onLater}>稍后处理</button>
        )}
        {isPending && (
          <button
            type="button"
            className="wiki-workbench__primary"
            disabled={saving || issueCount > 0}
            onClick={() => void save(typed, true, "已确认")}
          >
            {saving ? <SpinnerGap className="wiki-spin" size={20} /> : <Check size={20} />}
            确认
          </button>
        )}
      </div>
    </section>
  );
}
