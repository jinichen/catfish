/** SkillsMcpCard 的展示零件 —— 倒计时 / 统计数字 / 分组 / 行。
 *
 * 8/15 从 SkillsMcpCard.tsx 搬出来 (971 行, 过了 CLAUDE.md §1 的 800 红线)。
 *
 * 这六个都是**纯展示**: 没有 store 依赖, 没有 Tauri 调用, 状态只到"这一行展开
 * 没展开"为止。主卡片管数据和动作, 它们只管画。
 *
 * # UndoCountdown 不只是个数字
 *
 * 卸载 skill 之后给一个有倒计时的「撤销」—— 这是"不静默自决"在交互上的落点:
 * 动作立刻生效 (员工看到结果), 但给一段后悔时间。别改成"先弹确认框再执行",
 * 那会把每次卸载都变成一次打断。
 */
import { useEffect, useState } from "react";

import StatusDot from "../../components/StatusDot";
import type { McpServerEntry, SkillEntry, SkillNamespace } from "../../types/identity";

export function UndoCountdown({ expiresAt }: { expiresAt: number }) {
  const [s, setS] = useState(() =>
    Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000)),
  );
  useEffect(() => {
    const tick = setInterval(() => {
      setS(Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000)));
    }, 250);
    return () => clearInterval(tick);
  }, [expiresAt]);
  return <span className="undo-toast__countdown">{s}s</span>;
}

/* ───────── 小组件 ───────── */

export function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="dashboard-skills__stat-label">{label}</div>
      <div className="dashboard-skills__stat-value">{value}</div>
    </div>
  );
}

export function Section({
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

export function SubGroup({
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

export function McpRow({
  m,
  locked,
  onRemove,
}: {
  m: McpServerEntry;
  locked: boolean;
  onRemove?: () => void;
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
      {!locked && onRemove && (
        <button
          className="dashboard-skills__row-action"
          onClick={onRemove}
          title="移除此 MCP"
        >
          移除
        </button>
      )}
    </li>
  );
}

export function NamespaceRow({
  ns,
  locked,
  onUninstall,
}: {
  ns: SkillNamespace;
  locked: boolean;
  onUninstall?: (entry: SkillEntry) => void;
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
              {!locked && onUninstall && (
                <button
                  className="dashboard-skills__row-action"
                  onClick={(e) => {
                    e.stopPropagation();
                    onUninstall(s);
                  }}
                  title="卸载此 skill"
                >
                  卸载
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
