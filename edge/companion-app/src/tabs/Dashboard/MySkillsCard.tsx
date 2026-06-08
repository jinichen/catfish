/** 6/2 BL-SKILLS-CARD-SPLIT (鸿波 6/2 凌晨拍 方案 C): "🐟 我录的 skill" 主卡.
 *
 * 真物理位置: ~/.catfish/skills/ — RecMode 真生成 + LLM propose_skill 落地处.
 * 跟"已装的 skill / MCP" 次卡 (SkillsMcpCard) 拆开, 主卡上, 次卡下.
 *
 * # 为啥拆 (鸿波 6/2 凌晨洞察)
 *
 * - catfish 5 大差异化卖点之一: "员工自助生成 skill" + "跨员工共享"
 * - 共享对象本应清晰: 员工自己录的 skill (RecMode + propose_skill), 不是 Anthropic
 *   内置的 docx skill, 也不是 marketplaces 装的 apple-mail plugin
 * - 老 SkillsMcpCard 混在一起 (catfish 仓库 8 个 + ~/.hermes/skills/ 96 个), 共享
 *   按钮放哪都没意义 — "共享 Anthropic skill 给同事" 0 价值
 * - 拆开: 主卡只列"我录的", 共享按钮真兑现卖点; 次卡保留"装的" 给 power-user 排错
 *
 * # 空状态友好
 *
 * 多数员工第一次开 Companion 还没录任何 skill, 卡显空状态 + 引导去录: 工作台 →
 * 🎬 录屏 → 操作演示 → catfish 自动生成 skill. 跟 OnboardingWizard 第 7 步串联.
 *
 * # 共享按钮真接 (6/2 BL-SKILLS-PUBLISH-WIRE 鸿波下午 audit)
 *
 * 真状态发现: skills-hub 全栈 5/2 ship, catfish_skill_publish 工具 5/10 ship,
 * tool_bridge_call_tool Tauri 命令 + toolBridgeCallTool TS wrapper 早就在. 唯一缺
 * "员工点按钮" 这步 (5/16 BL-ARCH2 砍 SkillsHubCard 后没人补).
 *
 * 真链路:
 *   按钮点击 → confirm dialog → toolBridgeCallTool("catfish_skill_publish",
 *     {skill_path, namespace}) → tool-bridge Python skill_publish 跑 3 层扫描
 *     (凭据/PII/内网 URL) → 任一命中拒并提示 → 全过 → multipart POST /v1/hub/skills/{ns}
 *     → gateway proxy → skills-hub PG + 文件存. Toast 显结果或错误.
 *
 * 3 层安全扫描完全在 server 端 (skill_publish.py:205-244), 客户端 0 重做.
 */

import { useState } from "react";

import { useMySkills } from "../../hooks/useIdentity";
import { toolBridgeCallTool } from "../../lib/tauri";
import type { SkillEntry, SkillNamespace } from "../../types/identity";

/** BL-MYSKILLS-CARD-UI-CLEAN (2026-06-03): 从 SKILL.md frontmatter description 字段
 * 抽员工友好的简短描述. catfish convention:
 *   ⚠ MUST CALL: ... — BL-LLM-PLAN-WITHOUT-ACT 红线.   ← LLM 指令, 员工不该看
 *
 *   ⭐ 登录 EIS 一站式信息门户 (...)                       ← ⭐ 后是真员工描述
 *
 *   ◉ 由 catfish_freeze_skill 自动凝固 (...)             ← 元信息, 员工不该看
 *
 * 优先提 ⭐ / ⭐️ 后段; 没找到则剥 'MUST CALL' / 'BL-XXX' / '⚠/⚠️' 整行留剩下.
 */
function extractFriendlyDescription(desc: string | undefined | null): string {
  if (!desc) return "";

  // 1. 优先提 ⭐ 后段 (catfish convention)
  for (const marker of ["⭐", "⭐️"]) {
    const idx = desc.indexOf(marker);
    if (idx < 0) continue;
    let rest = desc.slice(idx + marker.length).trim();
    // 截到下一个 emoji 锚点 / 双换行
    let end = rest.length;
    for (const stop of ["\n\n", "◉", "⚠", "⚠️", "✅", "❌", "📌"]) {
      const i = rest.indexOf(stop);
      if (i >= 0 && i < end) end = i;
    }
    return rest.slice(0, end).trim();
  }

  // 2. fallback: 按行剥 LLM 指令 / dev 标记
  const lines = desc.split("\n");
  const cleaned: string[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    // 跳 LLM 指令行
    if (/^[⚠⚠️]?\s*MUST CALL/i.test(trimmed)) continue;
    // 跳 BL-XXX 标记行
    if (/^BL-[A-Z0-9-]+/.test(trimmed)) continue;
    // 跳元信息 (catfish_freeze_skill 自动凝固 ...)
    if (trimmed.includes("自动凝固") || trimmed.includes("freeze_skill")) continue;
    cleaned.push(trimmed);
  }
  return cleaned.join(" ").trim() || desc.slice(0, 80);
}

