/** P3.5.126 (6/26 鸿波 catch "local_search 目录设置 UI 找不到"):
 * Local Search 索引目录 UI — 让员工不用 vim ~/.catfish/search-scope.yaml.
 *
 * 仿 StyleFingerprintCard 真**ScanDirsManager** 套路, 但目标 yaml 不同:
 *   - StyleFingerprintCard → ~/.catfish/companion.yaml (style_fingerprint.scan_dirs)
 *   - 本卡 → ~/.catfish/search-scope.yaml (include 顶层段)
 *
 * 修改完后员工需手动跑 `catfish-search index` 或点 Local Search 重启按钮
 * (watcher 真**:** 重启时 reload config).
 */

import { useEffect, useState, useCallback } from "react";

import { invoke } from "@tauri-apps/api/core";

interface ScopeResult {
  include: string[];
  exclude: string[];
  yamlPath: string;
}

const scopeGet = () => invoke<ScopeResult>("local_search_scope_get");
const scopeAdd = (path: string) =>
  invoke<ScopeResult>("local_search_scope_add", { path });
const scopeRemove = (path: string) =>
  invoke<ScopeResult>("local_search_scope_remove", { path });

export default function LocalSearchScopeCard() {
  const [data, setData] = useState<ScopeResult | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [excludeExpanded, setExcludeExpanded] = useState(false);

  const reload = useCallback(async () => {
    try {
      const r = await scopeGet();
      setData(r);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void reload();
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
        padding: "var(--space-4)",
        boxSizing: "border-box",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: "var(--space-2)",
          marginBottom: "var(--space-2)",
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
          marginBottom: "var(--space-2)",
          lineHeight: 1.5,
        }}
      >
        加目录后, 跑一下 <code>catfish-search index</code> 或者重启 Local Search 服务
        让索引生效. 默认扫 ~/Documents / ~/Desktop / ~/Downloads / ~/.catfish/uploads.
      </div>

      {err && (
        <div
          style={{
            fontSize: 12,
            color: "var(--status-err, #dc2626)",
            marginBottom: "var(--space-2)",
          }}
        >
          ⚠️ {err}
        </div>
      )}

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
                yaml 真**include 段空** — Python load_config 会 fallback 到默认目录
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
    </div>
  );
}
