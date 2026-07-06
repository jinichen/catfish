/** XlsxImportModal — P3.5.173 从 xlsx 批量导入组织架构 (7/3 鸿波拍板 C).
 *
 * 3 Stage UX (状态机):
 *   1. pick    : 选文件 (native <input type="file"> 严格无 dialog plugin)
 *   2. preview : SheetJS parse 严格 tree preview + 员工 checkbox 每层确认
 *   3. importing / done / error : batch wikiCreateEntityOrConcept + progress
 *
 * 严格员工主权:
 *   - 员工 checkbox 每 sheet / 一级部 / 二级部 严格确认哪些要建
 *   - 员工可 rename (未实现, 后续可加)
 *   - 严格错误 skip 继续, 不阻塞 batch
 *   - 严格总量提示 (X sheet, Y 一级部, Z 二级部) 严格让员工严格心里有数
 *
 * 严格数据主权:
 *   - parse 严格前端 (员工 mac local), 数据严格不出端
 *   - batch create 严格调 tauri command 严格本地 fs
 *
 * 严格 wikilink 关联树 (batch create 严格顺序):
 *   1. 创建 3 concept (市场经营体系 / 生产运营体系 / 典型职能体系)
 *      related: ["[[组织架构]]"] 严格挂顶级体系
 *   2. 创建一级部 entity (subtype=org)
 *      related: ["[[<concept name>]]"] 严格指向对应 concept
 *   3. 创建二级部 entity (subtype=org)
 *      related: ["[[<一级部 name>]]"] 严格指向对应一级部
 *   → 严格建成完整树: 组织架构 → concept → 一级部 → 二级部
 *
 * 复用 RecMode ModalShell + T theme (UX 一致跟 SetupModal/LearnModal/
 * WikiLinkSuggestModal).
 */

import { useState } from "react";
import { ModalShell, T } from "../Chat/RecMode/shared";
import { wikiCreateEntityOrConcept } from "../../lib/tauri";
import {
  parseOrgXlsx,
  countSelectedTree,
  type XlsxSheetTree,
} from "../../lib/xlsxImport";
import { useWikiStore } from "../../store/wiki";

interface Props {
  onClose: () => void;
  /** 完成后 WikiTree 刷 files 拿新 entity/concept. */
  onImported: () => void;
}

type Stage =
  | "pick" // 严格选文件
  | "parsing" // 严格解析中
  | "preview" // 严格 tree preview + 员工确认
  | "importing" // 严格 batch create + progress
  | "done" // 严格完成
  | "error"; // 严格错误 (parse or import)

interface ImportProgress {
  current: number;
  total: number;
  currentName: string;
  successCount: number;
  failCount: number;
  errors: string[]; // 严格失败记录 (只前 10 条)
}

