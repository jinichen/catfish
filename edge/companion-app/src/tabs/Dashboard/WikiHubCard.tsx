/** Wiki Hub 卡 — 中央部门 wiki publish 市场 (P3.3.18 Phase 3a, 6/10).
 *
 * 列出 hub 上所有已 publish wiki 笔记, 按 namespace (dept/finance / dept/sales) 分组.
 * 从 gateway /v1/wiki/documents 拉 (gateway 反代到 :8994, 注入 OIDC 身份).
 *
 * 跟 SkillsHubCard 同模式. 关键差异:
 *   - 端点 /v1/wiki/documents 不是 /v1/hub/skills
 *   - response 字段 {documents, count} 不是 {skills, count}
 *   - 没有 "升级 N 个" 概念 — wiki 没 version, 每次 publish 直接 upsert
 *   - stale 项显灰 + 不让装 (原作者撤回了, body 已清零)
 *   - "安装" 按钮调 catfish_wiki_install (tool_bridge), 装到 ~/.catfish/wiki-shared/
 *
 * Phase 4 polish 加 ~/.catfish/wiki-shared/ 扫描 + 已装状态对比.
 */

import { useEffect, useState } from "react";

import { config } from "../../lib/env";
import { fetchWithAuth } from "../../lib/me";
import {
  toolBridgeCallTool,
  listInstalledWikiShared,
  type InstalledWikiSharedInfo,
} from "../../lib/tauri";
import { useAgentStore } from "../../store/agent";

interface WikiDoc {
  namespace: string;
  file_id: string;
  filename: string;
  title: string;
  kind: string;  // entity | concept | query
  description_preview: string;
  published_by: string;
  published_at: string | null;
  updated_at: string | null;
  size_bytes: number;
  stale_after_unpublish: boolean;
  unpublished_at: string | null;
  unpublished_reason: string | null;
}

interface ListResponse {
  documents: WikiDoc[];
  count: number;
}

const REFRESH_MS = 60_000;

const KIND_LABEL: Record<string, string> = {
  entity: "实体",
  concept: "概念",
  query: "查询",
};

function humanTime(iso?: string | null): string {
  if (!iso) return "";
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return iso.slice(0, 10);
  const diff = (Date.now() - ts) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  return iso.slice(0, 10);
}

