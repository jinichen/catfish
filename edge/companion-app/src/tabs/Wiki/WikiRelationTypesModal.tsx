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

  // 11/07 鸿波「这个 UI 需要优化」: 登记区原来缀在列表底下、上下级和按钮孤零零靠右; 列表滚动后分不清内置 / 我登记的。
  // 改成: 顶上一块「登记新关系」(带字段名的小表单), 下面列表分组、组标题滚动时钉在顶上, 上下级 / 用了几条做成小标签。
  const row = { display: "grid", gridTemplateColumns: "64px minmax(0, 1fr) auto", gap: 12, alignItems: "center", padding: "8px 2px", borderBottom: `1px solid ${V.border}`, fontSize: 13 } as const;
  const pill = { display: "inline-block", marginLeft: 6, padding: "1px 7px", borderRadius: 999, border: `1px solid ${V.border}`, color: V.muted, fontSize: 11, whiteSpace: "nowrap" } as const;
  const groupHead = { position: "sticky", top: 0, zIndex: 1, background: V.surface, padding: "8px 2px 6px", fontSize: 12, fontWeight: 600, color: V.muted, borderBottom: `1px solid ${V.border}` } as const;
  const fieldLabel = { display: "grid", gap: 4, fontSize: 12, color: V.muted, minWidth: 0 } as const;
  return (
    <ModalShell onClose={onClose} themed maxWidth={720}>
      <div style={{ width: "100%", fontFamily: V.font, color: V.text }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
          <h3 style={{ margin: 0, fontSize: 17 }}>关系类型</h3>
          <button type="button" onClick={onClose} aria-label="关闭" style={{ border: 0, background: "transparent", cursor: "pointer", color: V.muted }}>
            <X size={18} />
          </button>
        </div>
        <p style={{ margin: "0 0 14px", color: V.muted, fontSize: 13, lineHeight: 1.6 }}>
          关系读作「本条目 关系 对方」。列表里没有的可以自己登记，登记后关系下拉、小鲶建议和对话里都能用；没登记的词会被归成「关联」。
        </p>

        <section aria-label="登记新关系" style={{ border: `1px solid ${V.border}`, borderRadius: 10, padding: "12px 14px", background: V.bg, marginBottom: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>登记新关系</div>
          <div style={{ display: "grid", gridTemplateColumns: "140px minmax(0, 1fr)", gap: 10 }}>
            <label style={fieldLabel}>
              关系名
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="如 挂靠" maxLength={6}
                aria-label="新关系类型" style={{ ...field, padding: "7px 9px", borderRadius: 8, background: V.surface }} />
            </label>
            <label style={fieldLabel}>
              一句话意思
              <input value={meaning} onChange={(e) => setMeaning(e.target.value)} placeholder="如 本条目挂靠在对方名下 (人→公司)"
                maxLength={60} aria-label="这个关系的意思" style={{ ...field, padding: "7px 9px", borderRadius: 8, background: V.surface }} />
            </label>
          </div>
          <div style={{ display: "flex", alignItems: "flex-end", gap: 10, marginTop: 10 }}>
            <label style={{ ...fieldLabel, width: 220 }}>
              是不是上下级
              <select value={hierarchy} onChange={(e) => setHierarchy(e.target.value as "" | "child" | "parent")} aria-label="是不是上下级"
                style={{ ...field, padding: "7px 6px", borderRadius: 8, background: V.surface }}>
                <option value="">不是上下级</option>
                <option value="child">本条目是对方的下级</option>
                <option value="parent">本条目是对方的上级</option>
              </select>
            </label>
            {error && <span style={{ color: V.error, fontSize: 12, alignSelf: "center" }}>{error}</span>}
            <button type="button" disabled={busy || !name.trim() || !meaning.trim()} onClick={() => void submit()}
              style={{
                marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 4, padding: "8px 16px", border: 0, borderRadius: 8,
                background: V.accent, color: "#fff", fontSize: 13,
                cursor: busy || !name.trim() || !meaning.trim() ? "not-allowed" : "pointer",
                opacity: busy || !name.trim() || !meaning.trim() ? 0.5 : 1,
              }}>
              <Plus size={14} />登记
            </button>
          </div>
        </section>

        <div style={{ maxHeight: "42vh", overflowY: "auto", paddingRight: 4 }}>
          {custom.length > 0 && (
            <>
              <div style={groupHead}>我登记的 · {custom.length}</div>
              {custom.map((c) => (
                <div key={c.name} style={row}>
                  <strong style={{ color: V.accent }}>{c.name}</strong>
                  <span style={{ minWidth: 0 }}>
                    {c.meaning}
                    {c.hierarchy && <span style={pill}>上下级 · {c.hierarchy === "child" ? "本条目是下级" : "本条目是上级"}</span>}
                    {usage(c.name) > 0 && <span style={pill}>用了 {usage(c.name)} 条</span>}
                  </span>
                  <button type="button" disabled={busy} onClick={() => void drop(c.name)} aria-label={`删除关系类型「${c.name}」`} title="删除"
                    style={{ border: 0, background: "transparent", cursor: "pointer", color: V.muted, padding: 4 }}>
                    <Trash size={15} />
                  </button>
                </div>
              ))}
            </>
          )}
          <div style={groupHead}>内置 · {builtin.length}</div>
          {builtin.map((b) => (
            <div key={b.name} style={row}>
              <strong>{b.name}</strong>
              <span style={{ color: V.muted, minWidth: 0 }}>
                {b.meaning}
                {b.hierarchy && <span style={pill}>上下级</span>}
              </span>
              <span />
            </div>
          ))}
        </div>
      </div>
    </ModalShell>
  );
}
