/** WikiTree 的分组渲染 —— Group / EntityGroup / CategorySubgroup / ConceptGroup。
 *
 * 8/15 从 WikiTree.tsx 搬出来 (1215 行, 过了 CLAUDE.md §1 的 800 红线)。
 *
 * # 为什么这四个一起走
 *
 * 它们是一个递归家族:
 *
 *     EntityGroup  ─┐
 *                   ├→ CategorySubgroup
 *     ConceptGroup ─┘
 *     Group          (最朴素那版, queries 还在用)
 *
 * 而且共享同一套折叠交互约定 —— localStorage 的 key 命名必须彼此兼容, 不然
 * 员工原来展开/折叠的状态会在升级后凭空重置 (见 EntityGroup 上方注释里
 * "localStorage key 跟原 Group 保持兼容" 那句)。把它们分到两个文件, 下一个
 * 人改 key 的时候就少看见一半。
 *
 * # 只有 WikiTree 用它们
 *
 * 所以这里 export 的四个名字对外都不算 API, 别的地方不要 import。
 */
import { useState } from "react";
import { useWikiStore } from "../../store/wiki";
import type { WikiFileInfo } from "../../lib/tauri";
import { wikiSubtypeLabel } from "./wikiLabels";

export function Group({
  label,
  emoji,
  color,
  files,
  selectedPath,
  onSelect,
  forceOpen = false,
}: {
  label: string;
  emoji: string;
  color: string;
  files: WikiFileInfo[];
  selectedPath: string | null;
  onSelect: (relPath: string) => void;
  /** 8/3: 搜索/筛选生效时强制展开 */
  forceOpen?: boolean;
}) {
  // P40 (6/5 鸿波): Group collapsible. localStorage 记 collapse state per label.
  // 默认: entities (常长 21+) 折起; concepts/queries 展开. Wiki 累积后 sidebar
  // 一眼看 3 group header, 不用 scroll.
  const lsKey = `wiki_group_collapsed_${label}`;
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem(lsKey);
      if (v === null) return label.includes("实体"); // entity 默认折 (最长)
      return v === "1";
    } catch {
      return false;
    }
  });
  // 8/3: forceOpen —— 搜索/筛选生效时无视 collapsed。折叠是"我暂时不想看",
  // 不是"即使我在搜也别给我看"。见 WikiTree 里 isFiltering 那段注释。
  const isOpen = forceOpen || !collapsed;
  const toggle = () => {
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem(lsKey, next ? "1" : "0");
      } catch {
        /* 配额满 ignore */
      }
      return next;
    });
  };
  if (files.length === 0) return null;
  return (
    <div className={"wiki-group " + (isOpen ? "wiki-group--open" : "")}>
      <div
        className="wiki-group__header"
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        }}
        role="button"
        tabIndex={0}
        aria-expanded={isOpen}
        style={{ color }}
      >
        <span className="wiki-group__caret">▶</span>
        <span>{emoji ? `${emoji} ` : ""}{label}</span>
        <span style={{ fontWeight: 400, color: "var(--catfish-text-muted)", marginLeft: "auto" }}>
          {files.length}
        </span>
      </div>
      {isOpen && (
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {files.map((f) => {
          const active = selectedPath === f.rel_path;
          return (
            <li key={f.rel_path}>
              <button
                onClick={() => onSelect(f.rel_path)}
                title={f.rel_path}
                style={{
                  display: "block",
                  width: "100%",
                  textAlign: "left",
                  padding: "4px 8px",
                  fontSize: 14,
                  border: "none",
                  background: active ? "var(--catfish-bg-hover, #e8f0ff)" : "transparent",
                  color: active ? color : "var(--catfish-text)",
                  cursor: "pointer",
                  borderRadius: 4,
                  fontWeight: active ? 600 : 400,
                }}
              >
                {f.title}
                {f.ontology_status === "pending" && (
                  <span
                    title="关系尚未唯一确认；不会进入关系图"
                    style={{ marginLeft: 6, color: "var(--catfish-orange, #F47B3D)", fontSize: 12 }}
                  >
                    待确认
                  </span>
                )}
                {/* 8/4: 员工确认过的打个记号。219/220 条是 LLM 生成的, 在此之前
                    员工完全看不出哪些是没人看过的机器输出。 */}
                {f.authored_by === "employee" && (
                  <span
                    title="你确认过这条 —— 后台蒸馏不会覆盖它的正文"
                    style={{ marginLeft: 4, opacity: 0.7, fontSize: 12 }}
                  >
                    ✎
                  </span>
                )}
                {f.subtype && (
                  <span style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginLeft: 6 }}>
                    {wikiSubtypeLabel(f.subtype, f.kind)}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
      )}
    </div>
  );
}

/** P3.5.99 (6/24 鸿波) — 实体二级分组 group: 顶层 "实体 (62)" + 内嵌 N 个
 *  CategorySubgroup ("信息安全与安防类 (15)" 等), 跟图谱里 concept hub 一致.
 *
 *  数据驱动: entity.related[0] (P3.5.42.13 后端 merge body wikilink), 0 人工干预.
 *  localStorage key 跟原 Group "实体 (entities)" 保持兼容 (用户原折叠状态不丢).
 */
export function EntityGroup({
  total,
  categories,
  selectedPath,
  onSelect,
  forceOpen = false,
}: {
  total: number;
  categories: Array<[string, WikiFileInfo[]]>;
  selectedPath: string | null;
  onSelect: (relPath: string) => void;
  /** 8/3: 搜索/筛选生效时强制展开 */
  forceOpen?: boolean;
}) {
  const lsKey = "wiki_group_collapsed_实体 (entities)"; // 跟原 Group 一致, 不丢用户折叠
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem(lsKey);
      if (v === null) return true; // 默认折 (62 行太长)
      return v === "1";
    } catch {
      return true;
    }
  });
  // 8/3: forceOpen —— 搜索/筛选生效时无视 collapsed。折叠是"我暂时不想看",
  // 不是"即使我在搜也别给我看"。见 WikiTree 里 isFiltering 那段注释。
  const isOpen = forceOpen || !collapsed;
  const toggle = () => {
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem(lsKey, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  };
  if (total === 0) return null;
  return (
    <div className={"wiki-group " + (isOpen ? "wiki-group--open" : "")}>
      <div
        className="wiki-group__header"
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        }}
        role="button"
        tabIndex={0}
        aria-expanded={isOpen}
        style={{ color: "var(--catfish-cyan)" }}
      >
        <span className="wiki-group__caret">▶</span>
        <span>对象</span>
        <span style={{ fontWeight: 400, color: "var(--catfish-text-muted)", marginLeft: "auto" }}>
          {total}
        </span>
      </div>
      {isOpen && (
        <div style={{ paddingLeft: 8 }}>
          {categories.map(([category, list]) => (
            <CategorySubgroup
              key={category}
              category={category}
              files={list}
              forceOpen={forceOpen}
              selectedPath={selectedPath}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** P3.5.99 — 实体的二级 category 子组. 跟 Group 同款 collapsible + localStorage,
 *  但视觉更紧 (字号 / padding 缩小, 加缩进区分). 默认折叠 (二级太多, 全开会爆).
 *
 *  P3.5.110 (6/25 鸿波 catch "体系名称不能选择") 升级:
 *  - 点 caret (▶) → 折叠 (跟原行为一致, stopPropagation)
 *  - 点 category name → 优先 selectFile(找到的 concept file), dangling 显示虚拟态
 *    — 缺失分类不自动落盘，避免把展示分组变成本体节点
 */
function CategorySubgroup({
  category,
  files,
  selectedPath,
  onSelect,
  forceOpen = false,
}: {
  category: string;
  files: WikiFileInfo[];
  selectedPath: string | null;
  onSelect: (relPath: string) => void;
  /** 8/3: 搜索/筛选生效时强制展开 */
  forceOpen?: boolean;
}) {
  // P3.5.110: 从 store 拿 files (全 wiki) lookup, 跟 onSelect 同源
  const allFiles = useWikiStore((s) => s.files);
  // P3.5.111: dangling 体系名 click → setVirtualSystem (而不是 openCreateModal — 鸿波 catch)
  const setVirtualSystem = useWikiStore((s) => s.setVirtualSystem);
  // P3.5.113: dangling click 也 clear selectedPath → 触发 useMemo rebuild
  const selectFile = useWikiStore((s) => s.selectFile);
  const lsKey = `wiki_subgroup_collapsed_${category}`;
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem(lsKey);
      if (v === null) return true; // 默认折
      return v === "1";
    } catch {
      return true;
    }
  });
  // 8/3: forceOpen —— 搜索/筛选生效时无视 collapsed。折叠是"我暂时不想看",
  // 不是"即使我在搜也别给我看"。见 WikiTree 里 isFiltering 那段注释。
  const isOpen = forceOpen || !collapsed;
  const toggle = () => {
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem(lsKey, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  };
  // P3.5.110-114: 点 category name 真行为：
  //   - 特殊组 ("🌟 顶级体系" / "未分类") → 只 toggle
  //   - 真实文件 (match): setVirtualSystem(null) + selectFile(rel_path) → 切到该 system 子树
  //     (清旧虚拟态, 避免被它优先级压住)
  //   - dangling (虚拟体系): 只显示虚拟态，不自动写入 concept 文件。
  //     真实条目必须由员工明确新建，避免普通分类/来源路径污染本体。
  const handleCategoryClick = async (e: React.MouseEvent | React.KeyboardEvent) => {
    e.stopPropagation();
    const isSpecialGroup =
      category === "知识体系" || category === "未分类";
    if (isSpecialGroup) {
      toggle();
      return;
    }
    // 找 wiki/concepts/<category>.md file — 跟 WikiPreview handleWikilinkClick 同 lookup
    const lower = category.toLowerCase().trim();
    const match = allFiles.find(
      (f) =>
        f.title.toLowerCase() === lower ||
        f.slug.toLowerCase() === lower ||
        f.title.toLowerCase().includes(lower),
    );
    if (match) {
      // P3.5.113: 清旧虚拟态 — 真实system file 走 effectiveRoot 真真实 path** +
      // isSystemConcept → subtree (真实). 不清虚拟会被它优先级压住, 显错位的旧虚拟体系.
      setVirtualSystem(null);
      onSelect(match.rel_path);
      return;
    }
    // 缺失分类只显示虚拟体系，不自动落盘。
    setVirtualSystem(category);
    void selectFile(null);
  };
  if (files.length === 0) return null;
  return (
    <div
      style={{
        margin: "2px 0",
        borderLeft: "2px solid var(--catfish-border)",
        paddingLeft: 6,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          padding: "3px 6px",
          fontSize: 13,
          color: category === "未分类" ? "var(--catfish-text-muted)" : "var(--catfish-text)",
          userSelect: "none",
          fontWeight: 500,
        }}
      >
        {/* P3.5.110: caret 单独 button — stopPropagation 后 toggle, 不影响 name 点击 */}
        <span
          role="button"
          tabIndex={0}
          aria-expanded={isOpen}
          aria-label={isOpen ? "折叠" : "展开"}
          onClick={(e) => {
            e.stopPropagation();
            toggle();
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              toggle();
            }
          }}
          style={{
            display: "inline-block",
            transition: "transform 0.15s",
            transform: isOpen ? "rotate(90deg)" : "rotate(0deg)",
            fontSize: 9,
            cursor: "pointer",
            padding: "0 2px",
          }}
        >
          ▶
        </span>
        {/* P3.5.110: name 独立 click target — selectFile / 弹建 modal */}
        <span
          role="button"
          tabIndex={0}
          onClick={handleCategoryClick}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              handleCategoryClick(e);
            }
          }}
          title={
            category === "知识体系" || category === "未分类"
              ? `点击折叠/展开 (特殊组)`
              : `点击跳到"${category}" 真 preview (dangling 弹 +新建)`
          }
          style={{
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            cursor: "pointer",
            padding: "0 2px",
          }}
        >
          {category}
        </span>
        <span style={{ fontSize: 12, color: "var(--catfish-text-muted)", fontWeight: 400 }}>
          {files.length}
        </span>
      </div>
      {isOpen && (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {files.map((f) => {
            const active = selectedPath === f.rel_path;
            return (
              <li key={f.rel_path}>
                <button
                  onClick={() => onSelect(f.rel_path)}
                  title={f.rel_path}
                  style={{
                    display: "block",
                    width: "100%",
                    textAlign: "left",
                    padding: "3px 8px 3px 14px",
                    fontSize: 14,
                    border: "none",
                    background: active ? "var(--catfish-bg-hover, #e8f0ff)" : "transparent",
                    color: active ? "var(--catfish-cyan)" : "var(--catfish-text)",
                    cursor: "pointer",
                    borderRadius: 4,
                    fontWeight: active ? 600 : 400,
                  }}
                >
                  {f.title}
                  {f.ontology_status === "pending" && (
                    <span
                      title="关系尚未唯一确认；不会进入关系图"
                      style={{ marginLeft: 6, color: "var(--catfish-orange, #F47B3D)", fontSize: 12 }}
                    >
                      待确认
                    </span>
                  )}
                {/* 8/4: 员工确认过的打个记号。219/220 条是 LLM 生成的, 在此之前
                    员工完全看不出哪些是没人看过的机器输出。 */}
                {f.authored_by === "employee" && (
                  <span
                    title="你确认过这条 —— 后台蒸馏不会覆盖它的正文"
                    style={{ marginLeft: 4, opacity: 0.7, fontSize: 12 }}
                  >
                    ✎
                  </span>
                )}
                  {f.subtype && (
                    <span style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginLeft: 6 }}>
                      {wikiSubtypeLabel(f.subtype, f.kind)}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/** P3.5.182 (7/6 鸿波军规审判) — concept 二级分组 group: 顶层 "概念 (N)" + 内嵌
 *  按 concept.subtype 二分: "🌟 顶级体系" (subtype=system) + "概念" (其他, 平铺).
 *
 *  严格 P3.5.107 A (6/25) 老逻辑严格按 related[0] 上位体系分组 → 严格 hardcode
 *  assumption (位置约定当 semantic) 严格军规违反. P3.5.182 严格 删除.
 *
 *  localStorage key 跟原 Group "概念 (concepts)" 真兼容 — 用户原折叠态不丢.
 */
export function ConceptGroup({
  total,
  categories,
  selectedPath,
  onSelect,
  forceOpen = false,
}: {
  total: number;
  categories: Array<[string, WikiFileInfo[]]>;
  selectedPath: string | null;
  onSelect: (relPath: string) => void;
  /** 8/3: 搜索/筛选生效时强制展开 */
  forceOpen?: boolean;
}) {
  const lsKey = "wiki_group_collapsed_概念 (concepts)"; // 跟原 Group 一致, 不丢用户折叠
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem(lsKey);
      if (v === null) return false; // 默认开 (concept 数量小)
      return v === "1";
    } catch {
      return false;
    }
  });
  // 8/3: forceOpen —— 搜索/筛选生效时无视 collapsed。折叠是"我暂时不想看",
  // 不是"即使我在搜也别给我看"。见 WikiTree 里 isFiltering 那段注释。
  const isOpen = forceOpen || !collapsed;
  const toggle = () => {
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem(lsKey, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  };
  if (total === 0) return null;
  return (
    <div className={"wiki-group " + (isOpen ? "wiki-group--open" : "")}>
      <div
        className="wiki-group__header"
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        }}
        role="button"
        tabIndex={0}
        aria-expanded={isOpen}
        style={{ color: "var(--catfish-cyan-bright)" }}
      >
        <span className="wiki-group__caret">▶</span>
        <span>主题</span>
        <span style={{ fontWeight: 400, color: "var(--catfish-text-muted)", marginLeft: "auto" }}>
          {total}
        </span>
      </div>
      {isOpen && (
        <div style={{ paddingLeft: 8 }}>
          {categories.map(([category, list]) => (
            <CategorySubgroup
              key={category}
              category={category}
              files={list}
              forceOpen={forceOpen}
              selectedPath={selectedPath}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}
