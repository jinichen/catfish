/**
 * 11/07「整理属性」: 把一类知识 (现在是资质证书) 正文里写着的编号 / 发证机构 / 有效期 … 抽成属性。
 *
 * 先按固定句式抽 (秒出, 不花 token); 句式抽不到、正文像是写了的, 可以再「让小鲶读正文补」(一两分钟)。
 * 只给建议: 每个值都能改, 勾上的才写; 写之前每个文件整份备份到 ~/.catfish/wiki/.props-backups/。
 * 小鲶抽的值框是橙色的 —— 那是模型读出来的 (后端已校验值确实出现在正文里), 建议多看一眼。
 */
import { useEffect, useMemo, useState } from "react";
import { SpinnerGap, X } from "@phosphor-icons/react";
import { ModalShell, T } from "../../components/ModalShell";
import {
  wikiPropsApply,
  wikiPropsPropose,
  wikiTypesList,
  type WikiPropsProposeResult,
} from "../../lib/tauri_wiki_types";
import { useWikiStore } from "../../store/wiki";

type Rows = Record<string, { checked: boolean; values: Record<string, string> }>;

export default function WikiPropsReview({ onClose }: { onClose: () => void }) {
  const loadFiles = useWikiStore((s) => s.loadFiles);
  const [types, setTypes] = useState<{ key: string; label: string }[]>([]);
  const [typeKey, setTypeKey] = useState("");
  const [result, setResult] = useState<WikiPropsProposeResult | null>(null);
  const [rows, setRows] = useState<Rows>({});
  const [busy, setBusy] = useState<"" | "pattern" | "model" | "apply">("");
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    void wikiTypesList()
      .then((r) => {
        setTypes(r.types);
        if (r.types[0]) setTypeKey(r.types[0].key);
      })
      .catch((e) => setMessage(`读类型定义失败：${String(e)}`));
  }, []);

  const run = async (useModel: boolean) => {
    if (!typeKey) return;
    setBusy(useModel ? "model" : "pattern");
    setMessage(null);
    try {
      const r = await wikiPropsPropose(typeKey, useModel);
      setResult(r);
      setRows(Object.fromEntries(r.items.map((it) => [it.rel_path, { checked: true, values: { ...it.proposed } }])));
    } catch (e) {
      setMessage(`没抽成：${String(e)}`);
    } finally {
      setBusy("");
    }
  };

  useEffect(() => {
    if (typeKey) void run(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typeKey]);

  // 只列至少一条建议里出现过的属性, 表格不会一排空列
  const columns = useMemo(() => {
    if (!result) return [];
    const used = new Set(result.items.flatMap((it) => Object.keys(it.proposed)));
    return result.properties.filter((p) => used.has(p.key));
  }, [result]);

  const chosen = result ? result.items.filter((it) => rows[it.rel_path]?.checked) : [];

  const apply = async () => {
    const items = chosen
      .map((it) => ({ rel_path: it.rel_path, props: Object.fromEntries(Object.entries(rows[it.rel_path].values).filter(([, v]) => v.trim())) }))
      .filter((it) => Object.keys(it.props).length > 0);
    if (items.length === 0) return;
    setBusy("apply");
    setMessage(null);
    try {
      const r = await wikiPropsApply(items);
      const errs = r.errors.length ? `；${r.errors.length} 条没写成：${r.errors.map((e) => e.error).join("、")}` : "";
      setMessage(`已写入 ${r.written} 条${r.backup_dir ? `，原文件备份在 ${r.backup_dir}` : ""}${errs}`);
      void loadFiles();
      await run(false);
    } catch (e) {
      setMessage(`写入失败：${String(e)}`);
    } finally {
      setBusy("");
    }
  };

  const setValue = (rel: string, key: string, value: string) =>
    setRows((r) => ({ ...r, [rel]: { ...r[rel], values: { ...r[rel].values, [key]: value } } }));
  const allChecked = result ? result.items.every((it) => rows[it.rel_path]?.checked) : false;
  const toggleAll = () =>
    setRows((r) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, { ...v, checked: !allChecked }])));
  const btn = { padding: "7px 12px", border: 0, borderRadius: 8, cursor: "pointer", fontSize: 13 } as const;

  return (
    <ModalShell onClose={onClose}>
      <div className="wiki-props-review" style={{ fontFamily: T.systemFont, color: T.text }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h3 style={{ margin: 0, fontSize: 17 }}>整理属性</h3>
          <button type="button" onClick={onClose} aria-label="关闭" style={{ border: 0, background: "transparent", cursor: "pointer", color: T.textSecondary }}>
            <X size={18} />
          </button>
        </div>
        <p style={{ margin: 0, color: T.textSecondary, fontSize: 13, lineHeight: 1.5 }}>
          把正文里写着的信息抽成属性，之后才能按到期日查、挂类型上的行动。每个值都能改，勾上的才写进去；只改文件头里的属性，正文不动，写之前整份备份。
          <b style={{ color: "#b45309" }}> 橙框</b>是小鲶读正文抽的，多看一眼。
        </p>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          {types.length > 1 && (
            <select value={typeKey} onChange={(e) => setTypeKey(e.target.value)} aria-label="哪一类" style={{ padding: "6px", borderRadius: 8, fontSize: 13 }}>
              {types.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
            </select>
          )}
          {result && (
            <span style={{ fontSize: 13, color: T.textSecondary }}>
              {result.label} {result.scanned} 条，{result.items.length} 条有可补 / 可改的属性
            </span>
          )}
          {result && !result.used_model && result.model_candidates > 0 && (
            <button type="button" disabled={!!busy} onClick={() => void run(true)} style={{ ...btn, background: T.closeBg }}>
              {busy === "model" ? "小鲶在读…（一两分钟）" : `让小鲶读正文补（${result.model_candidates} 条句式没抽全）`}
            </button>
          )}
          {busy === "pattern" && <SpinnerGap className="wiki-spin" size={16} />}
        </div>

        {result && result.items.length === 0 && !busy && (
          <p style={{ fontSize: 13, color: T.textSecondary }}>没有要补的属性了。</p>
        )}
        {result && result.items.length > 0 && (
          <div className="wiki-props-review__table-wrap">
            <table>
              <thead>
                <tr>
                  <th><input type="checkbox" checked={allChecked} onChange={toggleAll} aria-label="全选" /></th>
                  <th>条目</th>
                  {columns.map((c) => <th key={c.key}>{c.label}</th>)}
                </tr>
              </thead>
              <tbody>
                {result.items.map((it) => (
                  <tr key={it.rel_path}>
                    <td>
                      <input
                        type="checkbox"
                        checked={rows[it.rel_path]?.checked ?? false}
                        onChange={(e) => setRows((r) => ({ ...r, [it.rel_path]: { ...r[it.rel_path], checked: e.target.checked } }))}
                        aria-label={`写入《${it.title}》`}
                      />
                    </td>
                    <td className="wiki-props-review__title" title={it.title}>{it.title}</td>
                    {columns.map((c) => (
                      <td key={c.key}>
                        {c.key in it.proposed ? (
                          <>
                            <input
                              type={c.type === "date" ? "date" : "text"}
                              value={rows[it.rel_path]?.values[c.key] ?? ""}
                              data-source={it.source[c.key]}
                              title={it.source[c.key] === "model" ? "小鲶读正文抽的" : "按固定句式抽的"}
                              onChange={(e) => setValue(it.rel_path, c.key, e.target.value)}
                              aria-label={`${it.title} ${c.label}`}
                            />
                            {it.current[c.key] && <span className="wiki-props-review__old">原：{it.current[c.key]}</span>}
                          </>
                        ) : (
                          <span style={{ color: T.textTertiary }}>{it.current[c.key] || ""}</span>
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button type="button" disabled={!!busy || chosen.length === 0} onClick={() => void apply()}
            style={{ ...btn, background: T.cyan, color: "#fff" }}>
            {busy === "apply" ? "写入中…" : `写入勾上的 ${chosen.length} 条`}
          </button>
          {message && <span style={{ fontSize: 13, color: message.includes("失败") ? T.errorRed : T.textSecondary }}>{message}</span>}
        </div>
      </div>
    </ModalShell>
  );
}
