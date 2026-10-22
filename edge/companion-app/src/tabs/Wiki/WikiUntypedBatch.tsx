/** 「没标类型」批量整理 (10/23): 只写了「关联」的关系一张表过完。
 *
 * 原来一个条目一条任务, 鸿波本机 145 条挤满清单, 每条点进去、点建议、等 8 秒、点采纳。
 * 现在: 「让小鲶全部建议」后台按条目跑 (同时 3 个), 表里每行 条目 / 对方 / 建议类型 / 理由,
 * 员工看过: 勾选的一键采纳, 或逐行「保持关联」「删掉」。**不点就不写**。
 *
 * 建议结果存在模块级缓存里 —— 切到别的任务再回来不用重跑。
 */
import { useMemo, useState } from "react";
import { Check, Sparkle, SpinnerGap, Trash } from "@phosphor-icons/react";
import { wikiReadFile, wikiUpdateFile, type WikiFileInfo } from "../../lib/tauri_wiki";
import { suggestRelationTypes, type RelationSuggestion } from "../../lib/wikiRelationSuggest";
import { addKeptUntyped, keptKey } from "../../lib/wikiUntypedKept";
import { useRelationVocab } from "../../lib/wikiRelationVocab";
import { useChatStore } from "../../store/chat";
import { useWikiStore } from "../../store/wiki";
import { buildConfirmedWikiContent, RELATION_FALLBACK, type WikiRelationshipTask } from "./wikiRelationshipTasks";

const CONCURRENCY = 3;
const suggestionCache = new Map<string, RelationSuggestion>();

interface Row { key: string; file: WikiFileInfo; name: string }

