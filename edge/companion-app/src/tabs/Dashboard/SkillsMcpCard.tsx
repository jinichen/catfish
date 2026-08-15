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

import { useEffect, useMemo, useRef, useState } from "react";
import { useInstalledSkillsAndMcp } from "../../hooks/useIdentity";
import type { McpServerEntry, SkillEntry, SkillNamespace } from "../../types/identity";
// 8/15: 本文件原来 971 行, 过了 CLAUDE.md §1 的 800 红线。下面两块是从这里
// 搬出去的**同一批组件**:
//   · Parts    纯展示零件 (倒计时 / 统计 / 分组 / 行), 无 store 无 Tauri 调用
//   · Dialogs  三个对话框, 只收输入不做事 —— 真正调 installSkillFromUrl /
//              addMcpServer 的还是本文件。装外部来源的东西是有风险的动作,
//              "到底发生了什么"要只有一个地方能看全。
import {
  McpRow,
  NamespaceRow,
  Section,
  Stat,
  SubGroup,
  UndoCountdown,
} from "./SkillsMcpCardParts";
import {
  AddMcpDialog,
  Dialog,
  InstallSkillDialog,
} from "./SkillsMcpDialogs";
import {
  addMcpServer,
  installSkillFromUrl,
  installSkillFromZip,
  removeMcpServer,
  restoreSkill,
  uninstallSkill,
  type InstallResult,
} from "../../lib/tauri";

