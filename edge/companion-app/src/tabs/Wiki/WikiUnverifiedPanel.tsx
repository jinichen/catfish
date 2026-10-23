/** 关系整理里的「没对上原文」(10/23): 蒸馏时找不到出处、没写进正文的事实, 员工决定加回还是丢掉。
 *
 * 插件写卡前逐条对原文 (catfish_memory_wiki_ground: 检索原文段 + 模型判断, 调不通退回
 * catfish_memory_wiki_verify 的摘录比对): 原文对不上、数字原文里没有的, 都拿出正文放进
 * frontmatter `unverified: ["事实 —— 原因", ...]`。
 * 这里: 加回 = 追加到正文末尾一行; 丢掉 = 只从列表里删。两种都不改条目状态 ——
 * 条目要是因为别的原因 (关系没对上) 待确认, 那件事归别的任务管。
 */
import { useState } from "react";
import { Check, SealWarning, SpinnerGap, Trash } from "@phosphor-icons/react";
import { wikiReadFile, wikiUpdateFile } from "../../lib/tauri_wiki";
import { useWikiStore } from "../../store/wiki";
import type { WikiRelationshipTask } from "./wikiRelationshipTasks";

const REASON_SEP = " —— ";

export function splitUnverified(item: string): { fact: string; reason: string } {
  const at = item.lastIndexOf(REASON_SEP);
  return at < 0 ? { fact: item, reason: "" } : { fact: item.slice(0, at), reason: item.slice(at + REASON_SEP.length) };
}

/** 改 frontmatter 里一个 list 字段 (空了就删掉这一行); 正文不动。 */
export function setFrontmatterList(content: string, field: string, items: string[]): string {
  const m = content.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!m) return content;
  const lines = m[1].split(/\r?\n/).filter((l) => !l.startsWith(`${field}:`));
  if (items.length > 0) lines.push(`${field}: [${items.map((i) => `"${i.replace(/"/g, "”")}"`).join(", ")}]`);
  return `---\n${lines.join("\n")}\n---${content.slice(m[0].length)}`;
}

/** 加回正文: 追加成一行事实。 */
export function appendFact(content: string, fact: string): string {
  return `${content.replace(/\s*$/, "")}\n- ${fact.trim()}\n`;
}

export default function WikiUnverifiedPanel({ task, onLater }: { task: WikiRelationshipTask; onLater: (() => void) | null }) {
  const loadFiles = useWikiStore((state) => state.loadFiles);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const items = task.file.unverified ?? [];

  const resolve = async (item: string, keep: boolean) => {
    setBusy(item);
    setError(null);
    try {
      const full = await wikiReadFile(task.file.rel_path);
      const rest = (full.info.unverified ?? []).filter((x) => x !== item);
      let next = setFrontmatterList(full.content, "unverified", rest);
      if (keep) next = appendFact(next, splitUnverified(item).fact);
      await wikiUpdateFile(task.file.rel_path, next);
      await loadFiles();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="wiki-workbench__section">
      <div className="wiki-workbench__section-title">
        <SealWarning size={22} aria-hidden="true" />
        <div>
          <h3>没对上原文的事实</h3>
          <p>{task.detail}</p>
        </div>
      </div>
      <div className="wiki-relations">
        {items.map((item) => {
          const { fact, reason } = splitUnverified(item);
          return (
            <div className="wiki-relations__row" key={item}>
              <span className="wiki-relations__name">
                <strong>{fact}</strong>
                {reason && <small style={{ color: "var(--catfish-text-muted)" }}>{reason}</small>}
              </span>
              <button type="button" className="wiki-workbench__secondary" disabled={!!busy} onClick={() => void resolve(item, true)}>
                {busy === item ? <SpinnerGap className="wiki-spin" size={16} /> : <Check size={16} />}
                确实对，加回正文
              </button>
              <button type="button" className="wiki-workbench__secondary" disabled={!!busy} onClick={() => void resolve(item, false)}>
                <Trash size={16} />
                丢掉
              </button>
            </div>
          );
        })}
      </div>
      {(task.file.evidence?.length ?? 0) > 0 && (
        <details className="wiki-preview__relation-details">
          <summary>这一条已经对上原文的依据 ({task.file.evidence!.length})</summary>
          <ul className="wiki-preview__evidence">
            {task.file.evidence!.map((q) => <li key={q}>「{q}」</li>)}
          </ul>
        </details>
      )}
      {error && <p className="wiki-workbench__error">{error}</p>}
      {onLater && (
        <div className="wiki-relation-editor__actions">
          <button type="button" className="wiki-workbench__secondary" onClick={onLater}>稍后处理</button>
        </div>
      )}
    </section>
  );
}
