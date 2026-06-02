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
 * # 共享按钮 (占位)
 *
 * 后端 (skills-hub 中央上传 + 团队订阅) 是 BL-SKILLS-SHARE 5/27 backlog, 当前
 * 没接. 按钮渲染但 disabled + tooltip "周一接后端". 避免假承诺.
 */

import { useMySkills } from "../../hooks/useIdentity";
import type { SkillNamespace } from "../../types/identity";

export default function MySkillsCard() {
  const { skills, error } = useMySkills();
  const totalSkills = skills?.reduce((sum, ns) => sum + ns.skills.length, 0) ?? 0;
  const totalNamespaces = skills?.length ?? 0;
  const hasAny = totalSkills > 0;

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
        <h3 style={{ margin: 0 }}>🐟 我录的 skill</h3>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          RecMode 录屏 + LLM propose · 100% 本机 · 想共享自己点
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
              <NamespaceBlock key={ns.namespace} ns={ns} />
            ))}
          </ul>

          {/* 共享按钮 — BL-SKILLS-SHARE 5/27 backlog 占位.
              真后端 (skills-hub 中央上传 + 团队订阅) 周一接. 现在 disabled 防假承诺. */}
          <div
            style={{
              marginTop: "var(--space-3)",
              paddingTop: "var(--space-3)",
              borderTop: "1px solid var(--catfish-border)",
              display: "flex",
              gap: "var(--space-2)",
              alignItems: "center",
            }}
          >
            <button
              type="button"
              disabled
              title="共享 skill 给同事 — 后端 (skills-hub 中央) 周一接, 现在按钮占位"
              style={{
                background: "transparent",
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                padding: "4px 12px",
                fontSize: 12,
                cursor: "not-allowed",
                opacity: 0.5,
              }}
            >
              📤 共享给同事
            </button>
            <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
              skills-hub 中央周一接, 现按钮占位
            </span>
          </div>
        </>
      )}
    </div>
  );
}

function NamespaceBlock({ ns }: { ns: SkillNamespace }) {
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
        {ns.skills.map((s) => (
          <li
            key={s.name}
            title={s.description}
            style={{
              fontSize: 12,
              padding: "2px 0",
              color: "var(--catfish-text-muted)",
            }}
          >
            <span style={{ fontFamily: "var(--font-mono)", color: "var(--catfish-text)" }}>
              {s.name}
            </span>
            {s.version && (
              <span style={{ marginLeft: 6, opacity: 0.6, fontSize: 11 }}>v{s.version}</span>
            )}
            {s.description && s.description !== "(no description)" && (
              <span style={{ marginLeft: 8, fontSize: 11, opacity: 0.7 }}>
                · {s.description.length > 60 ? s.description.slice(0, 60) + "…" : s.description}
              </span>
            )}
          </li>
        ))}
      </ul>
    </li>
  );
}
