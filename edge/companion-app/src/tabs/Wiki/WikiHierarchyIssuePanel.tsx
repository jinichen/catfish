/** 关系整理里的层级问题 (10/23): 跨级重复 / 多个上级 —— 列出涉及的上下级关系, 一键去掉多余的。
 *
 * 关系可能写在下级 (「隶属 上级」) 也可能写在上级 (「包含 下级」), 去掉时改写它实际写在的那个条目。
 */
import { useState } from "react";
import { SpinnerGap, Trash, TreeStructure } from "@phosphor-icons/react";
import { wikiReadFile, wikiUpdateFile } from "../../lib/tauri_wiki";
import { useWikiStore } from "../../store/wiki";
import type { HierarchyLink } from "./wikiHierarchy";
import { buildConfirmedWikiContent, type WikiRelationshipTask } from "./wikiRelationshipTasks";

export default function WikiHierarchyIssuePanel({ task, onLater }: { task: WikiRelationshipTask; onLater: (() => void) | null }) {
  const files = useWikiStore((state) => state.files);
  const loadFiles = useWikiStore((state) => state.loadFiles);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const title = (path: string) => files.find((f) => f.rel_path === path)?.title ?? path;

  const removeLink = async (link: HierarchyLink) => {
    setBusy(`${link.parent}→${link.child}`);
    setError(null);
    try {
      const full = await wikiReadFile(link.declaredOn);
      const next = full.info.related.filter((r) => !(r.source !== "body" && r.name.trim() === link.name.trim() && (r.rel ?? "") === link.rel));
      await wikiUpdateFile(link.declaredOn, buildConfirmedWikiContent(full.content, next, full.info, { keepStatus: true }));
      await loadFiles();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(null);
    }
  };

  const links = task.hierarchyLinks ?? [];
  return (
    <section className="wiki-workbench__section">
      <div className="wiki-workbench__section-title">
        <TreeStructure size={22} aria-hidden="true" />
        <div>
          <h3>{task.kind === "skiplevel" ? "跨级重复" : "多个上级"}</h3>
          <p>{task.detail}</p>
        </div>
      </div>
      <div className="wiki-relations">
        {links.map((link) => {
          const key = `${link.parent}→${link.child}`;
          return (
            <div className="wiki-relations__row" key={key}>
              <span className="wiki-relations__name">
                <strong>{title(link.child)} 在「{title(link.parent)}」下面</strong>
                <small style={{ color: "var(--catfish-text-muted)" }}>写在「{title(link.declaredOn)}」条目上：{link.rel} {link.name}</small>
              </span>
              <span />
              <button type="button" className="wiki-workbench__secondary" disabled={!!busy} onClick={() => void removeLink(link)}>
                {busy === key ? <SpinnerGap className="wiki-spin" size={16} /> : <Trash size={16} />}
                去掉这条
              </button>
            </div>
          );
        })}
      </div>
      {error && <p className="wiki-workbench__error">{error}</p>}
      {onLater && (
        <div className="wiki-relation-editor__actions">
          <button type="button" className="wiki-workbench__secondary" onClick={onLater}>稍后处理</button>
        </div>
      )}
    </section>
  );
}
