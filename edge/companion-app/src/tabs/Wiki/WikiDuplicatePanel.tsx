/** 关系整理里的「可能重复」(10/23 重做): 以前只列出几条、点进去看, 没有处理按钮。
 *
 * 每条一张卡 (类型 · 来源 · 正文前几行), 两种处理:
 *   · 保留这条 —— 其余的内容、别名、关系、来源并进来, 其余文件移到回收站 (wiki/.trash, 可找回)
 *   · 改个名 —— 确实是两个东西, 名字撞了; 改完各自留着
 */
import { useEffect, useState } from "react";
import { ArrowRight, CheckCircle, PencilSimple, SpinnerGap, Stack, WarningCircle } from "@phosphor-icons/react";
import { wikiDeleteFile, wikiReadFile, wikiUpdateFile, type WikiFileFull } from "../../lib/tauri_wiki";
import { useWikiStore } from "../../store/wiki";
import type { WikiRelationshipTask } from "./wikiRelationshipTasks";
import { wikiSourceLabel, wikiSubtypeLabel, wikiKindLabel } from "./wikiLabels";
import { mergeWikiEntries, renameWikiEntry } from "./wikiMerge";

function preview(body: string): string[] {
  return body.split(/\r?\n/).map((l) => l.trim()).filter((l) => l && !l.startsWith("#")).slice(0, 3);
}

export default function WikiDuplicatePanel({ task, onLater }: { task: WikiRelationshipTask; onLater: (() => void) | null }) {
  const loadFiles = useWikiStore((state) => state.loadFiles);
  const selectFile = useWikiStore((state) => state.selectFile);
  const [entries, setEntries] = useState<WikiFileFull[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [newName, setNewName] = useState("");

  const paths = task.duplicatePaths ?? [];
  useEffect(() => {
    let alive = true;
    Promise.all(paths.map((p) => wikiReadFile(p)))
      .then((list) => { if (alive) setEntries(list); })
      .catch((err) => { if (alive) setError(String(err)); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paths.join("|")]);

  const run = async (work: () => Promise<string>, after: string) => {
    setBusy(true);
    setError(null);
    try {
      const message = await work();
      await loadFiles();
      await selectFile(after);
      setNotice(message);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const keep = (main: WikiFileFull) => void run(async () => {
    const others = (entries ?? []).filter((e) => e.info.rel_path !== main.info.rel_path);
    await wikiUpdateFile(main.info.rel_path, mergeWikiEntries(main, others));
    for (const o of others) await wikiDeleteFile(o.info.rel_path);
    return `已合并成一条「${main.info.title}」，另外 ${others.length} 条移到了回收站`;
  }, main.info.rel_path);

  const rename = (entry: WikiFileFull) => {
    const name = newName.trim();
    if (!name || name === entry.info.title) return;
    void run(async () => {
      await wikiUpdateFile(entry.info.rel_path, renameWikiEntry(entry.content, entry.info.title, name));
      setRenaming(null);
      return `已改名为「${name}」，两条各自保留`;
    }, entry.info.rel_path);
  };

  if (notice) {
    return (
      <section className="wiki-workbench__section">
        <p className="wiki-workbench__success"><CheckCircle size={18} />{notice}</p>
        {onLater && <button type="button" className="wiki-workbench__secondary" onClick={onLater}>下一项</button>}
      </section>
    );
  }

  return (
    <section className="wiki-workbench__section">
      <div className="wiki-workbench__section-title">
        <Stack size={22} aria-hidden="true" />
        <div>
          <h3>这 {paths.length} 条可能是同一个东西</h3>
          <p>是同一个：选要保留的那条，其余的内容并进来。不是：给其中一条改个名字。</p>
        </div>
      </div>
      {!entries && !error && <p className="wiki-dup__loading"><SpinnerGap className="wiki-spin" size={18} />正在读取…</p>}
      <div className="wiki-dup">
        {entries?.map((e) => (
          <article className="wiki-dup__card" key={e.info.rel_path}>
            <header>
              <strong>{e.info.title}</strong>
              <span className="wiki-dup__type">{wikiKindLabel(e.info.kind)} · {wikiSubtypeLabel(e.info.subtype, e.info.kind)}</span>
            </header>
            <small className="wiki-dup__source">
              {e.info.sources.length ? e.info.sources.map(wikiSourceLabel).join("、") : "没有来源"}
            </small>
            <ul>{preview(e.body).map((line) => <li key={line}>{line.replace(/^[-*]\s*/, "")}</li>)}</ul>
            {renaming === e.info.rel_path ? (
              <div className="wiki-dup__rename">
                <input value={newName} autoFocus aria-label="新名字" onChange={(ev) => setNewName(ev.target.value)}
                  onKeyDown={(ev) => { if (ev.key === "Enter") rename(e); }} />
                <button type="button" className="wiki-workbench__secondary" onClick={() => setRenaming(null)}>取消</button>
                <button type="button" className="wiki-workbench__primary" disabled={busy || !newName.trim() || newName.trim() === e.info.title}
                  onClick={() => rename(e)}>改名</button>
              </div>
            ) : (
              <footer>
                <button type="button" className="wiki-workbench__link" onClick={() => void selectFile(e.info.rel_path)}>
                  看全文 <ArrowRight size={14} aria-hidden="true" />
                </button>
                <span />
                <button type="button" className="wiki-workbench__secondary" disabled={busy}
                  onClick={() => { setRenaming(e.info.rel_path); setNewName(e.info.title); }}>
                  <PencilSimple size={16} aria-hidden="true" />改个名
                </button>
                <button type="button" className="wiki-workbench__primary" disabled={busy || !entries} onClick={() => keep(e)}>
                  {busy ? <SpinnerGap className="wiki-spin" size={16} /> : <CheckCircle size={16} />}保留这条，合并其余
                </button>
              </footer>
            )}
          </article>
        ))}
      </div>
      {error && <p className="wiki-workbench__error"><WarningCircle size={16} />{error}</p>}
      <div className="wiki-relation-editor__actions">
        <span className="wiki-workbench__hint">合并后另一条移到回收站，可以找回</span>
        {onLater && <button type="button" className="wiki-workbench__secondary" onClick={onLater}>稍后处理</button>}
      </div>
    </section>
  );
}
