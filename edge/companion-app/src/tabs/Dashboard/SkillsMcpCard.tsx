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
 * E7.P1 (6/6): 视觉分层 ship — "团队审定" / "我装的" 2 段, ◆ 锁图标区分.
 * E7.P2 (6/6): 安装对话框 + 卸载 + undo toast.
 *   - `+ 安装 skill` 按钮 → modal 输 URL → installSkillFromUrl (npx subprocess) → reload
 *   - `+ 接入 MCP` 按钮 → modal 输 name/command/args → addMcpServer (改 config.yaml) → reload
 *   - 每个 unprotected 行右侧 hover 显 "卸载" link → inline 二次 confirm → 移 trash + 5s undo
 *   - 加 .install-dialog* / .undo-toast* CSS class 群 (globals.css)
 */

import { useEffect, useMemo, useState } from "react";
import { useInstalledSkillsAndMcp } from "../../hooks/useIdentity";
import type { McpServerEntry, SkillEntry, SkillNamespace } from "../../types/identity";
import StatusDot from "../../components/StatusDot";
import {
  addMcpServer,
  installSkillFromUrl,
  removeMcpServer,
  restoreSkill,
  uninstallSkill,
  type InstallResult,
} from "../../lib/tauri";

type Modal =
  | { kind: "none" }
  | { kind: "install-skill" }
  | { kind: "install-skill-running"; url: string }
  | { kind: "install-skill-result"; result: InstallResult }
  | { kind: "add-mcp" }
  | { kind: "add-mcp-running" }
  | { kind: "uninstall-confirm"; entry: SkillEntry; ns: string }
  | { kind: "remove-mcp-confirm"; mcp: McpServerEntry };

interface UndoState {
  trashPath: string;
  originalPath: string;
  label: string;          // skill name 或 mcp name
  kind: "skill" | "mcp-removed";
  // mcp 删了不走 trash (改 yaml 没法 atomic undo), 这里 originalPath 留空, 走"提示式" undo (再 add 回去, 需员工填)
  mcpInfo?: { name: string; command: string; args: string[] };
  expiresAt: number;
}

