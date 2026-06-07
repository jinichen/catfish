/** 已装的 skills + MCP servers
 *
 * Skills 按 namespace 折叠：默认显示 namespace + skill 数，
 * 点击展开看每个 skill 的 name/description。
 *
 * 6/2 BL-SKILLS-CARD-SPLIT (鸿波 6/2 凌晨拍 方案 C): 这卡现在**只显内置 / 装的**
 * skill (catfish 仓库 skills/ + ~/.hermes/skills/), 员工自己生成的拆到 MySkillsCard
 * 主卡. 卡标题改 "已装的 skill / MCP", 副标"团队审定 + 装的", 给 power-user 排错
 * 或查"我能用哪些 anthropic skill" 用. hook 从 useSkillsAndMcp → useInstalledSkillsAndMcp.
 *
 * E7.P1 (6/6): 视觉分层 ship — 内"团队审定" / "我装的" 2 段, 锁 icon 区分.
 *   - 团队审定 (catfish 仓库 skills/, isProtected=true): 灰显 + 🔒 锁图标
 *     hover 提示 "由 catfish 仓库管控"
 *   - 我装的 (~/.hermes/skills/, isProtected=false): 正常显, 右侧预留"卸载" slot
 *     (phase 2 接 install_skill_from_url / uninstall_skill subprocess)
 *   - MCP 同模式 (catfish-tools 锁 / 员工接的可删)
 * brand 跟 E5 一致, 走 globals.css `.dashboard-skills*` class 群.
 */

import { useMemo, useState } from "react";
import { useInstalledSkillsAndMcp } from "../../hooks/useIdentity";
import type { SkillNamespace } from "../../types/identity";
import StatusDot from "../../components/StatusDot";