/** 共享操作的状态机.
 * idle → confirming (员工点 📤, 弹 confirm) → publishing (调 tool) → done/error.
 * 用 sessionId 字段绑定具体哪条 skill 在共享 — 防止快速点多个不同 skill 时弹错对话框. */
interface ShareState {
  phase: "idle" | "confirming" | "publishing" | "done" | "error";
  /** 当前操作的 skill 的 "namespace/name" 标识 — 跨阶段一致, UI 用它定位 */
  skillKey?: string;
  /** Toast 文案 (done/error 阶段显示) */
  message?: string;
  /** 服务器返的 hub_url, done 阶段供员工点击 */
  hubUrl?: string;
}

export default function MySkillsCard() {
  const { skills, error } = useMySkills();
  const totalSkills = skills?.reduce((sum, ns) => sum + ns.skills.length, 0) ?? 0;
  const totalNamespaces = skills?.length ?? 0;
  const hasAny = totalSkills > 0;

  const [share, setShare] = useState<ShareState>({ phase: "idle" });

  /** 6/2 BL-SKILLS-PUBLISH-WIRE: 真调 catfish_skill_publish 工具.
   *
   * 真 result shape (adapter.dispatch_tool 388-394):
   *   ToolCallResult.ok    — dispatch_tool 是否抓住异常 (true = tool 函数没 raise)
   *   ToolCallResult.result — 嵌套真 tool 返 (skill_publish 自己的 dict)
   *
   * skill_publish 返 (skill_publish.py:290 / :180):
   *   成功: {ok: True, namespace, name, version, hub_url, files_count, ...}
   *   失败: {ok: False, error: "凭据扫描命中..."} (3 层安全扫描命中 / 缺 token / gateway 502)
   *
   * 所以**两层 ok 都要查** — dispatch_tool.ok=True 但 tool 内 ok=False = 真业务失败. */
  const handlePublish = async (ns: string, skill: SkillEntry) => {
    const skillKey = `${ns}/${skill.name}`;
    setShare({ phase: "publishing", skillKey });
    try {
      const result = await toolBridgeCallTool(
        "catfish_skill_publish",
        { skill_path: skill.path, namespace: ns },
      );
      // L1: dispatch_tool 自己有没有异常 (网络/挂)
      if (!result.ok) {
        setShare({
          phase: "error",
          skillKey,
          message: result.error || "tool dispatch 失败 (tool-bridge 不可达?)",
        });
        return;
      }
      const inner = result.result as {
        ok?: boolean;
        error?: string;
        hub_url?: string;
        version?: string;
        name?: string;
      } | null;
      // L2: tool 内部业务成功 / 失败 (3 层扫描命中 / 缺 token / gateway 502 等)
      if (!inner || inner.ok === false) {
        setShare({
          phase: "error",
          skillKey,
          message: inner?.error || "tool 内部返失败 (无 error 字段)",
        });
        return;
      }
      setShare({
        phase: "done",
        skillKey,
        message: `✓ 已共享 ${ns}/${inner.name || skill.name}${inner.version ? " v" + inner.version : ""}`,
        hubUrl: inner.hub_url,
      });
    } catch (e) {
      setShare({ phase: "error", skillKey, message: `调 tool 异常: ${e}` });
    }
  };

  const startConfirm = (ns: string, skill: SkillEntry) => {
    setShare({ phase: "confirming", skillKey: `${ns}/${skill.name}` });
  };

  const dismiss = () => setShare({ phase: "idle" });

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
          alignItems: "center",
          gap: "var(--space-2)",
          marginBottom: "var(--space-3)",
          flexWrap: "wrap",
        }}
      >
        <h3 style={{ margin: 0 }}>🎬 我录的技能 (本机)</h3>
        {/* 6/8 BL-SKILL-CARD-DISAMBIG (鸿波): 边界 vs "📦 已装技能库" 卡明示来源. */}
        <span
          style={{
            fontSize: 10,
            padding: "2px 6px",
            background: "var(--catfish-bg)",
            border: "1px solid var(--catfish-border)",
            borderRadius: 4,
            color: "var(--catfish-text-muted)",
            fontFamily: "monospace",
          }}
        >
          ~/.catfish/skills/
        </span>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          RecMode 录屏 + LLM propose · 100% 本机 · 想共享自己点 📤
        </span>
        {hasAny && (
          <span
            style={{
              marginLeft: "auto",
              fontSize: 12,
              color: "var(--catfish-text-muted)",
            }}
          >
            {totalSkills} 个 · {totalNamespaces} 类
          </span>
        )}
      </div>

      {error && (
        <div style={{ fontSize: 12, color: "var(--status-err)", marginBottom: 8 }}>
          拉不到本机 skill 列表: {error}
        </div>
      )}

      {!error && skills === null && (
        <div style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>加载中…</div>
      )}

      {!error && skills !== null && !hasAny && (
        <div
          style={{
            fontSize: 13,
            color: "var(--catfish-text-muted)",
            padding: "10px 12px",
            background: "var(--catfish-bg, transparent)",
            border: "1px dashed var(--catfish-border)",
            borderRadius: 4,
          }}
        >
          还没录过 skill. 在<strong>工作台</strong>点{" "}
          <code style={{ fontSize: 11 }}>🎬 录屏</code> 演示一遍, catfish 自动学会
          → 下次说"帮我跑一次"就行.
          <br />
          <span style={{ fontSize: 11, opacity: 0.7 }}>
            (录的内容存你本机 <code>~/.catfish/skills/</code>, 公司服务器一个字看不到.)
          </span>
        </div>
      )}

      {!error && skills !== null && hasAny && (
        <>
          <ul
            style={{
              listStyle: "none",
              padding: 0,
              margin: 0,
            }}
          >
            {skills.map((ns) => (
              <NamespaceBlock
                key={ns.namespace}
                ns={ns}
                share={share}
                onShare={startConfirm}
              />
            ))}
          </ul>

          {/* 6/2 BL-SKILLS-PUBLISH-WIRE: 共享按钮**真接** (audit 完毕 skills-hub
              全栈 5/2 ship, catfish_skill_publish 5/10 ship, tool_bridge_call_tool 在).
              每条 skill 旁边自带 📤 按钮, 不再底部一个全局按钮.
              这里改成提示行 + 错误/成功 toast.  */}
          {share.phase !== "idle" && (
            <div
              style={{
                marginTop: "var(--space-3)",
                paddingTop: "var(--space-3)",
                borderTop: "1px solid var(--catfish-border)",
              }}
            >
              <ShareStatusBar share={share} onDismiss={dismiss} onPublish={(ns, s) => void handlePublish(ns, s)} skills={skills} />
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** 6/2 BL-SKILLS-PUBLISH-WIRE: 共享状态条 — 1 个组件覆盖 4 个状态 (confirming/
 * publishing/done/error). 跟独立 dialog 不同, 这里 inline 在卡底, 让员工保留 skill
 * 列表视觉上下文, 不弹窗打断. */
function ShareStatusBar({
  share,
  onDismiss,
  onPublish,
  skills,
}: {
  share: ShareState;
  onDismiss: () => void;
  onPublish: (ns: string, skill: SkillEntry) => void;
  skills: SkillNamespace[];
}) {
  // 通过 share.skillKey 反查 namespace + skill
  const found = (() => {
    if (!share.skillKey) return null;
    const [ns, name] = share.skillKey.split("/", 2);
    const nsObj = skills.find((s) => s.namespace === ns);
    const skill = nsObj?.skills.find((s) => s.name === name);
    if (!nsObj || !skill) return null;
    return { ns, skill };
  })();

  if (!found) return null;
  const { ns, skill } = found;

  if (share.phase === "confirming") {
    return (
      <div style={{ fontSize: 12, color: "var(--catfish-text)" }}>
        共享 <code style={{ fontFamily: "var(--font-mono)" }}>{ns}/{skill.name}</code>
        {skill.version && <span style={{ opacity: 0.6 }}> v{skill.version}</span>}
        {" "}到中央 hub? 同事在 catfish-web /skills 看得到.
        <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
          <button
            type="button"
            onClick={() => onPublish(ns, skill)}
            style={{
              background: "var(--catfish-cyan, #38b2ac)",
              color: "#fff",
              border: "none",
              borderRadius: 4,
              padding: "4px 12px",
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            确认共享
          </button>
          <button
            type="button"
            onClick={onDismiss}
            style={{
              background: "transparent",
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              padding: "4px 12px",
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            取消
          </button>
        </div>
        <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: 6 }}>
          📋 共享前服务器自动跑 3 道扫描: 凭据 (密码/api key) / PII (身份证/手机号) / 内网 URL.
          任一命中拒并提示改法.
        </div>
      </div>
    );
  }

  if (share.phase === "publishing") {
    return (
      <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>
        正在共享 <code style={{ fontFamily: "var(--font-mono)" }}>{ns}/{skill.name}</code>… (3 道安全扫描 + 上传, 通常几秒)
      </div>
    );
  }

  if (share.phase === "done") {
    return (
      <div style={{ fontSize: 12 }}>
        <span style={{ color: "var(--catfish-cyan, #38b2ac)" }}>{share.message}</span>
        {share.hubUrl && (
          <span style={{ marginLeft: 8, fontSize: 11, color: "var(--catfish-text-muted)" }}>
            · 链接: <code style={{ fontFamily: "var(--font-mono)" }}>{share.hubUrl}</code>
          </span>
        )}
        <button
          type="button"
          onClick={onDismiss}
          style={{
            marginLeft: 12,
            background: "transparent",
            border: "1px solid var(--catfish-border)",
            borderRadius: 4,
            padding: "2px 8px",
            fontSize: 11,
            cursor: "pointer",
          }}
        >
          关闭
        </button>
      </div>
    );
  }

  // error
  return (
    <div style={{ fontSize: 12, color: "var(--status-err, #c93a3a)" }}>
      ✗ 共享失败: {share.message}
      <button
        type="button"
        onClick={onDismiss}
        style={{
          marginLeft: 12,
          background: "transparent",
          border: "1px solid var(--catfish-border)",
          borderRadius: 4,
          padding: "2px 8px",
          fontSize: 11,
          cursor: "pointer",
          color: "var(--catfish-text)",
        }}
      >
        关闭
      </button>
    </div>
  );
}

function NamespaceBlock({
  ns,
  share,
  onShare,
}: {
  ns: SkillNamespace;
  share: ShareState;
  onShare: (ns: string, skill: SkillEntry) => void;
}) {
  return (
    <li
      style={{
        marginBottom: "var(--space-2)",
      }}
    >
      <div
        style={{
          fontSize: 12,
          fontFamily: "var(--font-mono)",
          fontWeight: 600,
          color: "var(--catfish-text)",
          marginBottom: 4,
        }}
      >
        {ns.namespace}{" "}
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)", fontWeight: 400 }}>
          {ns.skills.length} 个
        </span>
      </div>
      <ul
        style={{
          listStyle: "none",
          padding: "0 0 0 var(--space-3)",
          margin: 0,
        }}
      >
        {ns.skills.map((s) => {
          // 6/2 BL-SKILLS-PUBLISH-WIRE: 这条 skill 是不是正在共享流程中
          const myKey = `${ns.namespace}/${s.name}`;
          const isActive = share.skillKey === myKey;
          const isBusy = isActive && (share.phase === "publishing" || share.phase === "confirming");
          // 共享中 (publishing) 或弹了 confirm 让员工先决定 → 禁用本按钮防多点
          const otherBusy = !isActive && (share.phase === "publishing" || share.phase === "confirming");

          // BL-MYSKILLS-CARD-UI-CLEAN: 从 SKILL.md description 提员工友好描述,
          // 剥掉 LLM 指令 (MUST CALL / BL-XXX / 自动凝固).
          const friendlyDesc = extractFriendlyDescription(s.description);
          return (
            <li
              key={s.name}
              title={friendlyDesc || s.description}
              style={{
                fontSize: 12,
                padding: "2px 0",
                color: "var(--catfish-text-muted)",
                display: "flex",
                alignItems: "baseline",
                gap: "var(--space-2)",
              }}
            >
              <span style={{ fontFamily: "var(--font-mono)", color: "var(--catfish-text)" }}>
                {s.name}
              </span>
              {s.version && (
                <span style={{ opacity: 0.6, fontSize: 11 }}>v{s.version}</span>
              )}
              {friendlyDesc && friendlyDesc !== "(no description)" && (
                <span
                  style={{
                    fontSize: 11,
                    opacity: 0.7,
                    flex: 1,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  · {friendlyDesc.length > 60 ? friendlyDesc.slice(0, 60) + "…" : friendlyDesc}
                </span>
              )}
              <button
                type="button"
                onClick={() => onShare(ns.namespace, s)}
                disabled={isBusy || otherBusy}
                title={
                  isBusy
                    ? "正在处理这条..."
                    : otherBusy
                      ? "先处理完上一条共享"
                      : "共享给同事 — 走 catfish_skill_publish 含 3 道安全扫描"
                }
                style={{
                  marginLeft: "auto",
                  background: "transparent",
                  border: "1px solid var(--catfish-border)",
                  borderRadius: 4,
                  padding: "1px 6px",
                  fontSize: 11,
                  cursor: isBusy || otherBusy ? "not-allowed" : "pointer",
                  opacity: isBusy || otherBusy ? 0.4 : 1,
                  color: "var(--catfish-text)",
                  flexShrink: 0,
                }}
              >
                {isBusy && share.phase === "publishing" ? "…" : "📤"}
              </button>
            </li>
          );
        })}
      </ul>
    </li>
  );
}
