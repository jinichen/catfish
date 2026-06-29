/** P3.5.126 (6/26 鸿波 catch "local_search 目录设置 UI 找不到"):
 * Local Search 索引目录 UI — 让员工不用 vim ~/.catfish/search-scope.yaml.
 *
 * 仿 StyleFingerprintCard ScanDirsManager 套路, 但目标 yaml 不同:
 *   - StyleFingerprintCard → ~/.catfish/companion.yaml (style_fingerprint.scan_dirs)
 *   - 本卡 → ~/.catfish/search-scope.yaml (include 顶层段)
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

const scopeGet = () => invoke<ScopeResult>("local_search_scope_get");
const scopeAdd = (path: string) =>
  invoke<ScopeResult>("local_search_scope_add", { path });
const scopeRemove = (path: string) =>
  invoke<ScopeResult>("local_search_scope_remove", { path });
const statsGet = () => invoke<LocalSearchStats>("local_search_stats");

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

  const onAdd = async () => {
    const p = input.trim();
    if (!p) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await scopeAdd(p);
      setData(r);
      setInput("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onRemove = async (path: string) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await scopeRemove(path);
      setData(r);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
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
          加目录后, 跑一下 <code>catfish-search index</code> 或者重启 Local Search 服务
          让索引生效. 默认扫 ~/Documents / ~/Desktop / ~/Downloads / ~/.catfish/uploads.
        </div>
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
                ⚠️ <strong>还没建索引</strong> — 跑 <code>catfish-search index</code> 或
                启 Local Search watcher 后会自动落库到 <code>{stats.dbPath}</code>
              </span>
            ) : stats.totalFiles === 0 ? (
              <span style={{ color: "var(--catfish-text-muted)" }}>
                📊 索引库存在但 <strong>0 文件</strong> — 可能是 include 目录都被
                exclude 命中, 或文件类型 / 大小不达标
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
