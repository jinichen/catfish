/** 知识体系「关系类型」(10/23): 看内置关系的意思, 登记 / 删除自己的关系类型。
 *
 * 登记 = 词 + 一句话意思。登记过的词在关系下拉、小鲶建议、蒸馏、主聊天里都能用;
 * 没登记的词写进条目会被归成「关联」(防止模型自造词, 9/17 起的规矩)。
 */
import { useEffect, useState } from "react";
import { confirmDialog } from "../../components/dialogs";
import { Plus, Trash, X } from "@phosphor-icons/react";
import { ModalShell } from "../../components/ModalShell";
import { V, field } from "./wikiTheme";
import { useRelationVocab } from "../../lib/wikiRelationVocab";
import { useWikiStore } from "../../store/wiki";

export default function WikiRelationTypesModal({ onClose }: { onClose: () => void }) {
  const { builtin, custom, load, add, remove } = useRelationVocab();
  const files = useWikiStore((state) => state.files);
  const [name, setName] = useState("");
  const [meaning, setMeaning] = useState("");
  const [hierarchy, setHierarchy] = useState<"" | "child" | "parent">("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void load();
  }, [load]);

  const usage = (word: string) =>
    files.reduce((n, f) => n + f.related.filter((r) => r.source !== "body" && r.rel?.trim() === word).length, 0);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await add(name.trim(), meaning.trim(), hierarchy || undefined);
      setName("");
      setMeaning("");
      setHierarchy("");
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };
  const drop = async (word: string) => {
    const n = usage(word);
    if (n > 0 && !(await confirmDialog(`有 ${n} 条关系写着「${word}」。删掉后这些关系下次保存时会变成「关联」。确定删？`))) return;
    setBusy(true);
    setError(null);
    try {
      await remove(word);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const row = { display: "grid", gridTemplateColumns: "64px 1fr auto", gap: 10, padding: "6px 0", borderBottom: `1px solid ${V.border}`, fontSize: 13 } as const;
  return (
    <ModalShell onClose={onClose} themed maxWidth={720}>
      <div style={{ width: "100%", fontFamily: V.font, color: V.text }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
          <h3 style={{ margin: 0, fontSize: 17 }}>关系类型</h3>
          <button type="button" onClick={onClose} aria-label="关闭" style={{ border: 0, background: "transparent", cursor: "pointer", color: V.muted }}>
            <X size={18} />
          </button>
        </div>
        <p style={{ margin: "0 0 12px", color: V.muted, fontSize: 13, lineHeight: 1.5 }}>
          关系读作「本条目 关系 对方」。下面没有的，可以登记自己的（词 + 一句话意思）；登记后关系下拉、小鲶建议和对话里都能用。没登记的词会被归成「关联」。
        </p>
        <div style={{ maxHeight: "46vh", overflowY: "auto", marginBottom: 14 }}>
          {custom.length > 0 && (
            <>
              <div style={{ fontSize: 12, color: V.muted, margin: "4px 0" }}>我登记的</div>
              {custom.map((c) => (
                <div key={c.name} style={row}>
                  <strong style={{ color: V.accent }}>{c.name}</strong>
                  <span>{c.meaning}<small style={{ color: V.muted }}>{c.hierarchy ? ` · 上下级 (${c.hierarchy === "child" ? "本条目是下级" : "本条目是上级"})` : ""}{usage(c.name) > 0 ? ` · 用了 ${usage(c.name)} 条` : ""}</small></span>
                  <button type="button" disabled={busy} onClick={() => void drop(c.name)} aria-label={`删除关系类型「${c.name}」`}
                    style={{ border: 0, background: "transparent", cursor: "pointer", color: V.muted }}>
                    <Trash size={15} />
                  </button>
                </div>
              ))}
            </>
          )}
          <div style={{ fontSize: 12, color: V.muted, margin: "10px 0 4px" }}>内置</div>
          {builtin.map((b) => (
            <div key={b.name} style={row}>
              <strong>{b.name}</strong>
              <span style={{ color: V.muted }}>{b.meaning}{b.hierarchy ? " · 上下级" : ""}</span>
              <span />
            </div>
          ))}
        </div>
        {/* 11/07: 原来四样挤一行, 输入框里的提示字被截断 —— 名字 + 意思一行, 上下级 + 登记一行 */}
        <div style={{ display: "grid", gridTemplateColumns: "130px minmax(0, 1fr)", gap: 8 }}>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="新关系，如 挂靠" maxLength={6}
            aria-label="新关系类型" style={{ ...field, padding: "7px 9px", borderRadius: 8 }} />
          <input value={meaning} onChange={(e) => setMeaning(e.target.value)} placeholder="一句话意思，如 本条目挂靠在对方名下 (人→公司)"
            maxLength={60} aria-label="这个关系的意思" style={{ ...field, padding: "7px 9px", borderRadius: 8 }} />
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 8, justifyContent: "flex-end" }}>
          <select value={hierarchy} onChange={(e) => setHierarchy(e.target.value as "" | "child" | "parent")} aria-label="是不是上下级"
            style={{ ...field, padding: "7px 6px", borderRadius: 8 }}>
            <option value="">不是上下级</option>
            <option value="child">本条目是对方的下级</option>
            <option value="parent">本条目是对方的上级</option>
          </select>
          <button type="button" disabled={busy || !name.trim() || !meaning.trim()} onClick={() => void submit()}
            style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "7px 12px", border: 0, borderRadius: 8, background: V.accent, color: "#fff", cursor: "pointer", fontSize: 13 }}>
            <Plus size={14} />登记
          </button>
        </div>
        {error && <p style={{ color: V.error, fontSize: 12, margin: "8px 0 0" }}>{error}</p>}
      </div>
    </ModalShell>
  );
}
