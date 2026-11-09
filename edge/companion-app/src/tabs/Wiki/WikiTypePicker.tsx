/** 页面顶上的分类: 点一下就能改 (11/09)。
 *
 * 鸿波截图「AAA信用等级证书」掉在「未分类」里问「这种分类错的, 怎么修正?」—— 当时界面上没有任何地方能改:
 * 编辑只改正文, frontmatter 不动。现在分类旁边一个「改」, 候选是同一类 (对象 / 主题) 里已经在用的类型
 * (按显示名去重, 每个取最常用的写法, 免得再多出一种写法), 也能填一个新的。
 * 只改 entity_type / concept_type 这一行, 走 wiki_update_file (会打 authored_by: employee, 后台整理不再覆盖)。
 */
import { useMemo, useState } from "react";

import { wikiReadFile, wikiUpdateFile, type WikiFileInfo } from "../../lib/tauri_wiki";
import { useWikiStore } from "../../store/wiki";
import { setFrontmatterScalar, subtypeOptions, wikiSubtypeLabel } from "./wikiLabels";

const OTHER = "__other__";

export default function WikiTypePicker({ info, readOnly }: { info: WikiFileInfo; readOnly?: boolean }) {
  const files = useWikiStore((s) => s.files);
  const loadFiles = useWikiStore((s) => s.loadFiles);
  const selectFile = useWikiStore((s) => s.selectFile);
  const [open, setOpen] = useState(false);
  const [other, setOther] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const field = info.kind === "entity" ? "entity_type" : info.kind === "concept" ? "concept_type" : null;
  const label = info.subtype ? wikiSubtypeLabel(info.subtype, info.kind) : "未分类";
  const options = useMemo(() => (open ? subtypeOptions(files, info.kind) : []), [open, files, info.kind]);

  if (!field || readOnly) return <span>{label}</span>;

  const save = async (value: string) => {
    const v = value.trim();
    if (!v || v === info.subtype) {
      setOpen(false);
      return;
    }
    if (/[\n:#[\]{}"]/.test(v)) {
      setErr("分类名里不能有换行、冒号、#、括号或引号");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const full = await wikiReadFile(info.rel_path);
      await wikiUpdateFile(info.rel_path, setFrontmatterScalar(full.content, field, v));
      await loadFiles();
      await selectFile(info.rel_path);
      setOpen(false);
      setOther(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button
        type="button"
        className={`wiki-type-picker__btn${info.subtype ? "" : " wiki-type-picker__btn--none"}`}
        onClick={() => setOpen(true)}
        title="改分类 —— 左边目录按它分组"
      >
        {label} <span className="wiki-type-picker__edit">改</span>
      </button>
    );
  }

  return (
    <span className="wiki-type-picker">
      {other === null ? (
        <select
          aria-label="分类"
          autoFocus
          disabled={busy}
          value={info.subtype ?? ""}
          onChange={(e) => (e.target.value === OTHER ? setOther("") : void save(e.target.value))}
        >
          {!info.subtype && <option value="">未分类</option>}
          {info.subtype && !options.some((o) => o.key === info.subtype) && <option value={info.subtype}>{label}</option>}
          {options.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label} ({o.count})
            </option>
          ))}
          <option value={OTHER}>其他分类…</option>
        </select>
      ) : (
        <>
          <input
            aria-label="新分类"
            autoFocus
            placeholder="新分类名, 例如 资质证书"
            value={other}
            disabled={busy}
            onChange={(e) => setOther(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void save(other);
              if (e.key === "Escape") setOther(null);
            }}
          />
          <button type="button" className="wiki-type-picker__act" disabled={busy || !other.trim()} onClick={() => void save(other)}>
            保存
          </button>
        </>
      )}
      <button
        type="button"
        className="wiki-type-picker__act"
        disabled={busy}
        onClick={() => {
          setOpen(false);
          setOther(null);
          setErr(null);
        }}
      >
        取消
      </button>
      {busy && <span className="wiki-type-picker__hint">保存中…</span>}
      {err && <span className="wiki-type-picker__err">{err}</span>}
    </span>
  );
}