export default function SkillsMcpCard() {
  const { skills, mcps, error } = useInstalledSkillsAndMcp();

  // E7.P1: 按 isProtected 拆 2 段 — 团队审定 vs 我装的.
  // namespace 内可能混 (e.g. productivity 有 catfish-* skill protected + 普通 skill),
  // 所以要按 skill 粒度分流, 不是 namespace 粒度.
  const { protectedNs, openNs } = useMemo(() => {
    if (!skills) return { protectedNs: [], openNs: [] };
    const protectedAcc: SkillNamespace[] = [];
    const openAcc: SkillNamespace[] = [];
    for (const ns of skills) {
      const prot = ns.skills.filter((s) => s.isProtected);
      const open = ns.skills.filter((s) => !s.isProtected);
      if (prot.length > 0) {
        protectedAcc.push({ namespace: ns.namespace, skills: prot });
      }
      if (open.length > 0) {
        openAcc.push({ namespace: ns.namespace, skills: open });
      }
    }
    return { protectedNs: protectedAcc, openNs: openAcc };
  }, [skills]);

  const totalSkills =
    skills?.reduce((sum, ns) => sum + ns.skills.length, 0) ?? 0;
  const totalNamespaces = skills?.length ?? 0;
  const protectedMcps = mcps?.filter((m) => m.isProtected) ?? [];
  const openMcps = mcps?.filter((m) => !m.isProtected) ?? [];

  return (
    <div className="dashboard-skills">
      <h3 className="dashboard-skills__title">已装的 skill / MCP</h3>
      <div className="dashboard-skills__sub">
        团队审定 + 内置 + marketplaces 装的 · 排错 / 查"我能用哪些"
      </div>

      {error && <div className="dashboard-skills__err">{error}</div>}

      {!error && (skills || mcps) && (
        <>
          {/* 数量概览 — 拆 2 列 (审定 / 装的), 跟下方分段呼应 */}
          <div className="dashboard-skills__stats">
            <Stat
              label="Skills"
              value={`${totalSkills} 个 / ${totalNamespaces} 类`}
            />
            <Stat label="MCP" value={`${mcps?.length ?? 0} 个`} />
          </div>

          {/* MCP servers — 拆 2 段 */}
          {(protectedMcps.length > 0 || openMcps.length > 0) && (
            <Section title="MCP servers">
              {protectedMcps.length > 0 && (
                <SubGroup label="核心 · 主链路依赖" locked>
                  <ul className="dashboard-skills__mcp-list">
                    {protectedMcps.map((m) => (
                      <McpRow key={m.name} m={m} locked />
                    ))}
                  </ul>
                </SubGroup>
              )}
              {openMcps.length > 0 && (
                <SubGroup label="我接的">
                  <ul className="dashboard-skills__mcp-list">
                    {openMcps.map((m) => (
                      <McpRow key={m.name} m={m} locked={false} />
                    ))}
                  </ul>
                </SubGroup>
              )}
              {openMcps.length === 0 && (
                <div className="dashboard-skills__hint">
                  暂未接入员工 MCP (phase 2 加 "+ 接入新工具" 按钮)
                </div>
              )}
            </Section>
          )}

          {/* Skills — 拆 2 段 */}
          {(protectedNs.length > 0 || openNs.length > 0) && (
            <Section title="Skills (按 namespace)">
              {protectedNs.length > 0 && (
                <SubGroup
                  label={`团队审定 · ${protectedNs.reduce(
                    (s, n) => s + n.skills.length,
                    0,
                  )} 个`}
                  locked
                >
                  <ul className="dashboard-skills__ns-grid">
                    {protectedNs.map((ns) => (
                      <NamespaceRow key={ns.namespace} ns={ns} locked />
                    ))}
                  </ul>
                </SubGroup>
              )}
              {openNs.length > 0 && (
                <SubGroup
                  label={`我装的 · ${openNs.reduce(
                    (s, n) => s + n.skills.length,
                    0,
                  )} 个`}
                >
                  <ul className="dashboard-skills__ns-grid">
                    {openNs.map((ns) => (
                      <NamespaceRow key={ns.namespace} ns={ns} locked={false} />
                    ))}
                  </ul>
                </SubGroup>
              )}
            </Section>
          )}
        </>
      )}

      {!error && !skills && !mcps && (
        <div className="dashboard-skills__hint">加载中…</div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="dashboard-skills__stat-label">{label}</div>
      <div className="dashboard-skills__stat-value">{value}</div>
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
    <div className="dashboard-skills__section">
      <h4 className="dashboard-skills__section-title">{title}</h4>
      {children}
    </div>
  );
}

/** E7.P1: 2 段分组 sub-header. locked=true 加 🔒 锁标 + muted 提示. */
function SubGroup({
  label,
  locked,
  children,
}: {
  label: string;
  locked?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      className={
        "dashboard-skills__subgroup" +
        (locked ? " dashboard-skills__subgroup--locked" : "")
      }
    >
      <div className="dashboard-skills__subgroup-label">
        {locked && (
          <span
            className="dashboard-skills__lock"
            title="由 catfish 仓库 / 主链路管控, 不可删"
            aria-label="锁定"
          >
            ◆
          </span>
        )}
        {label}
      </div>
      {children}
    </div>
  );
}

function McpRow({
  m,
  locked,
}: {
  m: { name: string; command: string };
  locked: boolean;
}) {
  return (
    <li
      className={
        "dashboard-skills__mcp-item" +
        (locked ? " dashboard-skills__mcp-item--locked" : "")
      }
      title={m.command}
    >
      <StatusDot status="ok" />
      <span className="dashboard-skills__mcp-name">{m.name}</span>
      <span className="dashboard-skills__mcp-cmd">
        {m.command.split("/").slice(-2).join("/")}
      </span>
    </li>
  );
}

function NamespaceRow({
  ns,
  locked,
}: {
  ns: SkillNamespace;
  locked: boolean;
}) {
  const [open, setOpen] = useState(false);
  const toggle = () => setOpen(!open);
  return (
    <li className="dashboard-skills__ns">
      <div
        className={
          "dashboard-skills__ns-header" +
          (open ? " dashboard-skills__ns-header--open" : "") +
          (locked ? " dashboard-skills__ns-header--locked" : "")
        }
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        }}
        role="button"
        tabIndex={0}
        aria-expanded={open}
      >
        <span className="dashboard-skills__ns-caret">▶</span>
        <span className="dashboard-skills__ns-name">{ns.namespace}</span>
        <span className="dashboard-skills__ns-count">{ns.skills.length} 个</span>
      </div>
      {open && (
        <ul className="dashboard-skills__skill-list">
          {ns.skills.map((s) => (
            <li
              key={s.name}
              className="dashboard-skills__skill-item"
              title={s.description}
            >
              <span className="dashboard-skills__skill-name">{s.name}</span>
              {s.version && (
                <span className="dashboard-skills__skill-version">
                  v{s.version}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