export default function SkillsMcpCard() {
  const { skills, mcps, error, reload } = useInstalledSkillsAndMcp();
  const [modal, setModal] = useState<Modal>({ kind: "none" });
  const [undo, setUndo] = useState<UndoState | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);

  // E7.P1: 按 isProtected 拆 2 段 — 团队审定 vs 我装的.
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

  // undo 5 秒倒计时 — 过期 trash 留盘 (后台 cron 清), UI 自动消
  useEffect(() => {
    if (!undo) return;
    const tick = setInterval(() => {
      if (Date.now() >= undo.expiresAt) {
        setUndo(null);
        clearInterval(tick);
      }
    }, 250);
    return () => clearInterval(tick);
  }, [undo]);

  const handleUninstall = async (entry: SkillEntry, ns: string) => {
    setActionErr(null);
    try {
      const r = await uninstallSkill(entry.path);
      setUndo({
        trashPath: r.trashPath,
        originalPath: r.originalPath,
        label: `${ns}/${entry.name}`,
        kind: "skill",
        expiresAt: Date.now() + 5000,
      });
      reload();
    } catch (e) {
      setActionErr(String(e));
    }
    setModal({ kind: "none" });
  };

  const handleRestore = async () => {
    if (!undo) return;
    if (undo.kind === "skill") {
      try {
        await restoreSkill(undo.trashPath, undo.originalPath);
        reload();
      } catch (e) {
        setActionErr(`还原失败: ${e}`);
      }
    } else if (undo.kind === "mcp-removed" && undo.mcpInfo) {
      try {
        await addMcpServer(
          undo.mcpInfo.name,
          undo.mcpInfo.command,
          undo.mcpInfo.args,
        );
        reload();
      } catch (e) {
        setActionErr(`还原 MCP 失败: ${e}`);
      }
    }
    setUndo(null);
  };

  const handleRemoveMcp = async (m: McpServerEntry) => {
    setActionErr(null);
    try {
      await removeMcpServer(m.name);
      // MCP 删走 "提示式 undo" — yaml 没法 trash, 但记下来让员工一键 re-add
      setUndo({
        trashPath: "",
        originalPath: "",
        label: m.name,
        kind: "mcp-removed",
        mcpInfo: { name: m.name, command: m.command, args: m.args },
        expiresAt: Date.now() + 5000,
      });
      reload();
    } catch (e) {
      setActionErr(String(e));
    }
    setModal({ kind: "none" });
  };

  return (
    <div className="dashboard-skills">
      <div className="dashboard-skills__top">
        <div>
          <h3 className="dashboard-skills__title">已装的 skill / MCP</h3>
          <div className="dashboard-skills__sub">
            团队审定 + 内置 + marketplaces 装的 · 排错 / 查"我能用哪些"
          </div>
        </div>
        <div className="dashboard-skills__top-actions">
          <button
            className="approval-banner__btn-link"
            onClick={() => setModal({ kind: "install-skill" })}
          >
            + 安装 skill
          </button>
          <button
            className="approval-banner__btn-link"
            onClick={() => setModal({ kind: "add-mcp" })}
          >
            + 接入 MCP
          </button>
        </div>
      </div>

      {error && <div className="dashboard-skills__err">{error}</div>}
      {actionErr && (
        <div className="dashboard-skills__err">操作失败: {actionErr}</div>
      )}

      {!error && (skills || mcps) && (
        <>
          <div className="dashboard-skills__stats">
            <Stat
              label="Skills"
              value={`${totalSkills} 个 / ${totalNamespaces} 类`}
            />
            <Stat label="MCP" value={`${mcps?.length ?? 0} 个`} />
          </div>

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
                      <McpRow
                        key={m.name}
                        m={m}
                        locked={false}
                        onRemove={() =>
                          setModal({ kind: "remove-mcp-confirm", mcp: m })
                        }
                      />
                    ))}
                  </ul>
                </SubGroup>
              )}
              {openMcps.length === 0 && (
                <div className="dashboard-skills__hint">
                  暂未接入员工 MCP — 用上方 "+ 接入 MCP" 加
                </div>
              )}
            </Section>
          )}

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
                      <NamespaceRow
                        key={ns.namespace}
                        ns={ns}
                        locked={false}
                        onUninstall={(entry) =>
                          setModal({
                            kind: "uninstall-confirm",
                            entry,
                            ns: ns.namespace,
                          })
                        }
                      />
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

      {/* ─── modals ─── */}
      {modal.kind === "install-skill" && (
        <InstallSkillDialog
          onClose={() => setModal({ kind: "none" })}
          onSubmit={async (url) => {
            setModal({ kind: "install-skill-running", url });
            try {
              const r = await installSkillFromUrl(url);
              setModal({ kind: "install-skill-result", result: r });
              if (r.success) reload();
            } catch (e) {
              setModal({
                kind: "install-skill-result",
                result: {
                  success: false,
                  stdout: "",
                  stderr: String(e),
                  exitCode: null,
                },
              });
            }
          }}
        />
      )}
      {modal.kind === "install-skill-running" && (
        <Dialog title="安装中…" onClose={() => { /* 跑中不让关 */ }}>
          <div className="install-dialog__running">
            <span className="install-dialog__spinner" />
            <code>npx -y skills add {modal.url}</code>
          </div>
          <div className="install-dialog__hint">
            首次安装可能耗时 (npm 下载), 请耐心等
          </div>
        </Dialog>
      )}
      {modal.kind === "install-skill-result" && (
        <Dialog
          title={modal.result.success ? "安装成功" : "安装失败"}
          onClose={() => setModal({ kind: "none" })}
        >
          {modal.result.stdout && (
            <details open>
              <summary className="install-dialog__summary">stdout</summary>
              <pre className="install-dialog__output">{modal.result.stdout}</pre>
            </details>
          )}
          {modal.result.stderr && (
            <details open={!modal.result.success}>
              <summary className="install-dialog__summary">stderr</summary>
              <pre
                className={
                  "install-dialog__output " +
                  (modal.result.success
                    ? ""
                    : "install-dialog__output--err")
                }
              >
                {modal.result.stderr}
              </pre>
            </details>
          )}
          <div className="install-dialog__hint">
            exit code: {modal.result.exitCode ?? "(无)"}
          </div>
          <div className="install-dialog__actions">
            <button
              className="approval-banner__btn-primary"
              onClick={() => setModal({ kind: "none" })}
            >
              关闭
            </button>
          </div>
        </Dialog>
      )}
      {modal.kind === "add-mcp" && (
        <AddMcpDialog
          onClose={() => setModal({ kind: "none" })}
          onSubmit={async (name, command, args) => {
            setModal({ kind: "add-mcp-running" });
            try {
              await addMcpServer(name, command, args);
              reload();
              setModal({ kind: "none" });
            } catch (e) {
              setActionErr(String(e));
              setModal({ kind: "none" });
            }
          }}
        />
      )}
      {modal.kind === "add-mcp-running" && (
        <Dialog title="接入中…" onClose={() => { /* 短时间, 不让关 */ }}>
          <div className="install-dialog__running">
            <span className="install-dialog__spinner" />
            <span>写 ~/.hermes/config.yaml…</span>
          </div>
        </Dialog>
      )}
      {modal.kind === "uninstall-confirm" && (
        <Dialog
          title={`卸载 ${modal.ns}/${modal.entry.name} ?`}
          onClose={() => setModal({ kind: "none" })}
        >
          <div className="install-dialog__hint">
            skill 会移到 <code>~/.catfish/.trash/skills/</code>, 5 秒内 toast 可点"撤销"恢复.
          </div>
          <div className="install-dialog__actions">
            <button
              className="approval-banner__btn-link"
              onClick={() => setModal({ kind: "none" })}
            >
              取消
            </button>
            <button
              className="approval-banner__btn-deny"
              onClick={() => void handleUninstall(modal.entry, modal.ns)}
            >
              卸载
            </button>
          </div>
        </Dialog>
      )}
      {modal.kind === "remove-mcp-confirm" && (
        <Dialog
          title={`移除 MCP ${modal.mcp.name} ?`}
          onClose={() => setModal({ kind: "none" })}
        >
          <div className="install-dialog__hint">
            会改 <code>~/.hermes/config.yaml</code> 删 <code>mcp_servers.{modal.mcp.name}</code> 段.
            5 秒内 toast 可点"撤销"重接 (重新写回 config.yaml).
            <br />
            <strong>注意</strong>: hermes daemon 要重启 (右上"专注" → 退出 → 重开 catfish) MCP 才完全脱钩.
          </div>
          <div className="install-dialog__actions">
            <button
              className="approval-banner__btn-link"
              onClick={() => setModal({ kind: "none" })}
            >
              取消
            </button>
            <button
              className="approval-banner__btn-deny"
              onClick={() => void handleRemoveMcp(modal.mcp)}
            >
              移除
            </button>
          </div>
        </Dialog>
      )}

      {/* undo toast — 绝对定位 bottom-right, 5s 自消 */}
      {undo && (
        <div className="undo-toast">
          <span className="undo-toast__icon">↻</span>
          <span>
            {undo.kind === "skill" ? "已卸载" : "已移除 MCP"}{" "}
            <code>{undo.label}</code>
          </span>
          <button
            className="undo-toast__btn"
            onClick={() => void handleRestore()}
          >
            撤销
          </button>
          <UndoCountdown expiresAt={undo.expiresAt} />
        </div>
      )}
    </div>
  );
}

