/**
 * 知识体系「类型定义」页 (11/07 鸿波「属性是硬编码吗? 做类型定义页」)。
 *
 * 每一类知识 (资质证书、项目、部门…) 有哪些属性、到期前多少天提醒、能做哪些行动, 都在这里改。
 * 改的是本机补充文件 ~/.catfish/wiki-type-schema.json (内置定义随安装包发, 不动): 内置的属性 / 行动
 * 能改名、改说明、隐藏 / 恢复, 不能删; 自己加的随便删。新类型从知识库里已有的细分类型挑。
 * 改完马上生效: 知识页的属性卡片、「整理属性」、小鲶按属性查都按新定义来。
 */
import { useEffect, useState } from "react";
import { X } from "@phosphor-icons/react";
import { ModalShell, T } from "../../components/ModalShell";
import { wikiTypeDefs, wikiTypeDefsApply, type TypeDefChange, type TypeDefsView } from "../../lib/tauri_wiki_types";
import WikiTypeDefEditor from "./WikiTypeDefEditor";
import { wikiSubtypeLabel } from "./wikiLabels";

export default function WikiTypeDefsModal({ onClose }: { onClose: () => void }) {
  const [view, setView] = useState<TypeDefsView | null>(null);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newType, setNewType] = useState({ key: "", label: "" });

  useEffect(() => {
    void wikiTypeDefs()
      .then((v) => {
        setView(v);
        setSelected(v.types[0]?.key ?? "");
      })
      .catch((e) => setError(`读类型定义失败：${String(e)}`));
  }, []);

  const apply = async (change: TypeDefChange): Promise<boolean> => {
    setBusy(true);
    setError(null);
    try {
      const v = await wikiTypeDefsApply(change);
      setView(v);
      if (change.op === "add_type") setSelected(change.type);
      if (change.op === "delete_type") setSelected(v.types[0]?.key ?? "");
      return true;
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const def = view?.types.find((t) => t.key === selected);
  const subtypeName = (key: string) => {
    const label = wikiSubtypeLabel(key, "entity");
    return label && label !== key ? `${label}（${key}）` : key;
  };

  return (
    <ModalShell onClose={onClose} maxWidth={1080}>
      <div style={{ width: "100%", fontFamily: T.systemFont, color: T.text }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
          <h3 style={{ margin: 0, fontSize: 17 }}>类型定义</h3>
          <button type="button" onClick={onClose} aria-label="关闭" style={{ border: 0, background: "transparent", cursor: "pointer", color: T.textSecondary }}>
            <X size={18} />
          </button>
        </div>
        <p style={{ margin: "0 0 12px", color: T.textSecondary, fontSize: 13, lineHeight: 1.5 }}>
          每一类知识有哪些属性、到期前多少天提醒、能做哪些行动。改完马上生效：知识页的属性卡片、「整理属性」、小鲶按属性查都按这里来。
          内置的属性和行动不能删，可以隐藏；只改你这台电脑上的设置。
        </p>
        {error && <p style={{ color: T.errorRed, fontSize: 13, margin: "0 0 8px" }}>{error}</p>}
        {!view ? (
          <p style={{ fontSize: 13, color: T.textSecondary }}>读取中…</p>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "180px minmax(0, 1fr)", gap: 16, maxHeight: "68vh" }}>
            <div style={{ borderRight: `1px solid ${T.border}`, paddingRight: 12, overflowY: "auto", overflowX: "hidden", minWidth: 0 }}>
              {view.types.map((t) => (
                <button key={t.key} type="button" onClick={() => setSelected(t.key)}
                  style={{
                    display: "flex", justifyContent: "space-between", width: "100%", padding: "8px 10px", marginBottom: 4,
                    border: 0, borderRadius: 8, cursor: "pointer", fontSize: 14, textAlign: "left",
                    background: t.key === selected ? "rgba(6,182,212,0.12)" : "transparent", color: T.text,
                  }}>
                  <span>{t.label}</span>
                  <small style={{ color: T.textTertiary }}>{t.properties.filter((p) => !p.hidden).length} 项</small>
                </button>
              ))}
              <div style={{ marginTop: 14, display: "grid", gap: 6 }}>
                <div style={{ fontSize: 12, color: T.textSecondary }}>新建类型（从知识库已有的分类里选）</div>
                <select value={newType.key} onChange={(e) => setNewType({ key: e.target.value, label: newType.label || subtypeName(e.target.value).replace(/（.*$/, "") })}
                  aria-label="知识库里的分类" style={{ padding: 6, borderRadius: 6, fontSize: 13, width: "100%", minWidth: 0 }}>
                  <option value="">选一个分类…</option>
                  {view.subtypes.map((s) => <option key={s.key} value={s.key}>{subtypeName(s.key)} · {s.count} 条</option>)}
                </select>
                {newType.key && (
                  <>
                    <input value={newType.label} onChange={(e) => setNewType({ ...newType, label: e.target.value })} maxLength={20}
                      placeholder="类型名" aria-label="新类型名" style={{ padding: "6px 8px", border: `1px solid ${T.border}`, borderRadius: 6, fontSize: 13, width: "100%", boxSizing: "border-box" }} />
                    <button type="button" disabled={busy || !newType.label.trim()}
                      onClick={async () => {
                        if (await apply({ op: "add_type", type: newType.key, label: newType.label.trim() })) setNewType({ key: "", label: "" });
                      }}
                      style={{ padding: "6px 12px", border: 0, borderRadius: 8, background: T.cyan, color: "#fff", cursor: "pointer", fontSize: 13 }}>
                      新建
                    </button>
                  </>
                )}
              </div>
            </div>
            <div style={{ overflowY: "auto", paddingRight: 4 }}>
              {def ? (
                <>
                  <WikiTypeDefEditor def={def} apply={apply} busy={busy} />
                  {def.origin === "local" && (
                    <button type="button" disabled={busy} onClick={() => void apply({ op: "delete_type", type: def.key })}
                      style={{ marginTop: 16, border: 0, background: "transparent", color: T.errorRed, cursor: "pointer", fontSize: 13 }}>
                      删除这个类型（已写进知识的属性不会删）
                    </button>
                  )}
                </>
              ) : (
                <p style={{ fontSize: 13, color: T.textSecondary }}>左边选一个类型。</p>
              )}
            </div>
          </div>
        )}
      </div>
    </ModalShell>
  );
}