export default function WikiHubCard() {
  const agentName = useAgentStore((s) => s.name);
  const [docs, setDocs] = useState<WikiDoc[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [installingKey, setInstallingKey] = useState<string | null>(null);
  const [toast, setToast] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  // P3.3.18 Phase 4 (6/10): 本机已装 wiki-shared 索引 (ns/file_id → InstalledWikiSharedInfo)
  const [installedByKey, setInstalledByKey] = useState<Map<string, InstalledWikiSharedInfo>>(new Map());

  const refresh = async () => {
    try {
      // P3.3.37 (6/12 鸿波 audit): backendUrl 切 8642 hermes proxy 对 /v1/wiki/*
      //   返 500. 用 gatewayUrl 永远 8999 直连 (P3.3.37 isGatewayDirectPath 白名单
      //   已让 fetchWithAuth 走 OAuth path).
      const url = `${config.gatewayUrl}/v1/wiki/documents`;
      // 并发拉 hub + 本机已装. 本机失败不阻塞.
      const [hubRes, installedResult] = await Promise.allSettled([
        fetchWithAuth(url),
        listInstalledWikiShared(),
      ]);

      // 本机已装
      if (installedResult.status === "fulfilled") {
        const m = new Map<string, InstalledWikiSharedInfo>();
        for (const w of installedResult.value) {
          m.set(`${w.namespace}/${w.fileId}`, w);
        }
        setInstalledByKey(m);
      } else {
        console.warn("[WikiHubCard] listInstalledWikiShared 失败:", installedResult.reason);
        setInstalledByKey(new Map());
      }

      // hub
      if (hubRes.status === "rejected") {
        // P3.3.36 (6/12): 翻 Tauri webview "Load failed" 原始英文 → 中文友好
        setError(friendlyWikiHubError(hubRes.reason));
        return;
      }
      const res = hubRes.value;
      if (!res.ok) {
        if (res.status === 502) {
          setError("中央 wiki-hub 服务未就绪 (gateway :8994 反代未启动)");
        } else if (res.status === 401) {
          setError("鉴权失败 — 请重新登录");
        } else {
          setError(`HTTP ${res.status}`);
        }
        return;
      }
      const data = (await res.json()) as ListResponse;
      setDocs(data.documents || []);
      setError(null);
    } catch (e) {
      setError(friendlyWikiHubError(e));
    } finally {
      setLoading(false);
    }
  };

  /** P3.3.36 文案翻译, P3.3.37 真 root cause 修后罕见触发.
   *  真因是 /v1/wiki/* 走 hermes proxy 500, P3.3.37 加白名单直连 gateway 修好.
   *  helper 留作 safety net — 真网络挂 / VPN 异常时给友好 message. */
  function friendlyWikiHubError(raw: unknown): string {
    const msg = raw instanceof Error ? raw.message : String(raw);
    if (/load failed|failed to fetch|networkerror/i.test(msg)) {
      return "网络层挂了 (检查 LLM Gateway 是否在跑 / VPN / 防火墙). 真挂可看 console 详细错";
    }
    return msg;
  }

  useEffect(() => {
    void refresh();
    const t = window.setInterval(() => void refresh(), REFRESH_MS);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 3500);
    return () => window.clearTimeout(t);
  }, [toast]);

  /** 点"安装" → catfish_wiki_install via tool_bridge. */
  const handleInstall = async (doc: WikiDoc) => {
    const key = `${doc.namespace}/${doc.file_id}`;
    if (installingKey) return;
    if (doc.stale_after_unpublish) {
      setToast({ kind: "err", text: "原作者已撤回, body 已清零, 无法安装" });
      return;
    }
    setInstallingKey(key);
    try {
      const res = await toolBridgeCallTool("catfish_wiki_install", {
        hub_namespace: doc.namespace,
        hub_file_id: doc.file_id,
      });
      if (res.ok) {
        setToast({
          kind: "ok",
          text: `已装 '${doc.title}' 到 ~/.catfish/wiki-shared/${doc.namespace}/`,
        });
      } else {
        const errMsg = typeof res.result === "string" ? res.result : JSON.stringify(res.result);
        setToast({ kind: "err", text: `安装失败: ${errMsg.slice(0, 120)}` });
      }
    } catch (e) {
      setToast({ kind: "err", text: `安装异常: ${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setInstallingKey(null);
    }
  };

  // 按 namespace 分组
  const grouped: Record<string, WikiDoc[]> = {};
  for (const d of docs) {
    (grouped[d.namespace] ||= []).push(d);
  }
  const namespaces = Object.keys(grouped).sort();
  const staleCount = docs.filter((d) => d.stale_after_unpublish).length;
  // P3.3.18 Phase 4: 本机已装 + hub 已 stale 的 "需要员工注意" 计数
  const installedStaleCount = docs.filter(
    (d) => d.stale_after_unpublish && installedByKey.has(`${d.namespace}/${d.file_id}`),
  ).length;

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        gridColumn: "1 / -1",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          marginBottom: "var(--space-3)",
        }}
      >
        {/* P3.3.38 (6/12): "Wiki Hub" → "知识库" 中文化 */}
        <h3 style={{ margin: 0 }}>
          📚 {agentName} 知识库
          <span
            style={{
              fontSize: 12,
              fontWeight: "normal",
              color: "var(--catfish-text-muted)",
              marginLeft: "var(--space-2)",
            }}
          >
            ({docs.length} wiki / {namespaces.length} 部门)
          </span>
          {staleCount > 0 && (
            <span
              style={{
                fontSize: 11,
                color: "var(--catfish-text-muted)",
                background: "rgba(107,114,128,0.1)",
                padding: "2px 6px",
                borderRadius: 3,
                marginLeft: "var(--space-2)",
              }}
              title={`${staleCount} 条已被原作者撤回 (灰色显示, 无法安装)`}
            >
              ⏸ {staleCount} stale
            </span>
          )}
          {installedStaleCount > 0 && (
            <span
              style={{
                fontSize: 11,
                color: "#dc2626",
                background: "rgba(220,38,38,0.1)",
                padding: "2px 6px",
                borderRadius: 3,
                marginLeft: "var(--space-2)",
                fontWeight: 600,
              }}
              title={`${installedStaleCount} 条你本机有副本, 但原作者已撤回 — 你可以决定是否本机也卸 (manifesto 公理 4)`}
            >
              ⚠ {installedStaleCount} 你装的已撤回
            </span>
          )}
        </h3>
        <span
          style={{
            fontSize: 11,
            color: "var(--catfish-text-muted)",
          }}
        >
          部门共享的知识文档 · 每分钟刷新
        </span>
      </div>

      {toast && (
        <div
          style={{
            fontSize: 12,
            padding: "var(--space-2) var(--space-3)",
            marginBottom: "var(--space-2)",
            borderRadius: "var(--radius-sm)",
            background: toast.kind === "ok" ? "#16a34a20" : "#dc262620",
            border: `1px solid ${toast.kind === "ok" ? "#16a34a" : "#dc2626"}`,
            color: toast.kind === "ok" ? "#16a34a" : "#dc2626",
          }}
        >
          {toast.kind === "ok" ? "✓ " : "⚠ "}{toast.text}
        </div>
      )}

      {loading && (
        <div style={{ color: "var(--catfish-text-muted)", fontSize: 13 }}>加载中…</div>
      )}

      {error && (
        <div
          style={{
            color: "var(--status-err)",
            fontSize: 12,
            background: "var(--catfish-bg-secondary)",
            padding: "var(--space-2)",
            borderRadius: "var(--radius-sm)",
          }}
        >
          {error}
        </div>
      )}

      {!loading && !error && docs.length === 0 && (
        <div
          style={{
            color: "var(--catfish-text-muted)",
            fontSize: 13,
            padding: "var(--space-3)",
            textAlign: "center",
          }}
        >
          部门里还没人 publish 过 wiki. 写完一条 entity / concept / query 后, 让 {agentName} 帮你发到这里, 部门同事能看到.
        </div>
      )}

      {!error && namespaces.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
          {namespaces.map((ns) => (
            <div key={ns}>
              <div
                style={{
                  fontSize: 12,
                  color: "var(--catfish-text-muted)",
                  marginBottom: "var(--space-2)",
                  fontFamily: "var(--font-mono)",
                }}
              >
                {ns}  ·  {grouped[ns].length} wiki
              </div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                  gap: "var(--space-2)",
                }}
              >
                {grouped[ns].map((doc) => {
                  const key = `${doc.namespace}/${doc.file_id}`;
                  const installed = installedByKey.get(key);
                  return (
                    <WikiRow
                      key={key}
                      doc={doc}
                      installed={installed}
                      installing={installingKey === key}
                      onInstall={() => void handleInstall(doc)}
                    />
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function WikiRow({
  doc,
  installed,
  installing,
  onInstall,
}: {
  doc: WikiDoc;
  installed: InstalledWikiSharedInfo | undefined;
  installing: boolean;
  onInstall: () => void;
}) {
  const stale = doc.stale_after_unpublish;
  const isInstalled = installed !== undefined;
  const kindLabel = KIND_LABEL[doc.kind] || doc.kind;
  // P3.3.18 Phase 4: 4 状态分支
  //   - stale + installed: 本机有副本但原作者撤回了 (manifesto 公理 4 — 员工自己决定)
  //   - stale + !installed: hub 已撤回 (灰显, 不让装)
  //   - !stale + installed: 已装, 显 ✓ badge, 不显安装按钮
  //   - !stale + !installed: 可装, 显安装按钮
  return (
    <div
      style={{
        background: stale ? "var(--catfish-bg)" : "var(--catfish-bg-secondary)",
        border: `1px solid var(--catfish-border)`,
        borderRadius: "var(--radius-sm)",
        padding: "var(--space-2) var(--space-3)",
        fontSize: 13,
        opacity: stale ? 0.55 : 1,
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          marginBottom: "var(--space-1)",
        }}
      >
        <span style={{ fontWeight: 500 }}>{doc.title}</span>
        <span
          style={{
            fontSize: 11,
            color: "var(--catfish-text-muted)",
            background: "rgba(0,0,0,0.05)",
            padding: "1px 6px",
            borderRadius: 3,
          }}
        >
          {kindLabel}
        </span>
      </div>
      {stale && isInstalled ? (
        // P3.3.18 Phase 4 关键: 本机有副本, hub 已撤回 → manifesto 公理 4 提示
        <div
          style={{
            color: "#dc2626",
            fontSize: 12,
            marginBottom: "var(--space-1)",
            background: "rgba(220,38,38,0.08)",
            padding: "4px 8px",
            borderRadius: 4,
            border: "1px solid rgba(220,38,38,0.2)",
          }}
          title={doc.unpublished_reason || ""}
        >
          ⚠ 原作者已撤回 ({humanTime(doc.unpublished_at)}). 你本机仍有副本
          (~/.catfish/wiki-shared/{doc.namespace}/{doc.file_id}.md), 自己决定是否卸. (manifesto 公理 4 — 中央不强制清你本机)
        </div>
      ) : stale ? (
        <div
          style={{
            color: "var(--catfish-text-muted)",
            fontSize: 12,
            marginBottom: "var(--space-1)",
            fontStyle: "italic",
          }}
          title={doc.unpublished_reason || ""}
        >
          ⏸ 已被原作者撤回 ({humanTime(doc.unpublished_at)}). body 已清零, 无法安装.
        </div>
      ) : (
        doc.description_preview && (
          <div
            style={{
              color: "var(--catfish-text-muted)",
              fontSize: 12,
              marginBottom: "var(--space-1)",
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
          >
            {doc.description_preview}
          </div>
        )
      )}
      <div
        style={{
          fontSize: 11,
          color: "var(--catfish-text-muted)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span>
          👤 {doc.published_by || "?"} · {humanTime(doc.updated_at || doc.published_at)}
        </span>
        {!stale && isInstalled && (
          <span
            style={{
              fontSize: 11,
              color: "#16a34a",
              background: "rgba(22,163,74,0.1)",
              padding: "1px 6px",
              borderRadius: 3,
            }}
            title={`已装 ${humanTime(installed.installedAt)}`}
          >
            ✓ 已装
          </span>
        )}
        {!stale && !isInstalled && (
          <button
            type="button"
            onClick={onInstall}
            disabled={installing}
            style={{
              fontSize: 11,
              padding: "3px 10px",
              background: "transparent",
              color: "var(--catfish-text)",
              border: "1px solid var(--catfish-border)",
              borderRadius: "var(--radius-sm)",
              cursor: installing ? "wait" : "pointer",
              opacity: installing ? 0.6 : 1,
            }}
            title="装到本机 ~/.catfish/wiki-shared/ (read-only, 知识体系 tab 能看到)"
          >
            {installing ? "安装中…" : "安装"}
          </button>
        )}
      </div>
    </div>
  );
}
