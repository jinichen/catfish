/**
 * 设置 → 技能 →「📦 技能库」默认页 (10/19, 鸿波「这种样式的技能展现方式, 对普通的办公人员来说只能靠猜, 从开始就容易把人吓退」)。
 *
 * 原来直接摊开 hermes 的技能目录 (apple / mlops / smart-home…, catfish:department, MCP SERVERS, bin/python)。
 * 现在默认给办公人员看:
 *   1. 小鲶能帮你做的事 —— 按办公场景分组, 一句白话 + 「试试这样说」(点了带到工作台直接发给小鲶)
 *   2. 部门 / 团队给的技能 —— 中文名 + 什么时候用
 * 原来那张技术清单 (hermes 自带技能、连接的工具服务、安装 / 卸载) 收进底部「高级 · 给 IT 看」, 默认不展开。
 *
 * 场景是手写的对照表 (写得准、有例句); 鲶鱼加了新能力要顺手补一条。
 */
import { useEffect, useMemo, useState } from "react";

import { useInstalledSkillsAndMcp, useMySkills } from "../../hooks/useIdentity";
import { selectExpert } from "../../lib/expertChat";
import { backfillNamesOnce } from "../../lib/tauri_skill_names";
import { useUIStore } from "../../store/ui";
import type { SkillEntry } from "../../types/identity";
import { muted } from "../Collab/formsUi";
import { cardStyle, itemStyle } from "../Collab/roomLinkUi";
import SkillsMcpCard from "./SkillsMcpCard";

interface Scene {
  key: string;
  icon: string;
  title: string;
  what: string;
  examples: string[];
}

export const SCENES: Scene[] = [
  { key: "email", icon: "📧", title: "邮件", what: "搜邮件、读正文和附件、起草回复 (不会替你发)",
    examples: ["帮我找一下这周跟资质有关的邮件", "最近那封报价邮件的附件说了什么"] },
  { key: "docs", icon: "📄", title: "文档表格", what: "读写 Word / Excel / PPT / PDF, 按模板生成报告",
    examples: ["把这份 PDF 的要点整理成一页 Word", "帮我做一份本周工作汇报的 PPT"] },
  { key: "meeting", icon: "🗓", title: "会议日程", what: "会议录音转纪要、建提醒和日程、列待办",
    examples: ["把刚才的会议录音整理成纪要", "明天下午三点提醒我交周报"] },
  { key: "wiki", icon: "📚", title: "知识库", what: "查公司资料和你存过的东西, 好的回答存进知识库",
    examples: ["公司的差旅报销标准是什么", "把刚才这段说明存到知识库"] },
  { key: "web", icon: "🌐", title: "网页操作", what: "登录内网系统、填表、下载文件 —— 你教过一次就会",
    examples: ["登录 EIS 看看有没有待办", "我想教你做一件网页上的事"] },
  { key: "writing", icon: "✍️", title: "写作", what: "周报、汇报、通知, 照你的文风写",
    examples: ["帮我写这周的周报", "起草一份给领导的情况汇报"] },
  { key: "wechat", icon: "💬", title: "微信", what: "重要的事推送到你微信, 查微信聊天记录",
    examples: ["把今天的待办推送到我微信", "找一下上周群里说的开会时间"] },
  { key: "repeat", icon: "🔁", title: "反复做的事", what: "串成工作流, 以后一句话或定时整条跑完 (见「工作流」页)",
    examples: ["每周一帮我把资质相关的邮件理一下, 建个任务"] },
];

/** 技能库里「部门 / 团队给的」: 仓库里团队审定的 (🐟 catfish:*) + 装到本机的部门 / 外部技能; hermes 自带的不算 */
function isTeamNs(ns: string): boolean {
  return ns.startsWith("🐟") || ns === "department" || ns === "external";
}

/** 「说「登录 EIS」「进 EIS」时用」→ 第一句当例句 */
function exampleOf(s: SkillEntry): string | null {
  const m = /^说「([^」]+)」/.exec(s.summary || "");
  return m ? m[1] : null;
}

function Example({ text, onSend }: { text: string; onSend: (t: string) => void }) {
  return (
    <button type="button" onClick={() => onSend(text)} title="带到工作台, 直接发给小鲶"
            style={{ fontSize: 12, padding: "3px 10px", borderRadius: 999, cursor: "pointer", textAlign: "left",
                     border: "1px solid var(--catfish-border)", background: "var(--catfish-bg)", color: "var(--catfish-text)" }}>
      💬 {text}
    </button>
  );
}

export default function SkillsLibraryCard() {
  const { skills: installed, reload } = useInstalledSkillsAndMcp();
  const { skills: mine } = useMySkills();
  const [advanced, setAdvanced] = useState(false);
  const startChat = useUIStore((s) => s.startEmailChat);

  useEffect(() => { void backfillNamesOnce().then((changed) => { if (changed) reload(); }); }, [reload]);

  const team = useMemo(
    () => (installed ?? []).filter((ns) => isTeamNs(ns.namespace)).flatMap((ns) => ns.skills),
    [installed],
  );
  const mineCount = (mine ?? []).reduce((n, ns) => n + ns.skills.length, 0);
  const send = (text: string) => {
    selectExpert(null); // 场景例句是给主小鲶的
    startChat(`scene-${Date.now()}`, text);
  };

  return (
    <div style={{ ...cardStyle, height: "auto" }}>
      <div>
        <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600 }}>📦 小鲶能帮你做的事</h3>
        <div style={{ ...muted, marginTop: 4 }}>
          不用记技能名字, 在工作台直接说就行。点下面的例句试试。
          {installed && ` 部门 / 团队给的技能 ${team.length} 个 · 你教会的 ${mineCount} 个。`}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 10 }}>
        {SCENES.map((sc) => (
          <div key={sc.key} style={{ ...itemStyle, gap: 6 }}>
            <span style={{ fontSize: 14, fontWeight: 600 }}>{sc.icon} {sc.title}</span>
            <span style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>{sc.what}</span>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "flex-start" }}>
              {sc.examples.map((ex) => <Example key={ex} text={ex} onSend={send} />)}
            </div>
          </div>
        ))}
      </div>

      {team.length > 0 && (
        <>
          <strong style={{ fontSize: 13 }}>部门 / 团队给的技能</strong>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 8 }}>
            {team.map((s) => {
              const ex = exampleOf(s);
              return (
                <div key={s.path} style={{ ...itemStyle, gap: 4 }} title={s.name}>
                  <span style={{ fontSize: 13, fontWeight: 600 }}>{s.title || s.name}</span>
                  {s.summary && <span style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>{s.summary}</span>}
                  {ex && <div><Example text={ex} onSend={send} /></div>}
                </div>
              );
            })}
          </div>
        </>
      )}

      <button type="button" onClick={() => setAdvanced(!advanced)}
              style={{ ...muted, background: "none", border: "none", padding: 0, cursor: "pointer", textAlign: "left", fontSize: 12 }}>
        {advanced ? "▾" : "▸"} 高级 · 给 IT 看 (hermes 自带的全部技能、连接的工具服务、安装 / 卸载)
      </button>
      {advanced && <SkillsMcpCard />}
    </div>
  );
}