export default function WikiUntypedBatch({ task }: { task: WikiRelationshipTask }) {
  const files = useWikiStore((state) => state.files);
  const loadFiles = useWikiStore((state) => state.loadFiles);
  const model = useChatStore((state) => state.model);
  const customRelations = useRelationVocab((state) => state.custom);
  const [, bump] = useState(0);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [unchecked, setUnchecked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const rows: Row[] = useMemo(
    () => (task.untypedItems ?? []).flatMap(({ file, names }) =>
      names.map((name) => ({ key: keptKey(file.rel_path, name), file, name }))),
    [task.untypedItems],
  );
  const suggested = rows.filter((row) => suggestionCache.has(row.key));
  const adoptable = suggested.filter((row) =>
    suggestionCache.get(row.key)!.rel !== RELATION_FALLBACK && !unchecked.has(row.key));
  const keepSuggested = suggested.filter((row) => suggestionCache.get(row.key)!.rel === RELATION_FALLBACK);

  const suggestAll = async () => {
    const todo = (task.untypedItems ?? []).filter(({ file, names }) =>
      names.some((name) => !suggestionCache.has(keptKey(file.rel_path, name))));
    setError(null);
    setNotice(null);
    setProgress({ done: 0, total: todo.length });
    let done = 0;
    let failed = 0;
    const queue = [...todo];
    const worker = async () => {
      for (let item = queue.shift(); item; item = queue.shift()) {
        try {
          const full = await wikiReadFile(item.file.rel_path);
          const result = await suggestRelationTypes(full.info, full.body, item.names, files, model, customRelations);
          if (result.ok) {
            for (const s of result.suggestions) suggestionCache.set(keptKey(item.file.rel_path, s.name), s);
          } else {
            failed += 1;
          }
        } catch {
          failed += 1;
        }
        done += 1;
        setProgress({ done, total: todo.length });
        bump((n) => n + 1);
      }
    };
    await Promise.all(Array.from({ length: CONCURRENCY }, worker));
    setProgress(null);
    setNotice(failed > 0 ? `建议完了，${failed} 个条目没拿到建议（可再点一次补）` : "建议完了，看一遍再采纳");
  };

  /** 按条目分组改写: 每个文件只读写一次 */
  const rewrite = async (changes: Map<string, Map<string, string | null>>, message: string) => {
    setBusy(true);
    setError(null);
    const failed: string[] = [];
    for (const [relPath, byName] of changes) {
      try {
        const full = await wikiReadFile(relPath);
        const next = full.info.related
          .filter((r) => byName.get(r.name.trim()) !== null)
          .map((r) => (byName.has(r.name.trim()) ? { ...r, rel: byName.get(r.name.trim())! } : r));
        await wikiUpdateFile(relPath, buildConfirmedWikiContent(full.content, next, full.info, { keepStatus: true }));
      } catch (err) {
        failed.push(`${relPath}: ${String(err)}`);
      }
    }
    await loadFiles();
    setBusy(false);
    if (failed.length > 0) setError(`有 ${failed.length} 个条目没改成：\n${failed.join("\n")}`);
    else setNotice(message);
  };
  const group = (list: Row[], value: (row: Row) => string | null) => {
    const changes = new Map<string, Map<string, string | null>>();
    for (const row of list) {
      const byName = changes.get(row.file.rel_path) ?? new Map<string, string | null>();
      byName.set(row.name, value(row));
      changes.set(row.file.rel_path, byName);
    }
    return changes;
  };
  const adoptChecked = () =>
    void rewrite(group(adoptable, (row) => suggestionCache.get(row.key)!.rel), `已采纳 ${adoptable.length} 条建议`);
  const removeRow = (row: Row) =>
    void rewrite(group([row], () => null), `已删掉「${row.file.title}」与「${row.name}」的关系`);
  const keep = (list: Row[]) => {
    addKeptUntyped(list.map((row) => row.key));
    setNotice(`${list.length} 条保持「关联」，以后不再列`);
  };
  const toggle = (key: string) =>
    setUnchecked((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <section className="wiki-workbench__section wiki-untyped">
      <div className="wiki-untyped__bar">
        <button type="button" className="wiki-workbench__secondary" disabled={!!progress || busy} onClick={() => void suggestAll()}>
          {progress ? <SpinnerGap className="wiki-spin" size={16} /> : <Sparkle size={16} />}
          {progress
            ? `小鲶在建议… ${progress.done}/${progress.total} 个条目`
            : suggested.length === 0 ? `让小鲶全部建议（${rows.length} 条）` : `补全剩下的建议（已建议 ${suggested.length}/${rows.length}）`}
        </button>
        {adoptable.length > 0 && (
          <button type="button" className="wiki-workbench__primary" disabled={busy || !!progress} onClick={adoptChecked}>
            <Check size={16} />采纳勾选的 {adoptable.length} 条
          </button>
        )}
        {keepSuggested.length > 0 && (
          <button type="button" className="wiki-workbench__secondary" disabled={busy || !!progress} onClick={() => keep(keepSuggested)}>
            小鲶说拿不准的 {keepSuggested.length} 条都保持「关联」
          </button>
        )}
      </div>
      {error && <p className="wiki-workbench__error">{error}</p>}
      {notice && <p className="wiki-untyped__notice">{notice}</p>}
      <div className="wiki-untyped__table" role="table">
        {rows.map((row) => {
          const s = suggestionCache.get(row.key);
          const typed = s && s.rel !== RELATION_FALLBACK;
          return (
            <div className="wiki-untyped__row" role="row" key={row.key}>
              <input
                type="checkbox"
                aria-label="采纳这条建议"
                disabled={!typed}
                checked={!!typed && !unchecked.has(row.key)}
                onChange={() => toggle(row.key)}
              />
              <span className="wiki-untyped__pair"><strong>{row.file.title}</strong> → {row.name}</span>
              <span className="wiki-untyped__suggest">
                {s ? <><b>{typed ? s.rel : "保持关联"}</b>{s.reason && <small>{s.reason}</small>}</> : <small>还没建议</small>}
              </span>
              <span className="wiki-untyped__actions">
                <button type="button" disabled={busy} onClick={() => keep([row])} title="以后不再列这条">保持关联</button>
                <button type="button" disabled={busy} onClick={() => removeRow(row)} aria-label="删掉这条关系" title="只删关系，不删条目">
                  <Trash size={14} />
                </button>
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}
