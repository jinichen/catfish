/** 共享知识页的历史版本 (11/14)。
 *
 * 中央每次重发前存上一版 (最近 30 版)。两处用:
 * - 作者的「分享」对话框: 能看, 也能「恢复成这一版」(同事那边马上回到这一版, 回到待审);
 *   作者本机那页不动 —— 下次点「分享」还是发本机的内容, 这里会说清楚。
 * - 同事打开装进来的部门知识页: 只能看 (作者改了什么、改之前是什么)。
 */
import { useCallback, useState } from "react";
import ReactMarkdown from "react-markdown";

import { GFM_PLUGINS } from "../../lib/markdown";
import { wikiHistory, wikiHistoryRestore, wikiHistoryVersion, type WikiHistory as History } from "../../lib/wikiHistory";

function when(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function WikiHistory({ namespace, fileId, canRestore }: { namespace: string; fileId: string; canRestore: boolean }) {
  const [open, setOpen] = useState(false);
  const [hist, setHist] = useState<History | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [viewing, setViewing] = useState<{ revision: number; body: string } | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [confirm, setConfirm] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setErr(null);
    try {
      setHist(await wikiHistory(namespace, fileId));
    } catch (e) {
      setErr(`拿不到历史版本: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, [namespace, fileId]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !hist) void load();
  };

  const view = async (revision: number) => {
    if (viewing?.revision === revision) {
      setViewing(null);
      return;
    }
    setBusy(revision);
    try {
      const v = await wikiHistoryVersion(namespace, fileId, revision);
      setViewing({ revision, body: v.body_md });
    } catch (e) {
      setErr(`打不开第 ${revision} 版: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  };

  const restore = async (revision: number) => {
    if (confirm !== revision) {
      setConfirm(revision);
      return;
    }
    setConfirm(null);
    setBusy(revision);
    try {
      const r = await wikiHistoryRestore(namespace, fileId, revision);
      setNote(`同事那边已回到第 ${revision} 版 (现在是第 ${r.revision} 版, 主管要再看一眼)。你本机这页没变, 下次点「分享」还是会发本机的内容。`);
      setViewing(null);
      await load();
    } catch (e) {
      setErr(`没恢复成: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="wiki-history">
      <button type="button" className="wiki-share__link" onClick={toggle} aria-expanded={open}>
        {open ? "收起历史版本" : "历史版本"}
      </button>
      {open && (
        <div className="wiki-history__body">
          {err && <div className="wiki-share__warn-text">{err}</div>}
          {note && <div className="wiki-share__muted">{note}</div>}
          {!hist && !err && <div className="wiki-share__muted">正在拿…</div>}
          {hist && (
            <>
              <div className="wiki-share__muted">
                现在是第 {hist.current_revision} 版{hist.current_updated_at ? ` (${when(hist.current_updated_at)})` : ""}
                {hist.versions.length === 0 ? "; 还没有更早的版本。" : "; 中央留最近 30 版。"}
              </div>
              <ul className="wiki-history__list">
                {hist.versions.map((v) => (
                  <li key={v.revision}>
                    <span className="wiki-history__rev">第 {v.revision} 版</span>
                    <span className="wiki-share__muted wiki-history__meta">{when(v.updated_at)}{v.published_by ? ` · ${v.published_by}` : ""}</span>
                    <button type="button" className="wiki-share__link" disabled={busy !== null} onClick={() => void view(v.revision)}>
                      {viewing?.revision === v.revision ? "收起" : busy === v.revision ? "…" : "看"}
                    </button>
                    {canRestore && (
                      <button type="button" className="wiki-share__link" disabled={busy !== null} onClick={() => void restore(v.revision)}>
                        {confirm === v.revision ? "确认恢复?" : "恢复成这一版"}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
              {viewing && (
                <div className="wiki-history__view">
                  <ReactMarkdown remarkPlugins={GFM_PLUGINS}>{viewing.body || "(这一版正文是空的)"}</ReactMarkdown>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
