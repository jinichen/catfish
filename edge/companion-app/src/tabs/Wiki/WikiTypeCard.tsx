/**
 * 11/07 知识页「属性卡片」: 有类型定义的条目 (现在是资质证书) 显示属性、自动算的状态, 可以就地改属性;
 * 下面是这一类都有的行动 (官网核查 / 到期提醒 …), 点了交给小鲶, 属性直接带过去。
 * 定义在 edge/contracts/wiki_type_schema.json, 读写在 tool-bridge wiki_types.py。
 */
import { useCallback, useEffect, useState } from "react";
import { ChatCircleDots, PencilSimple, SpinnerGap, Tag } from "@phosphor-icons/react";
import { wikiPropsApply, wikiTypeEntry, type WikiTypeAction, type WikiTypeEntry } from "../../lib/tauri_wiki_types";
import { wikiTypeActionPrompt } from "../../lib/wikiActions";
import { selectExpert } from "../../lib/expertChat";
import { useUIStore } from "../../store/ui";
import { useWikiStore } from "../../store/wiki";

const STATUS_CLASS: Record<string, string> = {
  ok: "wiki-type-card__status--ok",
  warn: "wiki-type-card__status--warn",
  expired: "wiki-type-card__status--expired",
  unknown: "wiki-type-card__status--unknown",
};

export default function WikiTypeCard({ relPath, title, readOnly }: { relPath: string; title: string; readOnly: boolean }) {
  const startChat = useUIStore((s) => s.startEmailChat);
  const loadFiles = useWikiStore((s) => s.loadFiles);
  const [entry, setEntry] = useState<WikiTypeEntry | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setEntry(await wikiTypeEntry(relPath));
    } catch {
      setEntry(null); // tool-bridge 没起来 / 读失败: 不显示卡片, 不挡正文
    }
  }, [relPath]);

  useEffect(() => {
    setEditing(false);
    setMessage(null);
    void load();
  }, [load]);

  if (!entry?.typed || !entry.properties) return null;
  const props = entry.properties;
  const filled = props.filter((p) => p.value).length;

  const startEdit = () => {
    setDraft(Object.fromEntries(props.map((p) => [p.key, p.value])));
    setEditing(true);
    setMessage(null);
  };

  const save = async () => {
    const changed = Object.fromEntries(props.filter((p) => (draft[p.key] ?? "") !== p.value).map((p) => [p.key, draft[p.key] ?? ""]));
    if (Object.keys(changed).length === 0) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      const r = await wikiPropsApply([{ rel_path: relPath, props: changed }]);
      if (!r.ok) throw new Error(r.errors.map((e) => e.error).join("；") || "没写进去");
      setEditing(false);
      setMessage("属性已保存");
      await load();
      void loadFiles();
    } catch (error) {
      setMessage(`保存失败：${String(error instanceof Error ? error.message : error)}`);
    } finally {
      setSaving(false);
    }
  };

  const run = (action: WikiTypeAction) => {
    if (!action.available) return;
    selectExpert(null); // 给主小鲶
    startChat(
      `wiki-type-action:${action.id}:${relPath}`,
      wikiTypeActionPrompt(action, { title, relPath, typeLabel: entry.label ?? "", properties: props }),
    );
  };

  return (
    <section className="wiki-workbench__section wiki-type-card" aria-label={`${entry.label}属性`}>
      <div className="wiki-type-card__head">
        <Tag size={18} aria-hidden="true" />
        <strong>{entry.label}</strong>
        {entry.status && (
          <span className={`wiki-type-card__status ${STATUS_CLASS[entry.status.code] ?? ""}`}>
            {entry.status.value}
            {entry.status.code === "warn" && entry.status.days_left ? ` · 还剩 ${entry.status.days_left} 天` : ""}
          </span>
        )}
        <small>{filled}/{props.length} 项已填</small>
        {!readOnly && !editing && (
          <button type="button" className="wiki-workbench__secondary" onClick={startEdit}>
            <PencilSimple size={16} />改属性
          </button>
        )}
      </div>

      <dl className="wiki-type-card__props">
        {props.map((p) => (
          <div key={p.key} title={p.hint || undefined}>
            <dt>{p.label}</dt>
            <dd>
              {editing ? (
                <input
                  type={p.type === "date" ? "date" : "text"}
                  value={draft[p.key] ?? ""}
                  onChange={(e) => setDraft((d) => ({ ...d, [p.key]: e.target.value }))}
                  aria-label={p.label}
                />
              ) : p.value ? (
                p.value
              ) : (
                <span className="wiki-type-card__empty">未填</span>
              )}
            </dd>
          </div>
        ))}
      </dl>
      {editing && (
        <div className="wiki-type-card__edit-bar">
          <button type="button" className="wiki-workbench__primary" onClick={() => void save()} disabled={saving}>
            {saving && <SpinnerGap className="wiki-spin" size={16} />}保存
          </button>
          <button type="button" className="wiki-workbench__secondary" onClick={() => setEditing(false)} disabled={saving}>取消</button>
          <small>只改这几项属性，正文不动；改之前会备份原文件。</small>
        </div>
      )}

      {(entry.actions ?? []).length > 0 && (
        <div className="wiki-actions-list">
          <div className="wiki-workbench__quiet">{entry.label}都有的行动 · 点了交给小鲶，属性会一起带过去</div>
          {(entry.actions ?? []).map((a) => (
            <div className={`wiki-action-card${a.available ? "" : " wiki-action-card--disabled"}`} key={a.id}>
              <div>
                <strong>{a.label}</strong>
                {a.description && <small>{a.description}</small>}
                {!a.available && <small className="wiki-workbench__error">{a.reason || "当前不可用"}</small>}
              </div>
              <div className="wiki-action-card__buttons">
                <button type="button" className="wiki-workbench__primary" onClick={() => run(a)} disabled={!a.available || editing}>
                  <ChatCircleDots size={18} />交给小鲶
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      {message && <div className="wiki-workbench__success">{message}</div>}
    </section>
  );
}
