/**
 * 「协同」页左上: 请同事帮忙 (11/4 起是「派任务给多位同事」, docs/COLLEAGUE-TASK-PLAN.md 第一步)。
 *
 * 原来 (P50, 9/10) 只能找一个人、对方 10 分钟内要点同意、你这台电脑要一直开着直连对方 ——
 * 换成跟一起填表同一个邮筒: 发出去就不用管, 对方开机再处理, 在他自己的电脑上做完交回。
 * 上: 同事派给我的 (要我动手的排前面); 下: 我发出的 (看进度)。点一行打开对应的弹层。
 */
import { useState } from "react";

import { useColleagueTasks } from "../../lib/colleagueTasksStore";
import { StateTag, muted } from "./formsUi";
import { Btn, cardStyle, itemStyle } from "./roomLinkUi";
import TaskComposer from "./TaskComposer";
import TaskProgress from "./TaskProgress";
import TaskWork, { when } from "./TaskWork";

const STATUS_LABEL = { draft: "没发出", open: "进行中", cancelled: "已取消" } as const;
const ACTIVE = new Set(["待处理", "在做", "待核对", "要重新同意", "没做成", "定时没了"]);

const rowButton = { ...itemStyle, textAlign: "left" as const, cursor: "pointer", background: "transparent", color: "inherit", font: "inherit" };

export default function TasksPanel() {
  const { incoming, outgoing, loaded, syncError } = useColleagueTasks();
  const [composing, setComposing] = useState(false);
  const [working, setWorking] = useState<string | null>(null);
  const [watching, setWatching] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | undefined>();
  const inbox = [...incoming].sort((a, b) => Number(ACTIVE.has(b.state)) - Number(ACTIVE.has(a.state)));

  return (
    <div style={cardStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600 }}>🤝 请同事帮忙</h3>
          <div style={{ ...muted, marginTop: 4 }}>
            一次发给多位同事, 各自决定接不接; 在他自己的电脑上由他的小鲶或本人做完, 核对后交回。工具和要发出去的东西都由对方本人放行,
            结果只回到你这台电脑。中央只转交, 看不到内容。
          </div>
        </div>
        <Btn kind="primary" onClick={() => setComposing(true)}>派任务</Btn>
      </div>
      {syncError && <span style={muted}>暂时收不到同事的任务: {syncError}</span>}

      <section style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <strong style={{ fontSize: 13 }}>同事请我做的</strong>
        {loaded && inbox.length === 0 && <span style={muted}>还没有同事找你帮忙。</span>}
        {inbox.map((t) => (
          <button key={t.task_id} type="button" onClick={() => setWorking(t.task_id)} style={rowButton}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <span style={{ fontSize: "var(--text-sm)", fontWeight: 600 }}>{t.title}</span>
              <StateTag state={t.state} />
            </div>
            <span style={muted}>
              {t.owner}{t.schedule ? ` · ⏰ ${t.schedule}` : t.deadline ? ` · 截止 ${when(t.deadline)}` : ""}
              {t.to_review ? ` · ${t.to_review} 次待核对` : ""}
              {t.late && ACTIVE.has(t.state) ? " · 已过截止" : ""}{t.reminded_at && ACTIVE.has(t.state) ? " · 被催了" : ""}
            </span>
          </button>
        ))}
      </section>

      <section style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <strong style={{ fontSize: 13 }}>我请同事做的</strong>
        {loaded && outgoing.length === 0 && <span style={muted}>还没派过任务。</span>}
        {outgoing.map((t) => (
          <button key={t.task_id} type="button" onClick={() => { setNotice(undefined); setWatching(t.task_id); }} style={rowButton}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <span style={{ fontSize: "var(--text-sm)", fontWeight: 600 }}>{t.title}</span>
              <StateTag state={STATUS_LABEL[t.status]} />
            </div>
            <span style={muted}>
              {t.recipients} 人{t.schedule ? ` · ⏰ ${t.schedule}` : t.deadline ? ` · 截止 ${when(t.deadline)}` : ""}
              {Object.keys(t.progress).length ? ` · ${Object.entries(t.progress).map(([k, v]) => `${k} ${v}`).join(" · ")}` : ""}
            </span>
          </button>
        ))}
      </section>

      {composing && <TaskComposer onClose={() => setComposing(false)}
                                  onCreated={(id, n) => { setComposing(false); setNotice(n); setWatching(id); }} />}
      {working && <TaskWork taskId={working} onClose={() => setWorking(null)} />}
      {watching && <TaskProgress taskId={watching} initialError={notice} onClose={() => { setWatching(null); setNotice(undefined); }} />}
    </div>
  );
}
