/** P3.5.126 (6/26 鸿波 catch "local_search 目录设置 UI 找不到"):
 * Local Search 索引目录 UI — 让员工不用 vim ~/.catfish/search-scope.yaml.
 *
 * BL-STYLE-FP-USE-INDEX (7/27): 本卡现在是**唯一**的目录范围入口.
 * 原来还有第二份 —— StyleFingerprintCard 里的 ScanDirsManager 写
 * ~/.catfish/companion.yaml 的 style_fingerprint.scan_dirs. 两套割裂, 员工配了
 * 一处以为都生效, 结果文书风格 5/20 到 7/27 一直抽 0 文档. 那套已整个删掉,
 * 文书风格改成直接查本卡管的索引 (~/.catfish/search.db).
 *
 * BL-SEARCH-SCOPE-MIGRATION (7/27 鸿波定的原则): 索引范围**只有一个真相源** ——
 * search-scope.yaml, 本卡上看得见改得动, 代码里不留隐藏目录. 鲶鱼自己那两个目录
 * (~/.catfish/uploads 上传的附件 / ~/.catfish/output 生成的文档) 也一样是普通条目,
 * 员工不想索引就直接删. 新增系统目录走 config.py 的 MIGRATIONS 表, 一次性追加进
 * 员工的 yaml 实体文件 (行级插入保注释), 之后就是本卡里一条普通目录.
 *
 * 修改完后员工需手动跑 `catfish-search index` 或点 Local Search 重启按钮
 * (watcher : 重启时 reload config).
 */

import { useEffect, useState, useCallback } from "react";

import { invoke } from "@tauri-apps/api/core";

interface ScopeResult {
  include: string[];
  exclude: string[];
  yamlPath: string;
}

// P3.5.127 (6/26 鸿波 catch "怎么知道文件被没被索引?"): 索引状态查询.
// 对应后端 commands/local_search_stats.rs, 复用 Python stats_summary 同款 3 SQL.
interface TypeCount {
  fileType: string;
  count: number;
}
interface LocalSearchStats {
  dbExists: boolean;
  totalFiles: number;
  byType: TypeCount[];
  totalSizeBytes: number;
  lastIndexedAt: number | null;
  dbPath: string;
}

// BL-SEARCH-NO-BOOTSTRAP (7/27 鸿波实盘): watcher 只吃**文件变化事件**,
// 存量文件永远不会自己进索引 —— 鸿波 yaml 里配了 ~/Documents 等四个目录,
// 索引库里各 0 条. 加目录后要主动索引一次, 面板也得有个手动重建的入口.
interface IndexRunResult {
  output: string;
  ok: boolean;
}

const scopeGet = () => invoke<ScopeResult>("local_search_scope_get");
const scopeAdd = (path: string) =>
  invoke<ScopeResult>("local_search_scope_add", { path });
const scopeRemove = (path: string) =>
  invoke<ScopeResult>("local_search_scope_remove", { path });
const statsGet = () => invoke<LocalSearchStats>("local_search_stats");
/** only 给路径 = 只索引那一个根; 不给 = 整库重建 */
const runIndex = (only?: string) =>
  invoke<IndexRunResult>("local_search_index", { only: only ?? null });
/** BL-SEARCH-STALE-SCOPE (7/27): 清掉已删目录 / 已被 exclude 排除的索引数据 */
const runClean = () => invoke<IndexRunResult>("local_search_clean");

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0";
  const mb = bytes / (1024 * 1024);
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  const kb = bytes / 1024;
  return `${kb.toFixed(0)} KB`;
}

function formatIndexedAt(epoch: number | null): string {
  if (epoch === null) return "—";
  const now = Date.now() / 1000;
  const diff = now - epoch;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  return new Date(epoch * 1000).toLocaleDateString();
}