export default function XlsxImportModal({ onClose, onImported }: Props) {
  const files = useWikiStore((s) => s.files);
  const loadFiles = useWikiStore((s) => s.loadFiles);

  const [stage, setStage] = useState<Stage>("pick");
  const [trees, setTrees] = useState<XlsxSheetTree[]>([]);
  const [fileName, setFileName] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  // 严格 selection state (员工 checkbox)
  const [selConcepts, setSelConcepts] = useState<Set<string>>(new Set());
  const [selPrimaries, setSelPrimaries] = useState<Set<string>>(new Set());
  const [selSecondaries, setSelSecondaries] = useState<Set<string>>(new Set());

  // 严格顶级"组织架构" 严格 wikilink target — 员工严格可改 (默认 "组织架构")
  const [rootTitle, setRootTitle] = useState<string>("组织架构");

  const [progress, setProgress] = useState<ImportProgress>({
    current: 0,
    total: 0,
    currentName: "",
    successCount: 0,
    failCount: 0,
    errors: [],
  });

  const [closeHover, setCloseHover] = useState(false);
  const [applyHover, setApplyHover] = useState(false);

  // 严格 file picker 触发 (native input)
  async function handleFileSelected(file: File) {
    setFileName(file.name);
    setStage("parsing");
    setError(null);
    try {
      const buf = await file.arrayBuffer();
      const parsed = parseOrgXlsx(buf);
      if (parsed.length === 0) {
        setError(
          "严格 xlsx 无 header 含'一级部' 的 sheet. 严格 heuristic 找不到组织架构结构.",
        );
        setStage("error");
        return;
      }
      setTrees(parsed);
      // 严格默认全选 (员工严格可 uncheck)
      const cSet = new Set<string>();
      const pSet = new Set<string>();
      const sSet = new Set<string>();
      for (const t of parsed) {
        cSet.add(t.concept);
        for (const p of t.primary) {
          pSet.add(`${t.concept}||${p.name}`);
          for (const s of p.secondary) {
            sSet.add(`${t.concept}||${p.name}||${s}`);
          }
        }
      }
      setSelConcepts(cSet);
      setSelPrimaries(pSet);
      setSelSecondaries(sSet);
      setStage("preview");
    } catch (e) {
      setError(`严格 parse 失败: ${(e as Error).message || e}`);
      setStage("error");
    }
  }

  // 严格 toggle 严格 concept 严格 (全选/取消)
  function toggleConcept(name: string) {
    setSelConcepts((cur) => {
      const next = new Set(cur);
      if (next.has(name)) {
        next.delete(name);
        // 严格 cascading — 严格取消 concept 严格连带取消 primary + secondary
        const t = trees.find((x) => x.concept === name);
        if (t) {
          const pNext = new Set(selPrimaries);
          const sNext = new Set(selSecondaries);
          for (const p of t.primary) {
            pNext.delete(`${name}||${p.name}`);
            for (const s of p.secondary) {
              sNext.delete(`${name}||${p.name}||${s}`);
            }
          }
          setSelPrimaries(pNext);
          setSelSecondaries(sNext);
        }
      } else {
        next.add(name);
      }
      return next;
    });
  }

  // 严格 toggle 严格 primary
  function togglePrimary(concept: string, primary: string) {
    const key = `${concept}||${primary}`;
    setSelPrimaries((cur) => {
      const next = new Set(cur);
      if (next.has(key)) {
        next.delete(key);
        // 严格 cascading 严格取消 secondary
        const t = trees.find((x) => x.concept === concept);
        const p = t?.primary.find((x) => x.name === primary);
        if (p) {
          const sNext = new Set(selSecondaries);
          for (const s of p.secondary) {
            sNext.delete(`${concept}||${primary}||${s}`);
          }
          setSelSecondaries(sNext);
        }
      } else {
        next.add(key);
      }
      return next;
    });
  }

  // 严格 toggle 严格 secondary
  function toggleSecondary(concept: string, primary: string, secondary: string) {
    const key = `${concept}||${primary}||${secondary}`;
    setSelSecondaries((cur) => {
      const next = new Set(cur);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  // 严格 batch import 执行
  async function handleImport() {
    // 严格算总量
    const counts = countSelectedTree(
      trees,
      selConcepts,
      selPrimaries,
      selSecondaries,
    );
    // 严格 + 1 for rootTitle (若不存在)
    const rootExists = files.some(
      (f) => f.title.trim() === rootTitle.trim() && f.kind === "concept",
    );
    const rootToCreate = rootExists ? 0 : 1;
    const total =
      rootToCreate + counts.concepts + counts.primaries + counts.secondaries;

    if (total === 0) {
      setError("严格没选任何项. 严格至少选 1 个 concept + 一级部.");
      setStage("error");
      return;
    }

    setProgress({
      current: 0,
      total,
      currentName: "",
      successCount: 0,
      failCount: 0,
      errors: [],
    });
    setStage("importing");

    let curr = 0;
    let succ = 0;
    let fail = 0;
    const errs: string[] = [];

    const step = async (name: string, kind: string, task: () => Promise<void>) => {
      curr++;
      setProgress((p) => ({
        ...p,
        current: curr,
        currentName: `${kind}: ${name}`,
      }));
      try {
        await task();
        succ++;
      } catch (e) {
        fail++;
        const msg = (e as Error).message || String(e);
        // 严格 skip "已存在" — 严格员工可能已手动创建, 不算错
        if (msg.includes("已存在") || msg.includes("等价 slug 已存在")) {
          succ++;
          fail--;
        } else if (errs.length < 10) {
          errs.push(`${kind} "${name}": ${msg.slice(0, 100)}`);
        }
      }
      setProgress((p) => ({
        ...p,
        successCount: succ,
        failCount: fail,
        errors: [...errs],
      }));
    };

    // 严格 Step 1: 顶级 concept "组织架构" (若不存在)
    if (!rootExists) {
      await step(rootTitle, "顶级 concept", async () => {
        await wikiCreateEntityOrConcept({
          kind: "concept",
          title: rootTitle,
          subtype: "system",
          tags: [],
          related: [],
          body: `# ${rootTitle}\n\n福富组织架构顶级体系. 严格从 xlsx 批量导入.\n\n## 子体系\n\n(P3.5.173 自动填充)`,
        });
      });
    }

    // 严格 Step 2: 3 concept (每 sheet 一个)
    for (const t of trees) {
      if (!selConcepts.has(t.concept)) continue;
      await step(t.concept, "concept", async () => {
        await wikiCreateEntityOrConcept({
          kind: "concept",
          title: t.concept,
          subtype: "system",
          tags: [],
          related: [rootTitle],
          body: `# ${t.concept}\n\n福富组织架构下的 ${t.concept}. 从 xlsx 严格导入.\n\n## 关联概念\n\n- [[${rootTitle}]]`,
        });
      });
    }

    // 严格 Step 3: 一级部 entity
    for (const t of trees) {
      if (!selConcepts.has(t.concept)) continue;
      for (const p of t.primary) {
        const pKey = `${t.concept}||${p.name}`;
        if (!selPrimaries.has(pKey)) continue;
        await step(p.name, "一级部 entity", async () => {
          await wikiCreateEntityOrConcept({
            kind: "entity",
            title: p.name,
            subtype: "org",
            tags: [],
            related: [t.concept],
            body: `# ${p.name}\n\n福富 ${t.concept} 下辖一级部.\n\n## 关联概念\n\n- [[${t.concept}]]`,
          });
        });
      }
    }

    // 严格 Step 4: 二级部 entity
    for (const t of trees) {
      if (!selConcepts.has(t.concept)) continue;
      for (const p of t.primary) {
        const pKey = `${t.concept}||${p.name}`;
        if (!selPrimaries.has(pKey)) continue;
        for (const s of p.secondary) {
          const sKey = `${t.concept}||${p.name}||${s}`;
          if (!selSecondaries.has(sKey)) continue;
          await step(s, "二级部 entity", async () => {
            await wikiCreateEntityOrConcept({
              kind: "entity",
              title: s,
              subtype: "org",
              tags: [],
              related: [p.name],
              body: `# ${s}\n\n福富 ${p.name} 下辖二级部.\n\n## 关联概念\n\n- [[${p.name}]]`,
            });
          });
        }
      }
    }

    // 严格 done
    await loadFiles();
    onImported();
    setStage("done");
  }

  // 严格 render
  return (
    <ModalShell onClose={onClose}>
      {/* 关闭按钮 */}
      <button
        onClick={onClose}
        onMouseEnter={() => setCloseHover(true)}
        onMouseLeave={() => setCloseHover(false)}
        title="关闭"
        style={{
          position: "absolute",
          top: 16,
          right: 16,
          width: 22,
          height: 22,
          borderRadius: "50%",
          border: "none",
          background: closeHover ? T.closeBgHover : T.closeBg,
          color: T.textSecondary,
          fontSize: 11,
          cursor: "pointer",
          lineHeight: 1,
          padding: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <svg width="9" height="9" viewBox="0 0 9 9" fill="none">
          <path
            d="M1 1 L8 8 M8 1 L1 8"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
      </button>

      {/* 视觉锚 — cyan 圆 + 表格图标 */}
      <div
        style={{
          width: 56,
          height: 56,
          borderRadius: 14,
          background: `linear-gradient(135deg, ${T.cyan}, ${T.cyanHover})`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          marginBottom: 18,
          boxShadow: `0 6px 16px ${T.cyan}40`,
        }}
      >
        <svg
          width="28"
          height="28"
          viewBox="0 0 24 24"
          fill="none"
          stroke="white"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <line x1="9" y1="3" x2="9" y2="21" />
          <line x1="15" y1="3" x2="15" y2="21" />
          <line x1="3" y1="9" x2="21" y2="9" />
          <line x1="3" y1="15" x2="21" y2="15" />
        </svg>
      </div>

      <h3
        style={{
          margin: 0,
          fontSize: 19,
          fontWeight: 600,
          color: T.text,
          letterSpacing: "-0.015em",
          fontFamily: T.systemFont,
        }}
      >
        从 xlsx 批量导入组织架构
      </h3>
      <p
        style={{
          margin: "6px 0 0",
          fontSize: 13,
          color: T.textSecondary,
          lineHeight: 1.5,
          fontFamily: T.systemFont,
        }}
      >
        选一份组织架构 xlsx, 自动抽出**概念** (体系) + **实体** (部门) 树, 建 wiki
        关联.
      </p>

      {/* Stage: pick */}
      {stage === "pick" && (
        <div style={{ marginTop: 22 }}>
          <div
            style={{
              padding: "40px 20px",
              border: `2px dashed ${T.border}`,
              borderRadius: 12,
              textAlign: "center",
              background: T.bgInput,
            }}
          >
            <div style={{ fontSize: 32, marginBottom: 8 }}>📊</div>
            <label
              htmlFor="xlsx-file-input"
              style={{
                display: "inline-block",
                padding: "9px 22px",
                background: T.cyan,
                color: "white",
                borderRadius: 8,
                cursor: "pointer",
                fontSize: 14,
                fontWeight: 600,
                fontFamily: T.systemFont,
                boxShadow: `0 2px 6px ${T.cyan}40`,
              }}
            >
              选 xlsx 文件
            </label>
            <input
              id="xlsx-file-input"
              type="file"
              accept=".xlsx,.xls,.xlsm"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void handleFileSelected(f);
              }}
              style={{ display: "none" }}
            />
            <div
              style={{
                marginTop: 12,
                fontSize: 12,
                color: T.textTertiary,
                fontFamily: T.systemFont,
              }}
            >
              严格 heuristic: 找 header 含 "一级部" 的 sheet, 抽 一级部 → 二级部 树
            </div>
          </div>
        </div>
      )}

      {/* Stage: parsing */}
      {stage === "parsing" && (
        <div
          style={{
            marginTop: 22,
            padding: "40px 20px",
            textAlign: "center",
            color: T.textSecondary,
            fontFamily: T.systemFont,
          }}
        >
          <div style={{ fontSize: 28, marginBottom: 8 }}>⏳</div>
          解析 {fileName} 中…
        </div>
      )}

      {/* Stage: error */}
      {stage === "error" && (
        <div
          style={{
            marginTop: 22,
            padding: 20,
            background: `${T.errorRed}15`,
            border: `1px solid ${T.errorRed}40`,
            borderRadius: 8,
            color: T.errorRed,
            fontSize: 13,
            fontFamily: T.systemFont,
            lineHeight: 1.5,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 6 }}>❌ 出错</div>
          <div>{error}</div>
        </div>
      )}

      {/* Stage: preview */}
      {stage === "preview" && (
        <PreviewTree
          trees={trees}
          selConcepts={selConcepts}
          selPrimaries={selPrimaries}
          selSecondaries={selSecondaries}
          toggleConcept={toggleConcept}
          togglePrimary={togglePrimary}
          toggleSecondary={toggleSecondary}
          rootTitle={rootTitle}
          setRootTitle={setRootTitle}
        />
      )}

      {/* Stage: importing */}
      {stage === "importing" && (
        <ImportProgressView progress={progress} />
      )}

      {/* Stage: done */}
      {stage === "done" && (
        <div
          style={{
            marginTop: 22,
            padding: "30px 20px",
            textAlign: "center",
            fontFamily: T.systemFont,
          }}
        >
          <div
            style={{
              fontSize: 32,
              marginBottom: 8,
              color: T.cyan,
            }}
          >
            ✓
          </div>
          <div
            style={{
              fontSize: 15,
              fontWeight: 600,
              color: T.text,
              marginBottom: 6,
            }}
          >
            批量导入完成
          </div>
          <div style={{ fontSize: 13, color: T.textSecondary }}>
            成功 {progress.successCount} 项 / 失败 {progress.failCount} 项
          </div>
          {progress.errors.length > 0 && (
            <details
              style={{
                marginTop: 12,
                textAlign: "left",
                fontSize: 11,
                color: T.textTertiary,
              }}
            >
              <summary style={{ cursor: "pointer" }}>失败详情</summary>
              <ul style={{ marginTop: 6, paddingLeft: 20 }}>
                {progress.errors.map((err, i) => (
                  <li key={i}>{err}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {/* 按钮 bar */}
      {(stage === "preview" || stage === "done" || stage === "error") && (
        <div
          style={{
            display: "flex",
            gap: 8,
            marginTop: 20,
            paddingTop: 16,
            borderTop: `1px solid ${T.border}`,
            alignItems: "center",
          }}
        >
          <button
            onClick={onClose}
            style={{
              padding: "9px 18px",
              border: "none",
              borderRadius: 8,
              background: "transparent",
              color: T.textSecondary,
              fontSize: 14,
              fontFamily: T.systemFont,
              cursor: "pointer",
            }}
          >
            {stage === "done" ? "完成" : "取消"}
          </button>
          <div style={{ flex: 1 }} />
          {stage === "preview" && (
            <>
              <SelectedCount
                trees={trees}
                selConcepts={selConcepts}
                selPrimaries={selPrimaries}
                selSecondaries={selSecondaries}
              />
              <button
                onClick={handleImport}
                onMouseEnter={() => setApplyHover(true)}
                onMouseLeave={() => setApplyHover(false)}
                style={{
                  padding: "9px 22px",
                  border: "none",
                  borderRadius: 8,
                  background: applyHover ? T.cyanHover : T.cyan,
                  color: "white",
                  fontSize: 14,
                  fontWeight: 600,
                  fontFamily: T.systemFont,
                  cursor: "pointer",
                  boxShadow: `0 2px 6px ${T.cyan}40`,
                }}
              >
                开始导入 →
              </button>
            </>
          )}
        </div>
      )}
    </ModalShell>
  );
}

// ─── Sub-component: PreviewTree ──────────────────────────────

interface PreviewTreeProps {
  trees: XlsxSheetTree[];
  selConcepts: Set<string>;
  selPrimaries: Set<string>;
  selSecondaries: Set<string>;
  toggleConcept: (name: string) => void;
  togglePrimary: (concept: string, primary: string) => void;
  toggleSecondary: (concept: string, primary: string, secondary: string) => void;
  rootTitle: string;
  setRootTitle: (v: string) => void;
}

function PreviewTree({
  trees,
  selConcepts,
  selPrimaries,
  selSecondaries,
  toggleConcept,
  togglePrimary,
  toggleSecondary,
  rootTitle,
  setRootTitle,
}: PreviewTreeProps) {
  return (
    <div style={{ marginTop: 22 }}>
      {/* 顶级 concept 名 */}
      <div style={{ marginBottom: 12 }}>
        <label
          style={{
            fontSize: 12,
            color: T.textSecondary,
            display: "block",
            marginBottom: 4,
            fontFamily: T.systemFont,
          }}
        >
          顶级体系名 (会自动创建, 若不存在)
        </label>
        <input
          type="text"
          value={rootTitle}
          onChange={(e) => setRootTitle(e.target.value)}
          style={{
            width: "100%",
            padding: "8px 12px",
            border: `1px solid ${T.border}`,
            borderRadius: 6,
            background: T.bgInput,
            color: T.text,
            fontSize: 13,
            fontFamily: T.systemFont,
            outline: "none",
            boxSizing: "border-box",
          }}
        />
      </div>

      <div
        style={{
          maxHeight: 340,
          overflowY: "auto",
          border: `1px solid ${T.border}`,
          borderRadius: 8,
          padding: 8,
          fontFamily: T.systemFont,
        }}
      >
        {trees.map((t) => (
          <div key={t.concept} style={{ marginBottom: 10 }}>
            {/* Concept (Sheet) */}
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "6px 4px",
                cursor: "pointer",
                fontWeight: 600,
                fontSize: 13,
                color: T.text,
              }}
            >
              <input
                type="checkbox"
                checked={selConcepts.has(t.concept)}
                onChange={() => toggleConcept(t.concept)}
                style={{ accentColor: T.cyan, cursor: "pointer" }}
              />
              <span style={{ fontSize: 12, color: T.textTertiary }}>📁</span>
              {t.concept}
              <span
                style={{
                  fontSize: 11,
                  color: T.textTertiary,
                  fontWeight: 400,
                }}
              >
                (
                {t.primary.length} 一级 ·{" "}
                {t.primary.reduce((n, p) => n + p.secondary.length, 0)} 二级)
              </span>
            </label>

            {selConcepts.has(t.concept) &&
              t.primary.map((p) => {
                const pKey = `${t.concept}||${p.name}`;
                return (
                  <div
                    key={pKey}
                    style={{ marginLeft: 20, marginBottom: 4 }}
                  >
                    <label
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        padding: "3px 4px",
                        cursor: "pointer",
                        fontSize: 12,
                        color: T.text,
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={selPrimaries.has(pKey)}
                        onChange={() => togglePrimary(t.concept, p.name)}
                        style={{ accentColor: T.cyan, cursor: "pointer" }}
                      />
                      <span style={{ fontSize: 11, color: T.textTertiary }}>
                        🏢
                      </span>
                      {p.name}
                      {p.secondary.length > 0 && (
                        <span
                          style={{
                            fontSize: 10,
                            color: T.textTertiary,
                          }}
                        >
                          ({p.secondary.length})
                        </span>
                      )}
                    </label>

                    {selPrimaries.has(pKey) &&
                      p.secondary.map((s) => {
                        const sKey = `${t.concept}||${p.name}||${s}`;
                        return (
                          <label
                            key={sKey}
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: 6,
                              padding: "2px 4px",
                              marginLeft: 24,
                              cursor: "pointer",
                              fontSize: 11,
                              color: T.textSecondary,
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={selSecondaries.has(sKey)}
                              onChange={() =>
                                toggleSecondary(t.concept, p.name, s)
                              }
                              style={{
                                accentColor: T.cyan,
                                cursor: "pointer",
                              }}
                            />
                            <span style={{ color: T.textTertiary }}>·</span>
                            {s}
                          </label>
                        );
                      })}
                  </div>
                );
              })}
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Sub-component: SelectedCount ──────────────────────────────

function SelectedCount({
  trees,
  selConcepts,
  selPrimaries,
  selSecondaries,
}: {
  trees: XlsxSheetTree[];
  selConcepts: Set<string>;
  selPrimaries: Set<string>;
  selSecondaries: Set<string>;
}) {
  const counts = countSelectedTree(
    trees,
    selConcepts,
    selPrimaries,
    selSecondaries,
  );
  const total = counts.concepts + counts.primaries + counts.secondaries;
  return (
    <div
      style={{
        fontSize: 12,
        color: T.textSecondary,
        fontFamily: T.systemFont,
        marginRight: 8,
      }}
    >
      共 <strong style={{ color: T.text }}>{total}</strong> 项 ({counts.concepts}{" "}
      concept · {counts.primaries} 一级 · {counts.secondaries} 二级)
    </div>
  );
}

// ─── Sub-component: ImportProgressView ──────────────────────

function ImportProgressView({ progress }: { progress: ImportProgress }) {
  const pct =
    progress.total > 0
      ? Math.round((progress.current / progress.total) * 100)
      : 0;
  return (
    <div
      style={{
        marginTop: 22,
        padding: "20px",
        fontFamily: T.systemFont,
      }}
    >
      <div
        style={{
          fontSize: 13,
          color: T.textSecondary,
          marginBottom: 8,
          textAlign: "center",
        }}
      >
        写入中… {progress.current} / {progress.total} ({pct}%)
      </div>
      <div
        style={{
          height: 8,
          background: T.border,
          borderRadius: 4,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: T.cyan,
            transition: "width 200ms ease",
          }}
        />
      </div>
      <div
        style={{
          marginTop: 12,
          fontSize: 11,
          color: T.textTertiary,
          textAlign: "center",
          fontFamily: T.systemFont,
        }}
      >
        {progress.currentName || "准备中…"}
      </div>
      {(progress.successCount > 0 || progress.failCount > 0) && (
        <div
          style={{
            marginTop: 8,
            fontSize: 11,
            color: T.textSecondary,
            textAlign: "center",
          }}
        >
          ✓ {progress.successCount} 成功 · ✗ {progress.failCount} 失败 (skip 继续)
        </div>
      )}
    </div>
  );
}
