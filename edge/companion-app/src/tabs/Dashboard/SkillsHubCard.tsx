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
 * P1 (5/14 demo 后):
 *   - "装到本机" 按钮 (拉 SKILL.md + 文件, 写 ~/.hermes/skills/)
 *   - 评分 + 订阅数 (后端没字段, P1 加)
 *   - 我发布的 / 我订阅的 tab
 */

import { useEffect, useState } from "react";

import { config } from "../../lib/env";
import { getToken } from "../../lib/me";
import { useAgentStore } from "../../store/agent";

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

  const refresh = async () => {
    try {
      const token = await getToken();
      const url = `${config.gatewayUrl}/v1/hub/skills`;
      const res = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
      });
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

  // 按 namespace 分组
  const grouped: Record<string, SkillSummary[]> = {};
  for (const s of skills) {
    (grouped[s.namespace] ||= []).push(s);
  }
  const namespaces = Object.keys(grouped).sort();

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
                {grouped[ns].map((s) => (
                  <SkillRow key={`${s.namespace}/${s.name}`} skill={s} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SkillRow({ skill }: { skill: SkillSummary }) {
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
        <span
          style={{
            fontSize: 11,
            color: "var(--catfish-text-muted)",
            fontFamily: "var(--font-mono)",
          }}
        >
          v{skill.latest_version}
        </span>
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
        }}
      >
        <span>👤 {skill.published_by || "?"}</span>
        <span>{humanTime(skill.published_at)}</span>
      </div>
    </div>
  );
}