export default function LocalSearchScopeCard() {
  const [data, setData] = useState<ScopeResult | null>(null);
  const [stats, setStats] = useState<LocalSearchStats | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [excludeExpanded, setExcludeExpanded] = useState(false);
  const [typesExpanded, setTypesExpanded] = useState(false);
  /** 正在跑索引时显的那行字 (加完目录自动补建 / 手点重建) */
  const [indexing, setIndexing] = useState<string | null>(null);
  /** 索引跑完后的完整输出 —— 里面有各目录条数和"读不了的目录"提示 */
  const [indexReport, setIndexReport] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const [r, s] = await Promise.all([scopeGet(), statsGet()]);
      setData(r);
      setStats(s);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void reload();
    // P3.5.127: 30s 轮一次 — watcher 增量入库后, 卡里数字自己变. 不密 (查 sqlite
    // 便宜但加倒不必要), 跟 cron 卡同款 30s 节奏.
    const id = setInterval(() => {
      void reload();
    }, 30000);
    return () => clearInterval(id);
  }, [reload]);

  /** 跑索引 + 把输出显出来 + 刷新统计。only 不给就是整库重建。 */
  const doIndex = useCallback(
    async (only: string | undefined, label: string) => {
      setIndexing(label);
      setIndexReport(null);
      try {
        const r = await runIndex(only);
        setIndexReport(r.output || (r.ok ? "完成" : "索引器没有输出"));
        if (!r.ok) setErr("索引没跑成功，看下面输出");
        await reload();
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        setIndexing(null);
      }
    },
    [reload],
  );

  const onAdd = async () => {
    const p = input.trim();
    if (!p) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await scopeAdd(p);
      setData(r);
      setInput("");
      // BL-SEARCH-NO-BOOTSTRAP (7/27): 加进 yaml 只是让 watcher 以后监听它,
      // 目录里**已有**的文件还是搜不到 —— 必须主动索引一次。只索引新加的这个,
      // 不整库重扫。
      await doIndex(p, `正在索引 ${p}...`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onRemove = async (path: string) => {
    setBusy(true);
    setErr(null);
    setIndexReport(null);
    try {
      const r = await scopeRemove(path);
      setData(r);
      // BL-SEARCH-STALE-SCOPE (7/27): 从 yaml 删掉只是"以后不再扫"，
      // 索引库里那个目录的数据还在 —— 搜索照样搜得到、文书风格照样拿它当语料。
      // 员工点"删"的语义就是"别再看这里"，数据得跟着走，不能指望他记得
      // 回头再手动清一次。
      setIndexing(`正在清掉 ${path} 的索引数据...`);
      const c = await runClean();
      setIndexReport(c.output || "已清理");
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setIndexing(null);
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        // P3.5.126.1 (6/26 鸿波 catch "空间利用率太低"): 限高 + 内部滚动, 仿
        // CronJobsCard maxHeight 380 套路. 顶部标题/说明 flexShrink:0, 中间
        // 滚动区 flex:1 + overflowY:auto.
        maxHeight: 380,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        boxSizing: "border-box",
      }}
    >
      {/* 顶部固定区: 标题 + 说明 + 错误 */}
      <div
        style={{
          padding: "var(--space-4) var(--space-4) var(--space-2) var(--space-4)",
          flexShrink: 0,
          borderBottom: "1px solid var(--catfish-border-soft, rgba(0,0,0,0.05))",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: "var(--space-2)",
            marginBottom: 4,
          }}
        >
          <h3 style={{ margin: 0 }}>📂 Local Search 索引目录</h3>
          <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
            鲶鱼能搜哪些目录的文件
          </span>
        </div>
        <div
          style={{
            fontSize: 11,
            color: "var(--catfish-text-muted)",
            lineHeight: 1.5,
          }}
        >
          {/* BL-SEARCH-NO-BOOTSTRAP (7/27): 老文案让员工"自己去跑 catfish-search index" ——
              而那个命令根本不在 PATH 上 (生产走 local-search 自带 venv 的 -m 调用),
              等于让员工干一件他干不成的事. 现在加目录会自动索引, 这里只留重建入口. */}
          加目录后会自动索引一次。改了 exclude / file_types 或想清掉旧数据，点右边"重建索引"。
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6 }}>
          <button
            type="button"
            onClick={() => void doIndex(undefined, "正在重建整个索引...")}
            disabled={!!indexing || busy}
            style={{
              fontSize: 11,
              padding: "3px 10px",
              background: "transparent",
              border: "1px solid var(--catfish-border)",
              borderRadius: 3,
              cursor: indexing || busy ? "default" : "pointer",
              color: "var(--catfish-text-muted)",
            }}
            title="按当前 search-scope.yaml 把所有目录重扫一遍"
          >
            🔄 重建索引
          </button>
          {indexing && (
            <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
              {indexing}
            </span>
          )}
        </div>
        {/* 索引器的原样输出 —— 各目录条数 + 读不了的目录. BL-SEARCH-TCC-SILENT-SKIP:
            以前整棵目录读不了只留一条 debug 日志, 员工只看到总数, 发现不了. */}
        {indexReport && (
          <pre
            style={{
              marginTop: 6,
              padding: "6px 8px",
              background: "var(--catfish-bg, rgba(0,0,0,0.03))",
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              fontSize: 10,
              lineHeight: 1.5,
              maxHeight: 160,
              overflowY: "auto",
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
              color: "var(--catfish-text)",
            }}
          >
            {indexReport}
          </pre>
        )}
        {/* P3.5.127: 索引状态栏 — 一眼看到有没建索引 + 多少文件 */}
        {stats && (
          <div
            style={{
              marginTop: 6,
              padding: "6px 8px",
              background: "var(--catfish-bg, rgba(0,0,0,0.03))",
              borderRadius: 4,
              fontSize: 11,
            }}
          >
            {!stats.dbExists ? (
              <span style={{ color: "var(--catfish-text-muted)" }}>
                ⚠️ <strong>还没建索引</strong> — 点上面"重建索引", 或启 Local Search
                watcher (空库时它会自己先建一次), 落库到 <code>{stats.dbPath}</code>
              </span>
            ) : stats.totalFiles === 0 ? (
              <span style={{ color: "var(--catfish-text-muted)" }}>
                📊 索引库存在但 <strong>0 文件</strong> — 可能是 include 目录都被
                exclude 命中, 或文件类型 / 大小不达标. 点"重建索引"看逐目录报数.
              </span>
            ) : (
              <>
                <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                  <span>
                    📊 <strong>{stats.totalFiles.toLocaleString()}</strong> 个文件
                  </span>
                  <span style={{ color: "var(--catfish-text-muted)" }}>
                    {formatBytes(stats.totalSizeBytes)}
                  </span>
                  <span style={{ color: "var(--catfish-text-muted)" }}>
                    最近入库 {formatIndexedAt(stats.lastIndexedAt)}
                  </span>
                  {stats.byType.length > 0 && (
                    <button
                      type="button"
                      onClick={() => setTypesExpanded((t) => !t)}
                      style={{
                        fontSize: 11,
                        background: "transparent",
                        border: "none",
                        cursor: "pointer",
                        color: "var(--catfish-cyan, #0E5F66)",
                        padding: 0,
                        marginLeft: "auto",
                      }}
                    >
                      {typesExpanded ? "▼" : "▶"} 按类型 ({stats.byType.length})
                    </button>
                  )}
                </div>
                {typesExpanded && stats.byType.length > 0 && (
                  <div
                    style={{
                      marginTop: 4,
                      display: "flex",
                      flexWrap: "wrap",
                      gap: 6,
                      paddingLeft: 4,
                    }}
                  >
                    {stats.byType.map((t) => (
                      <span
                        key={`bt-${t.fileType}`}
                        style={{
                          fontSize: 10,
                          padding: "1px 6px",
                          background: "var(--catfish-bg-elevated, white)",
                          border: "1px solid var(--catfish-border)",
                          borderRadius: 3,
                          color: "var(--catfish-text-muted)",
                        }}
                      >
                        <code>{t.fileType}</code> × {t.count}
                      </span>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        )}
        {err && (
          <div
            style={{
              fontSize: 12,
              color: "var(--status-err, #dc2626)",
              marginTop: 4,
            }}
          >
            ⚠️ {err}
          </div>
        )}
      </div>

      {/* 中间滚动区 — 卡满时只这块滚 */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "var(--space-2) var(--space-4) var(--space-4) var(--space-4)",
        }}
      >
      {data && (
        <>
          {/* include 列表 — 可加可删 */}
          <div
            style={{
              fontSize: 12,
              fontWeight: 500,
              marginBottom: 4,
              marginTop: "var(--space-2)",
            }}
          >
            包含目录 ({data.include.length})
          </div>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 4,
              marginBottom: "var(--space-3)",
            }}
          >
            {data.include.length === 0 && (
              <div
                style={{
                  fontSize: 11,
                  color: "var(--catfish-text-muted)",
                  fontStyle: "italic",
                  padding: "4px 0",
                }}
              >
                yaml include 段空 — Python load_config 会 fallback 到默认目录
              </div>
            )}
            {data.include.map((d) => (
              <div
                key={`inc-${d}`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "2px 0",
                }}
              >
                <code
                  style={{
                    fontSize: 11,
                    flex: 1,
                    color: "var(--catfish-text)",
                  }}
                >
                  {d}
                </code>
                <button
                  type="button"
                  onClick={() => void onRemove(d)}
                  disabled={busy}
                  style={{
                    fontSize: 10,
                    padding: "1px 6px",
                    background: "transparent",
                    border: "1px solid var(--catfish-border)",
                    borderRadius: 3,
                    cursor: busy ? "default" : "pointer",
                    color: "var(--catfish-text-muted)",
                  }}
                  title="从 ~/.catfish/search-scope.yaml include 段删除这条"
                >
                  ✗ 删
                </button>
              </div>
            ))}

            {/* 添加输入 */}
            <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="~/work 或 /Users/your-name/Documents/项目"
                disabled={busy}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    void onAdd();
                  }
                }}
                style={{
                  flex: 1,
                  fontSize: 11,
                  padding: "3px 6px",
                  background: "var(--catfish-bg)",
                  border: "1px solid var(--catfish-border)",
                  borderRadius: 3,
                  color: "var(--catfish-text)",
                }}
              />
              <button
                type="button"
                onClick={() => void onAdd()}
                disabled={busy || !input.trim()}
                style={{
                  fontSize: 11,
                  padding: "3px 10px",
                  background:
                    input.trim() && !busy
                      ? "var(--catfish-cyan, #0E5F66)"
                      : "transparent",
                  border: "1px solid var(--catfish-border)",
                  borderRadius: 3,
                  cursor: input.trim() && !busy ? "pointer" : "default",
                  color:
                    input.trim() && !busy
                      ? "white"
                      : "var(--catfish-text-muted)",
                }}
              >
                + 添加
              </button>
            </div>
          </div>

          {/* exclude 列表 — 只读, 默认折叠 */}
          <div
            style={{
              fontSize: 12,
              cursor: "pointer",
              color: "var(--catfish-text-muted)",
              userSelect: "none",
            }}
            onClick={() => setExcludeExpanded((e) => !e)}
          >
            {excludeExpanded ? "▼" : "▶"} 排除规则 ({data.exclude.length})
            <span style={{ fontSize: 10, marginLeft: 6, opacity: 0.7 }}>
              (只读, 改要 vim {data.yamlPath})
            </span>
          </div>
          {excludeExpanded && (
            <div
              style={{
                marginTop: 4,
                paddingLeft: 12,
                display: "flex",
                flexDirection: "column",
                gap: 2,
              }}
            >
              {data.exclude.map((e) => (
                <code
                  key={`exc-${e}`}
                  style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}
                >
                  {e}
                </code>
              ))}
            </div>
          )}

          <div
            style={{
              fontSize: 10,
              color: "var(--catfish-text-muted)",
              marginTop: "var(--space-3)",
              opacity: 0.7,
            }}
          >
            写到 <code>{data.yamlPath}</code>
          </div>
        </>
      )}
      </div>{/* /滚动区 */}
    </div>
  );
}
