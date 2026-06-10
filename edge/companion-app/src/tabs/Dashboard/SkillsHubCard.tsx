/** Skills Hub 卡 — 中央 skill 市场 (BL-D2 5/10).
 *
 * 列出 hub 上所有已发布 skill, 按 namespace 分组, 标注作者 + 版本 + 发布时间.
 * 从 gateway /v1/hub/skills 拉 (gateway 反代到 :8997, 注入 OIDC 身份).
 *
 * 设计跟 McpRegistryCard 同模式 — 单一 origin (gateway 8999), Bearer = OAuth id_token.
 *
 * P0 范围:
 *   - 列 skill (按 namespace 分组)
 *   - 看每个 skill 详情 (展开看 SKILL.md 头几行)
 *   - "发布到 hub" 按钮 (跳工具说明, P1 加直接 multipart upload)
 *
 * P3.3.16 (6/10): "我装的 / 有升级" 状态 + 一键升级/安装按钮
 *   - mount 时并行拉 hub list + fetchMySkills (~/.catfish/skills/ 本机)
 *   - 按 ns + name 对比版本, 算 status: needs_upgrade / installed / not_installed
 *   - 头部 badge: 显"X 个有升级"
 *   - 每行右侧 button: 一键升级 / 一键安装
 *   - 升级 / 安装走 toolBridgeCallTool("catfish_skill_install", {hub_skill, hub_url, overwrite}),
 *     不走 LLM. 5-15s 装完, toast 反馈
 */

import { useEffect, useState } from "react";

import { config } from "../../lib/env";
// BL-AUTH-DECOUPLE-A5 Phase 2 (5/19): 改 fetchWithAuth — wrapper 按 useHermes
// 路径切 API_SERVER_KEY + X-Catfish-User / OAuth bearer, 这里不再 inline getToken.
import { fetchWithAuth } from "../../lib/me";
import { useAgentStore } from "../../store/agent";
// P3.3.16: 拉本机已装 skill 对比 hub 版本
import { fetchMySkills, toolBridgeCallTool } from "../../lib/tauri";
import type { SkillEntry } from "../../types/identity";

interface SkillSummary {
  namespace: string;
  name: string;
  latest_version: string;
  description?: string;
  published_by?: string;
  published_at?: string;  // ISO
  versions?: string[];
}

interface ListResponse {
  skills: SkillSummary[];
  count: number;
}

/** P3.3.16: hub skill 跟本机已装 skill 对比后的状态. */
type InstallStatus = "needs_upgrade" | "installed" | "not_installed";

interface SkillStatus {
  status: InstallStatus;
  /** installed / needs_upgrade 时, 本机当前版本. */
  installedVersion?: string;
}

/** semver-lite 比较 — 比较 "0.2.1" vs "0.2" vs "0.10.0". 不是 needs_upgrade 就是同版本.
 *  返 true 表示 hubVersion > installedVersion (需升级). */
function hubVersionNewer(hub: string, installed: string): boolean {
  if (hub === installed) return false;
  const parts = (v: string) =>
    v.split(".").map((s) => {
      const n = parseInt(s, 10);
      return Number.isNaN(n) ? 0 : n;
    });
  const a = parts(hub);
  const b = parts(installed);
  const len = Math.max(a.length, b.length);
  for (let i = 0; i < len; i++) {
    const x = a[i] ?? 0;
    const y = b[i] ?? 0;
    if (x > y) return true;
    if (x < y) return false;
  }
  return false;
}

const REFRESH_MS = 60_000;

