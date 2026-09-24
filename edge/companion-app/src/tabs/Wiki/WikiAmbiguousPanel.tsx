/** 一个名字指向不明 → 选一次它指谁, 所有写这个名字的关系一起改 (9/24)。
 *
 * 以前「中电福富」同时是两个条目的别名, 24 处关系各报一条"关系异常", 员工得逐条
 * 点 24 次, 每次还要在几百项的下拉里找同一个公司。它其实只是一个问题。
 * 改名只动关系里写的名字, 不改各条目的待确认状态。
 */
import { useState } from "react";
import { ArrowRight, CheckCircle, SpinnerGap, WarningCircle } from "@phosphor-icons/react";
import { wikiReadFile, wikiUpdateFile, type WikiFileInfo } from "../../lib/tauri_wiki";
import { useWikiStore } from "../../store/wiki";
import {
  buildConfirmedWikiContent,
  renameRelationTarget,
  type WikiRelationshipTask,
} from "./wikiRelationshipTasks";

export default function WikiAmbiguousPanel({
  task,
  onLater,
}: {
  task: WikiRelationshipTask;
  onLater: (() => void) | null;
}) {
  const loadFiles = useWikiStore((state) => state.loadFiles);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const name = task.relationName ?? "";
  const refs = task.refs ?? [];
  const candidates = task.candidates ?? [];

  const pointAll = async (target: WikiFileInfo) => {
    setSaving(target.rel_path);
    setError(null);
    const failed: string[] = [];
    for (const ref of refs) {
      try {
        const full = await wikiReadFile(ref.rel_path);
        const next = renameRelationTarget(full.info.related, name, target.title);
        const content = buildConfirmedWikiContent(full.content, next, full.info, { keepStatus: true });
        await wikiUpdateFile(ref.rel_path, content);
      } catch (err) {
        failed.push(`${ref.title}: ${String(err)}`);
      }
    }
    await loadFiles();
    setSaving(null);
    if (failed.length > 0) setError(`有 ${failed.length} 处没改成：\n${failed.join("\n")}`);
    else setDone(`${refs.length} 处关系都改成了「${target.title}」`);
  };

  return (
    <section className="wiki-workbench__section">
      <div className="wiki-workbench__section-title">
        <WarningCircle size={22} aria-hidden="true" />
        <div>
          <h3>选它指的是哪一个</h3>
          <p>
            {refs.length} 处关系写的是「{name}」，但 {candidates.length} 个条目的名字或别名都是它。
            选一个，这 {refs.length} 处一起改成那个条目的全名。
          </p>
        </div>
      </div>

      <div className="wiki-workbench__duplicate-list">
        {candidates.map((candidate) => (
          <button
            type="button"
            key={candidate.rel_path}
            disabled={saving !== null || done !== null}
            onClick={() => void pointAll(candidate)}
          >
            <span>
              <strong>都指向「{candidate.title}」</strong>
              <small>
                {candidate.subtype || "未标注类型"}
                {candidate.aliases.length > 0 && ` · 别名：${candidate.aliases.join("、")}`}
              </small>
            </span>
            {saving === candidate.rel_path ? <SpinnerGap className="wiki-spin" size={18} /> : <ArrowRight size={18} aria-hidden="true" />}
          </button>
        ))}
      </div>

      <p className="wiki-relations__body-refs">
        如果这几个条目本来就是同一个东西，先在「全部知识」里把多余的那条删掉，这里会自动消失。
      </p>

      <details className="wiki-relations__refs">
        <summary>写了「{name}」的 {refs.length} 个条目</summary>
        <p>{refs.map((ref) => ref.title).join("、")}</p>
      </details>

      <div className="wiki-relation-editor__actions">
        {error && <span className="wiki-workbench__error">{error}</span>}
        {done && <span className="wiki-workbench__success"><CheckCircle size={18} />{done}</span>}
        {onLater && <button type="button" className="wiki-workbench__secondary" onClick={onLater}>稍后处理</button>}
      </div>
    </section>
  );
}
