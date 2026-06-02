/** 已装的 skills + MCP servers
 *
 * Skills 按 namespace 折叠：默认显示 namespace + skill 数，
 * 点击展开看每个 skill 的 name/description。
 *
 * 6/2 BL-SKILLS-CARD-SPLIT (鸿波 6/2 凌晨拍 方案 C): 这卡现在**只显内置 / 装的**
 * skill (catfish 仓库 skills/ + ~/.hermes/skills/), 员工自己生成的拆到 MySkillsCard
 * 主卡. 卡标题改 "已装的 skill / MCP", 副标"团队审定 + 装的", 给 power-user 排错
 * 或查"我能用哪些 anthropic skill" 用. hook 从 useSkillsAndMcp → useInstalledSkillsAndMcp.
 */

import { useState } from "react";
import { useInstalledSkillsAndMcp } from "../../hooks/useIdentity";
import type { SkillNamespace } from "../../types/identity";
import StatusDot from "../../components/StatusDot";

export default function SkillsMcpCard() {
  const { skills, mcps, error } = useInstalledSkillsAndMcp();

  const totalSkills = skills?.reduce((sum, ns) => sum + ns.skills.length, 0) ?? 0;
  const totalNamespaces = skills?.length ?? 0;

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        gridColumn: "1 / -1", // 占整行: skills 列表多, 用宽度比用高度更好读
      }}
    >
      <h3 style={{ marginBottom: "var(--space-1)" }}>已装的 skill / MCP</h3>
      <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginBottom: "var(--space-3)" }}>
        团队审定 + 内置 + marketplaces 装的 · 排错 / 查"我能用哪些"
      </div>

      {error && (
        <div style={{ color: "var(--status-err)", fontSize: 12 }}>{error}</div>
      )}

      {!error && (skills || mcps) && (
        <>
          {/* 数量概览 */}
          <div
            style={{
              display: "flex",
              gap: "var(--space-4)",
              fontSize: 13,
              marginBottom: "var(--space-3)",
              paddingBottom: "var(--space-3)",
              borderBottom: "1px solid var(--catfish-border)",
            }}
          >
            <Stat label="Skills" value={`${totalSkills} 个 / ${totalNamespaces} 类`} />
            <Stat label="MCP" value={`${mcps?.length ?? 0} 个`} />
          </div>

          {/* MCP 列表 */}
          {mcps && mcps.length > 0 && (
            <Section title="MCP servers">
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {mcps.map((m) => (
                  <li
                    key={m.name}
                    style={{
                      display: "flex",
                      gap: "var(--space-2)",
                      alignItems: "center",
                      fontSize: 12,
                      padding: "var(--space-1) 0",
                      fontFamily: "var(--font-mono)",
                    }}
                    title={m.command}
                  >
                    <StatusDot status="ok" />
                    <span style={{ fontWeight: 600 }}>{m.name}</span>
                    <span
                      style={{
                        color: "var(--catfish-text-muted)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {m.command.split("/").slice(-2).join("/")}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {/* Skills 折叠列表 — grid 多列, 利用宽度 */}
          {skills && skills.length > 0 && (
            <Section title="Skills (按 namespace)">
              <ul
                style={{
                  listStyle: "none",
                  padding: 0,
                  margin: 0,
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
                  gap: "var(--space-1) var(--space-3)",
                }}
              >
                {skills.map((ns) => (
                  <NamespaceRow key={ns.namespace} ns={ns} />
                ))}
              </ul>
            </Section>
          )}
        </>
      )}

      {!error && !skills && !mcps && <div>加载中…</div>}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
        {label}
      </div>
      <div style={{ fontWeight: 600, fontFamily: "var(--font-mono)" }}>
        {value}
      </div>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginBottom: "var(--space-3)" }}>
      <h4
        style={{
          fontSize: 11,
          color: "var(--catfish-text-muted)",
          marginBottom: "var(--space-2)",
          textTransform: "uppercase",
          letterSpacing: 0.5,
          fontWeight: 600,
        }}
      >
        {title}
      </h4>
      {children}
    </div>
  );
}

function NamespaceRow({ ns }: { ns: SkillNamespace }) {
  const [open, setOpen] = useState(false);
  return (
    <li style={{ marginBottom: "var(--space-1)" }}>
      <div
        onClick={() => setOpen(!open)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          cursor: "pointer",
          fontSize: 12,
          padding: "2px 0",
        }}
      >
        <span
          style={{
            display: "inline-block",
            width: 10,
            color: "var(--catfish-text-muted)",
            fontSize: 10,
          }}
        >
          {open ? "▼" : "▶"}
        </span>
        <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}>
          {ns.namespace}
        </span>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          {ns.skills.length} 个
        </span>
      </div>
      {open && (
        <ul
          style={{
            listStyle: "none",
            padding: "0 0 0 20px",
            margin: "var(--space-1) 0 0 0",
          }}
        >
          {ns.skills.map((s) => (
            <li
              key={s.name}
              style={{
                fontSize: 11,
                padding: "2px 0",
                color: "var(--catfish-text-muted)",
              }}
              title={s.description}
            >
              <span style={{ fontFamily: "var(--font-mono)", color: "var(--catfish-text)" }}>
                {s.name}
              </span>
              {s.version && (
                <span style={{ marginLeft: 6, opacity: 0.6 }}>v{s.version}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