function UndoCountdown({ expiresAt }: { expiresAt: number }) {
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

function NamespaceRow({
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

/* ───────── modal 容器 + 安装/接入对话框 ───────── */

function Dialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  // ESC 关
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="install-dialog__backdrop" onClick={onClose}>
      <div
        className="install-dialog"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="install-dialog__header">
          <h4>{title}</h4>
          <button
            className="install-dialog__close"
            onClick={onClose}
            aria-label="关闭"
          >
            ×
          </button>
        </div>
        <div className="install-dialog__body">{children}</div>
      </div>
    </div>
  );
}

function InstallSkillDialog({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (url: string) => void;
}) {
  const [url, setUrl] = useState("");
  const valid =
    url.startsWith("http://") ||
    url.startsWith("https://") ||
    url.startsWith("github:") ||
    url.startsWith("@");
  return (
    <Dialog title="安装 skill" onClose={onClose}>
      <div className="install-dialog__hint">
        从 GitHub URL 或 npm 包名安装. 走 <code>npx -y skills add</code>{" "}
        命令 (跟 terminal 装一样).
      </div>
      <label className="install-dialog__label">URL / 包名</label>
      <input
        className="install-dialog__input"
        type="text"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="https://github.com/Leonxlnx/taste-skill 或 @vercel/some-skill"
        autoFocus
      />
      {url && !valid && (
        <div className="install-dialog__warn">
          需 http(s):// / github: / @scope/pkg 开头
        </div>
      )}
      <div className="install-dialog__actions">
        <button className="approval-banner__btn-link" onClick={onClose}>
          取消
        </button>
        <button
          className="approval-banner__btn-primary"
          disabled={!valid}
          onClick={() => onSubmit(url)}
        >
          安装
        </button>
      </div>
    </Dialog>
  );
}

function AddMcpDialog({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (name: string, command: string, args: string[]) => void;
}) {
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [argsText, setArgsText] = useState("");
  const nameValid =
    name.length > 0 &&
    !name.startsWith("catfish-") &&
    name !== "catfish-tools";
  const commandValid = command.length > 0;
  const valid = nameValid && commandValid;
  return (
    <Dialog title="接入 MCP" onClose={onClose}>
      <div className="install-dialog__hint">
        写 <code>~/.hermes/config.yaml</code> 加 <code>mcp_servers.{"<name>"}</code> 段.
        hermes daemon 重启后生效.
      </div>
      <label className="install-dialog__label">名字</label>
      <input
        className="install-dialog__input"
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="my-mcp (不能用 catfish-* 前缀)"
        autoFocus
      />
      {name && !nameValid && (
        <div className="install-dialog__warn">
          catfish-* 是核心命名保留, 用别的
        </div>
      )}
      <label className="install-dialog__label">command (stdio 启动命令绝对路径)</label>
      <input
        className="install-dialog__input"
        type="text"
        value={command}
        onChange={(e) => setCommand(e.target.value)}
        placeholder="/usr/local/bin/my-mcp-server"
      />
      <label className="install-dialog__label">args (空格分隔, 可空)</label>
      <input
        className="install-dialog__input"
        type="text"
        value={argsText}
        onChange={(e) => setArgsText(e.target.value)}
        placeholder="--port 9000 --token xxx"
      />
      <div className="install-dialog__actions">
        <button className="approval-banner__btn-link" onClick={onClose}>
          取消
        </button>
        <button
          className="approval-banner__btn-primary"
          disabled={!valid}
          onClick={() =>
            onSubmit(
              name,
              command,
              argsText.split(/\s+/).filter((s) => s.length > 0),
            )
          }
        >
          接入
        </button>
      </div>
    </Dialog>
  );
}
