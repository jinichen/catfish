/** 知识体系「层级」视图 (10/23): 选中的顶层往下展开成一棵树。
 *
 * 关系图是力导向布局 —— 按连接松紧摆, 没有上下之分, 公司→部门→人员 这种层级在图上看不出来。
 * 这里只看「层级型」关系 (隶属 / 包含 / 员工登记的上下级关系, 见 wikiHierarchy.ts), 拼成能折叠的树。
 * 点节点名字打开那个条目。
 */
import { useMemo, useState } from "react";
import { CaretDown, CaretRight, TreeStructure } from "@phosphor-icons/react";
import { customHierarchy, useRelationVocab } from "../../lib/wikiRelationVocab";
import { useWikiStore } from "../../store/wiki";
import { buildHierarchy, type HierarchyModel } from "./wikiHierarchy";

const DEFAULT_OPEN_DEPTH = 1;

function Node({
  path, model, depth, onOpen, trail,
}: {
  path: string;
  model: HierarchyModel;
  depth: number;
  onOpen: (path: string) => void;
  trail: Set<string>;
}) {
  const kids = model.children.get(path) ?? [];
  const [open, setOpen] = useState(depth < DEFAULT_OPEN_DEPTH);
  const file = model.byPath.get(path);
  const cyclic = trail.has(path);
  return (
    <li className="wiki-hier__node">
      <div className="wiki-hier__row" style={{ paddingLeft: depth * 18 }}>
        {kids.length > 0 && !cyclic ? (
          <button type="button" className="wiki-hier__toggle" onClick={() => setOpen(!open)} aria-label={open ? "收起" : "展开"}>
            {open ? <CaretDown size={14} /> : <CaretRight size={14} />}
          </button>
        ) : (
          <span className="wiki-hier__toggle" />
        )}
        <button type="button" className="wiki-hier__name" onClick={() => onOpen(path)} title="打开这个条目">
          {file?.title ?? path}
        </button>
        {file?.subtype && <small className="wiki-hier__type">{file.subtype}</small>}
        {kids.length > 0 && <small className="wiki-hier__count">{kids.length}</small>}
        {cyclic && <small className="wiki-hier__type">(循环)</small>}
      </div>
      {open && !cyclic && kids.length > 0 && (
        <ul>
          {[...kids]
            .sort((a, b) =>
              (model.children.get(b)?.length ?? 0) - (model.children.get(a)?.length ?? 0) ||
              (model.byPath.get(a)?.title ?? "").localeCompare(model.byPath.get(b)?.title ?? "", "zh-CN"))
            .map((k) => (
              <Node key={k} path={k} model={model} depth={depth + 1} onOpen={onOpen} trail={new Set([...trail, path])} />
            ))}
        </ul>
      )}
    </li>
  );
}

export function useHierarchyModel(): HierarchyModel {
  const files = useWikiStore((state) => state.files);
  const custom = useRelationVocab((state) => state.custom);
  return useMemo(() => buildHierarchy(files, customHierarchy(custom)), [files, custom]);
}

export default function WikiHierarchyView({ root, onOpen }: { root: string | null; onOpen: (path: string) => void }) {
  const model = useHierarchyModel();
  const current = root && model.byPath.has(root) ? root : model.roots[0]?.path ?? null;
  if (!current) {
    return (
      <div className="wiki-workbench__empty">
        <TreeStructure size={36} weight="duotone" aria-hidden="true" />
        <strong>还没有上下级关系</strong>
        <span>用「隶属 / 包含」(或登记成上下级的自定义关系) 连起来的条目, 会在这里排成层级。</span>
      </div>
    );
  }
  const total = model.roots.find((r) => r.path === current)?.size ?? 0;
  return (
    <div className="wiki-workbench wiki-hier">
      <header className="wiki-workbench__header">
        <h2>{model.byPath.get(current)?.title}</h2>
        <p>下面共 {total} 项 (按「隶属 / 包含」等上下级关系)。点名字打开条目。</p>
      </header>
      <ul className="wiki-hier__tree">
        <Node key={current} path={current} model={model} depth={0} onOpen={onOpen} trail={new Set()} />
      </ul>
    </div>
  );
}