function humanTime(iso?: string): string {
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

export default function SkillsHubCard() {
  const agentName = useAgentStore((s) => s.name);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  /** P3.3.16: 本机已装 skill 索引 (ns/name → SkillEntry). 用来对比 hub 算 status. */
  const [mySkillsByKey, setMySkillsByKey] = useState<Map<string, SkillEntry>>(new Map());
  /** P3.3.16: install 中的 hub skill key ("ns/name"), 防双击 + 显 spinner. */
  const [installingKey, setInstallingKey] = useState<string | null>(null);
  /** P3.3.16: install 后 toast — 简单实现. */
  const [toast, setToast] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const refresh = async () => {
    // 并发拉 hub + 本机 my skills (本机失败不阻塞 hub 显)
    try {
      const url = `${config.backendUrl}/v1/hub/skills`;
      const [hubRes, myResult] = await Promise.allSettled([
        fetchWithAuth(url),
        fetchMySkills(),
      ]);

      // 本机已装 — 失败时空 map, 全显 not_installed
      if (myResult.status === "fulfilled") {
        const m = new Map<string, SkillEntry>();
        for (const ns of myResult.value) {
          for (const s of ns.skills) {
            m.set(`${ns.namespace}/${s.name}`, s);
          }
        }
        setMySkillsByKey(m);
      } else {
        console.warn("[SkillsHubCard] fetchMySkills 失败:", myResult.reason);
        setMySkillsByKey(new Map());
      }

      // hub 拉
      if (hubRes.status === "rejected") {
        setError(hubRes.reason instanceof Error ? hubRes.reason.message : String(hubRes.reason));
        return;
      }
      const res = hubRes.value;
      if (!res.ok) {
        if (res.status === 502) {
          setError("skills-hub 未启动 (dev: python -m catfish_skills_hub.app, port 8997)");
        } else if (res.status === 401) {
          setError("鉴权失败 — 请重新登录");
        } else {
          setError(`HTTP ${res.status}`);
        }
        return;
      }
      const data = (await res.json()) as ListResponse;
      setSkills(data.skills || []);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    const t = window.setInterval(() => void refresh(), REFRESH_MS);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // P3.3.16: toast 3 秒自消
  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 3500);
    return () => window.clearTimeout(t);
  }, [toast]);

  /** P3.3.16: 算 hub skill 的状态 — needs_upgrade / installed / not_installed. */
  const computeStatus = (s: SkillSummary): SkillStatus => {
    const key = `${s.namespace}/${s.name}`;
    const installed = mySkillsByKey.get(key);
    if (!installed) return { status: "not_installed" };
    const installedVersion = installed.version ?? "0.1.0";
    if (hubVersionNewer(s.latest_version, installedVersion)) {
      return { status: "needs_upgrade", installedVersion };
    }
    return { status: "installed", installedVersion };
  };

  /** P3.3.16: 点"升级"或"安装" — 走 toolBridgeCallTool, 不通过 LLM. */
  const handleInstall = async (s: SkillSummary, isUpgrade: boolean) => {
    const key = `${s.namespace}/${s.name}`;
    if (installingKey) return;
    setInstallingKey(key);
    try {
      const hubUrl = `${config.backendUrl}/v1/hub`;
      const res = await toolBridgeCallTool("catfish_skill_install", {
        hub_skill: `${s.namespace}/${s.name}@${s.latest_version}`,
        hub_url: hubUrl,
        overwrite: isUpgrade,  // 升级要覆盖, 安装不需要
      });
      if (res.ok) {
        setToast({
          kind: "ok",
          text: isUpgrade
            ? `升级成功: ${s.namespace}/${s.name} → v${s.latest_version}`
            : `安装成功: ${s.namespace}/${s.name} v${s.latest_version}`,
        });
        await refresh();  // 拉新版本状态
      } else {
        const errMsg = typeof res.result === "string" ? res.result : JSON.stringify(res.result);
        setToast({ kind: "err", text: `${isUpgrade ? "升级" : "安装"}失败: ${errMsg.slice(0, 120)}` });
      }
    } catch (e) {
      setToast({ kind: "err", text: `${isUpgrade ? "升级" : "安装"}异常: ${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setInstallingKey(null);
    }
  };

  // 按 namespace 分组
  const grouped: Record<string, SkillSummary[]> = {};
  for (const s of skills) {
    (grouped[s.namespace] ||= []).push(s);
  }
  const namespaces = Object.keys(grouped).sort();

  // P3.3.16: 算"有升级"计数, 头部 badge 用
  const upgradeCount = skills.filter(
    (s) => computeStatus(s).status === "needs_upgrade",
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
        <h3 style={{ margin: 0 }}>
          🛠️ {agentName} Skills Hub
          <span
            style={{
              fontSize: 12,
              fontWeight: "normal",
              color: "var(--catfish-text-muted)",
              marginLeft: "var(--space-2)",
            }}
          >
            ({skills.length} skill / {namespaces.length} namespace)
          </span>
          {upgradeCount > 0 && (
            <span
              style={{
                fontSize: 12,
                fontWeight: 600,
                color: "white",
                background: "var(--status-warn, #c2410c)",
                borderRadius: "var(--radius-sm)",
                padding: "2px 8px",
                marginLeft: "var(--space-2)",
              }}
              title={`${upgradeCount} 个已装 skill 在 hub 上有新版本`}
            >
              ⬆ {upgradeCount} 有升级
            </span>
          )}
        </h3>
        <span
          style={{
            fontSize: 11,
            color: "var(--catfish-text-muted)",
          }}
        >
          中央 skill 市场 · 每分钟刷新
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

      {!loading && !error && skills.length === 0 && (
        <div
          style={{
            color: "var(--catfish-text-muted)",
            fontSize: 13,
            padding: "var(--space-3)",
            textAlign: "center",
          }}
        >
          hub 上还没人发布过 skill. 写完一个 skill 后, 让 {agentName} 帮你发布到这里, 全公司能看到.
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
                {ns}/  ·  {grouped[ns].length} skill
              </div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                  gap: "var(--space-2)",
                }}
              >
                {grouped[ns].map((s) => {
                  const st = computeStatus(s);
                  const key = `${s.namespace}/${s.name}`;
                  return (
                    <SkillRow
                      key={key}
                      skill={s}
                      status={st}
                      installing={installingKey === key}
                      onInstall={() => void handleInstall(s, st.status === "needs_upgrade")}
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

function SkillRow({
  skill,
  status,
  installing,
  onInstall,
}: {
  skill: SkillSummary;
  status: SkillStatus;
  installing: boolean;
  onInstall: () => void;
}) {
  // P3.3.16: 按 status 渲染不同 button + 版本显示
  let badge: React.ReactNode = null;
  let actionBtn: React.ReactNode = null;
  if (status.status === "needs_upgrade") {
    badge = (
      <span
        style={{
          fontSize: 11,
          color: "#c2410c",
          background: "rgba(194,65,12,0.1)",
          padding: "1px 6px",
          borderRadius: 3,
          fontWeight: 600,
        }}
        title={`本机 v${status.installedVersion} → hub v${skill.latest_version}`}
      >
        ⬆ v{status.installedVersion} → v{skill.latest_version}
      </span>
    );
    actionBtn = (
      <button
        type="button"
        onClick={onInstall}
        disabled={installing}
        style={{
          fontSize: 11,
          padding: "3px 10px",
          background: "var(--status-warn, #c2410c)",
          color: "white",
          border: "none",
          borderRadius: "var(--radius-sm)",
          cursor: installing ? "wait" : "pointer",
          opacity: installing ? 0.6 : 1,
        }}
        title="升级到 hub 最新版 (老版本会备份到 ~/.catfish/skill-trash/)"
      >
        {installing ? "升级中…" : "一键升级"}
      </button>
    );
  } else if (status.status === "installed") {
    badge = (
      <span
        style={{
          fontSize: 11,
          color: "#16a34a",
          background: "rgba(22,163,74,0.1)",
          padding: "1px 6px",
          borderRadius: 3,
        }}
        title={`已装 v${status.installedVersion}, 跟 hub 同版本`}
      >
        ✓ 已装 v{status.installedVersion}
      </span>
    );
  } else {
    // not_installed
    badge = (
      <span
        style={{
          fontSize: 11,
          color: "var(--catfish-text-muted)",
          fontFamily: "var(--font-mono)",
        }}
      >
        v{skill.latest_version}
      </span>
    );
    actionBtn = (
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
        title="装到本机 ~/.catfish/skills/ (装完后 hermes 重扫到 LLM 能调)"
      >
        {installing ? "安装中…" : "安装"}
      </button>
    );
  }

  return (
    <div
      style={{
        background: "var(--catfish-bg-secondary)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-sm)",
        padding: "var(--space-2) var(--space-3)",
        fontSize: 13,
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
        <span style={{ fontFamily: "var(--font-mono)", fontWeight: 500 }}>
          {skill.name}
        </span>
        {badge}
      </div>
      {skill.description && (
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
          {skill.description}
        </div>
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
          👤 {skill.published_by || "?"} · {humanTime(skill.published_at)}
        </span>
        {actionBtn}
      </div>
    </div>
  );
}
