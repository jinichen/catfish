/** WikiTree 的分组渲染 —— Group / EntityGroup / ConceptGroup / TypeSubgroup。
 *
 * 9/24 (鸿波"左侧也应该优化一下") 重做:
 *
 *   · 分组依据: 以前对象按 related[0] (第一条关系的名字) 分组 —— 结果冒出
 *     「资质对标分析流程 2」「邮件分级… 2」「ISO 20000… 1」这种按偶然关系拼出来的组,
 *     一大半组只有 1 条。现在对象、主题都按类型 (组织/部门/人员/项目/证书…、
 *     知识体系/流程/规则/原则/标准/口径…) 分组, WikiTree 里算好传进来。
 *   · 样式: 以前全是内联 style, 三层字号/缩进/粗细各写各的, 外面 WikiOrganizer 还用
 *     !important 盖一层; 每条后面挂「✎」+ 类型小字, 长标题折行后小字掉到第二行。
 *     现在统一走 globals.css 的 .wiki-tree-* 类: 一行一条, 超长省略, 全名在 tooltip;
 *     组名已经说明类型, 条目不再重复挂类型; 只保留「待确认」这一个需要行动的标记。
 *   · 折叠状态 localStorage key 沿用原来的命名, 老的展开/折叠偏好不丢。
 *
 * 只有 WikiTree 用这些组件, 别处不要 import。
 */
import { useState } from "react";
import type { WikiFileInfo } from "../../lib/tauri";

function useCollapsed(key: string, defaultCollapsed: boolean): [boolean, () => void] {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem(key);
      return v === null ? defaultCollapsed : v === "1";
    } catch {
      return defaultCollapsed;
    }
  });
  const toggle = () =>
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem(key, next ? "1" : "0");
      } catch {
        /* 配额满 ignore */
      }
      return next;
    });
  return [collapsed, toggle];
}

function itemTitle(f: WikiFileInfo): string {
  const notes = [f.title];
  if (f.ontology_status === "pending") notes.push("待确认: 关系还没核对");
  if (f.authored_by === "employee") notes.push("你确认过 —— 后台整理不会覆盖正文");
  return notes.join("\n");
}

function TreeItems({
  files,
  selectedPath,
  onSelect,
}: {
  files: WikiFileInfo[];
  selectedPath: string | null;
  onSelect: (relPath: string) => void;
}) {
  return (
    <ul className="wiki-tree-items">
      {files.map((f) => (
        <li key={f.rel_path}>
          <button
            type="button"
            className="wiki-tree-item"
            data-active={selectedPath === f.rel_path}
            onClick={() => onSelect(f.rel_path)}
            title={itemTitle(f)}
          >
            <span className="wiki-tree-item__title">{f.title}</span>
            {f.ontology_status === "pending" && <span className="wiki-tree-item__flag">待确认</span>}
          </button>
        </li>
      ))}
    </ul>
  );
}

function GroupHeader({
  label,
  count,
  open,
  onToggle,
  level,
}: {
  label: string;
  count: number;
  open: boolean;
  onToggle: () => void;
  level: 1 | 2;
}) {
  return (
    <button
      type="button"
      className={level === 1 ? "wiki-tree-group__header" : "wiki-tree-subgroup__header"}
      aria-expanded={open}
      onClick={onToggle}
    >
      <span className="wiki-tree__caret" data-open={open} aria-hidden="true">▶</span>
      <span className="wiki-tree-group__label">{label}</span>
      <span className="wiki-tree-group__count">{count}</span>
    </button>
  );
}

export function Group({
  label,
  files,
  selectedPath,
  onSelect,
  forceOpen = false,
}: {
  label: string;
  /** 旧参数, 已不用 (保留签名, 调用方不必改) */
  emoji?: string;
  color?: string;
  files: WikiFileInfo[];
  selectedPath: string | null;
  onSelect: (relPath: string) => void;
  /** 搜索/筛选生效时强制展开 */
  forceOpen?: boolean;
}) {
  const [collapsed, toggle] = useCollapsed(`wiki_group_collapsed_${label}`, false);
  const open = forceOpen || !collapsed;
  if (files.length === 0) return null;
  return (
    <section className="wiki-tree-group">
      <GroupHeader label={label} count={files.length} open={open} onToggle={toggle} level={1} />
      {open && <TreeItems files={files} selectedPath={selectedPath} onSelect={onSelect} />}
    </section>
  );
}

function TypeSubgroup({
  label,
  files,
  selectedPath,
  onSelect,
  forceOpen,
  defaultCollapsed,
}: {
  label: string;
  files: WikiFileInfo[];
  selectedPath: string | null;
  onSelect: (relPath: string) => void;
  forceOpen: boolean;
  defaultCollapsed: boolean;
}) {
  const [collapsed, toggle] = useCollapsed(`wiki_subgroup_collapsed_${label}`, defaultCollapsed);
  // 选中的条目在这个组里 → 展开, 否则从右侧/图谱跳过来时左边看不到它在哪
  const containsSelected = !!selectedPath && files.some((f) => f.rel_path === selectedPath);
  const open = forceOpen || containsSelected || !collapsed;
  if (files.length === 0) return null;
  return (
    <div className="wiki-tree-subgroup">
      <GroupHeader label={label} count={files.length} open={open} onToggle={toggle} level={2} />
      {open && <TreeItems files={files} selectedPath={selectedPath} onSelect={onSelect} />}
    </div>
  );
}

function TypedGroup({
  label,
  storageKey,
  defaultCollapsed,
  total,
  categories,
  selectedPath,
  onSelect,
  forceOpen,
}: {
  label: string;
  storageKey: string;
  defaultCollapsed: boolean;
  total: number;
  categories: Array<[string, WikiFileInfo[]]>;
  selectedPath: string | null;
  onSelect: (relPath: string) => void;
  forceOpen: boolean;
}) {
  const [collapsed, toggle] = useCollapsed(storageKey, defaultCollapsed);
  const containsSelected = !!selectedPath && categories.some(([, list]) => list.some((f) => f.rel_path === selectedPath));
  const open = forceOpen || containsSelected || !collapsed;
  if (total === 0) return null;
  return (
    <section className="wiki-tree-group">
      <GroupHeader label={label} count={total} open={open} onToggle={toggle} level={1} />
      {open && (
        <div className="wiki-tree-group__body">
          {categories.map(([category, list]) => (
            <TypeSubgroup
              key={category}
              label={category}
              files={list}
              forceOpen={forceOpen}
              // 条目少的类型默认展开, 多的 (证书 70+) 默认收起
              defaultCollapsed={list.length > 12}
              selectedPath={selectedPath}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </section>
  );
}

type TypedGroupProps = {
  total: number;
  categories: Array<[string, WikiFileInfo[]]>;
  selectedPath: string | null;
  onSelect: (relPath: string) => void;
  forceOpen?: boolean;
};

/** 对象 (entities): 按 entity_type 分组。storage key 沿用原「实体 (entities)」。 */
export function EntityGroup({ forceOpen = false, ...rest }: TypedGroupProps) {
  return (
    <TypedGroup label="对象" storageKey="wiki_group_collapsed_实体 (entities)" defaultCollapsed={false} forceOpen={forceOpen} {...rest} />
  );
}

/** 主题 (concepts): 按 concept_type 分组, 知识体系排最前。storage key 沿用原「概念 (concepts)」。 */
export function ConceptGroup({ forceOpen = false, ...rest }: TypedGroupProps) {
  return (
    <TypedGroup label="主题" storageKey="wiki_group_collapsed_概念 (concepts)" defaultCollapsed={false} forceOpen={forceOpen} {...rest} />
  );
}