// P3.3.34 (6/12): zip 装入口从 MySkillsCard 挪到这 — 集中 skill 装入口防割裂.
//   状态机: idle → installing → done / error → (用户点 dismiss) idle.
type InstallZipState =
  | { phase: "idle" }
  | { phase: "installing"; filename: string }
  | { phase: "done"; message: string; installedPath: string; warnings: string[] }
  | { phase: "error"; message: string };

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

  // P3.3.34 (6/12): zip 装 state — 从 MySkillsCard 挪过来, 集中入口
  const [installZip, setInstallZip] = useState<InstallZipState>({ phase: "idle" });
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  /** P3.3.34 (6/12): 装外部 skill zip 流程 (P3.3.23 复用):
   *    1. 隐藏 input → 拿 File 对象
   *    2. window.prompt 拿 namespace (默认 external, 可改)
   *    3. arrayBuffer → Uint8Array → number[] 给 Rust
   *    4. 调 installSkillFromZip → 成功 reload + 弹 done 卡; 失败 弹 error 卡 */
  const handleZipPicked = async (file: File) => {
    const nsInput = window.prompt(
      `把 "${file.name}" 装到哪个 namespace? (a-z 0-9 _ -; 留空 = external)`,
      "external",
    );
    if (nsInput === null) return;
    const namespace = nsInput.trim() || "external";
    if (!/^[a-z0-9_-]+$/.test(namespace)) {
      setInstallZip({
        phase: "error",
        message: `namespace 只许 a-z 0-9 _ -, 传了: ${namespace}`,
      });
      return;
    }
    setInstallZip({ phase: "installing", filename: file.name });
    try {
      const buf = await file.arrayBuffer();
      const bytes = Array.from(new Uint8Array(buf));
      const result = await installSkillFromZip(bytes, namespace);
      if (!result.success) {
        setInstallZip({ phase: "error", message: "Rust 返 success=false (上下文不明)" });
        return;
      }
      setInstallZip({
        phase: "done",
        message: `✓ 装好 ${result.filesCount} 个文件 → ${namespace}/`,
        installedPath: result.installedPath,
        warnings: result.warnings,
      });
      reload(); // 装的归"已装技能库", reload 让本卡立刻看到新 skill
    } catch (e) {
      setInstallZip({ phase: "error", message: String(e) });
    }
  };

  const onZipInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) void handleZipPicked(file);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const dismissInstallZip = () => setInstallZip({ phase: "idle" });

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
          {/* P3.3.31 (6/12): 跟 P3.3.24 改 MySkillsCard 同款 polish —
              删 ~/.hermes/skills/ 路径徽章 + 副文案, 给员工看技术细节是噪音.
              hover 标题看 title 解释三个来源. */}
          {/* P3.3.32 (6/12): 删 "(可直接用)" — 我的技能也可直接用, 括号误导
              P3.3.35 (6/12): "已装技能库" → "技能库" — 跟"我的技能"对仗 */}
          <h3
            className="dashboard-skills__title"
            style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}
            title="装好可直接用的 skill — 团队审定 / 内置 / marketplaces 装的 / 装的 zip + URL / 部门 publish. 鲶鱼对话时自动可调."
          >
            📦 技能库
          </h3>
        </div>
        <div className="dashboard-skills__top-actions">
          <button
            className="approval-banner__btn-link"
            onClick={() => setModal({ kind: "install-skill" })}
            title="装 hub URL / github / npm 包 — 走 npx skills add"
          >
            + 装 URL
          </button>
          {/* P3.3.34 (6/12): 装 zip 入口从 MySkillsCard 挪过来集中 */}
          <button
            className="approval-banner__btn-link"
            onClick={() => fileInputRef.current?.click()}
            disabled={installZip.phase === "installing"}
            title="选 zip / .skill 文件 (ClawHub 下载的 / 同事发的 / 自己导出的). 解到 ~/.catfish/skills/external/. 文件白名单 .md/.json/.txt/.yaml, 最大 50MB."
          >
            + 装 zip
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".zip,.skill"
            style={{ display: "none" }}
            onChange={onZipInputChange}
          />
          <button
            className="approval-banner__btn-link"
            onClick={() => setModal({ kind: "add-mcp" })}
          >
            + 接入 MCP
          </button>
        </div>
      </div>

      {/* P3.3.34 (6/12): zip 装状态 3 态 banner — 从 MySkillsCard 挪过来 */}
      {installZip.phase === "installing" && (
        <div
          style={{
            padding: "8px 12px",
            marginBottom: 8,
            borderRadius: 4,
            background: "rgba(74,158,255,0.08)",
            fontSize: 12,
            color: "var(--catfish-text)",
          }}
        >
          ⏳ 装 {installZip.filename} ...
        </div>
      )}
      {installZip.phase === "done" && (
        <div
          style={{
            padding: "8px 12px",
            marginBottom: 8,
            borderRadius: 4,
            background: "rgba(34,197,94,0.08)",
            border: "1px solid rgba(34,197,94,0.3)",
            fontSize: 12,
            color: "var(--catfish-text)",
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ flex: 1 }}>{installZip.message}</span>
            <button
              type="button"
              onClick={dismissInstallZip}
              style={{
                fontSize: 11,
                padding: "2px 8px",
                borderRadius: 3,
                border: "1px solid var(--catfish-border)",
                background: "transparent",
                color: "var(--catfish-text-muted)",
                cursor: "pointer",
              }}
            >
              知道了
            </button>
          </div>
          <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", fontFamily: "monospace" }}>
            {installZip.installedPath}
          </div>
          {installZip.warnings.length > 0 && (
            <div style={{ fontSize: 11, color: "#ca8a04" }}>
              ⚠ 跳了 {installZip.warnings.length} 个非白名单文件: {installZip.warnings.slice(0, 3).join(" · ")}
              {installZip.warnings.length > 3 && ` (+ ${installZip.warnings.length - 3})`}
            </div>
          )}
        </div>
      )}
      {installZip.phase === "error" && (
        <div
          style={{
            padding: "8px 12px",
            marginBottom: 8,
            borderRadius: 4,
            background: "rgba(220,38,38,0.08)",
            border: "1px solid rgba(220,38,38,0.3)",
            fontSize: 12,
            color: "#dc2626",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <span style={{ flex: 1 }}>❌ 装失败: {installZip.message}</span>
          <button
            type="button"
            onClick={dismissInstallZip}
            style={{
              fontSize: 11,
              padding: "2px 8px",
              borderRadius: 3,
              border: "1px solid var(--catfish-border)",
              background: "transparent",
              color: "var(--catfish-text-muted)",
              cursor: "pointer",
            }}
          >
            知道了
          </button>
        </div>
      )}

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

