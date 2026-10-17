/**
 * 「协同」页的一起填表区 (10/17, docs/FORM-COLLECT-PLAN.md)。
 *
 * 左: 收到的表 (要我填的), 右: 我发起的表 (看进度)。点一行打开对应的弹层。
 * 只做收集: 每人填自己那份, 回答只回到发起人这台电脑。多人同时改一张表请用公司的在线文档。
 */
import { useState } from "react";

import { useForms } from "../../lib/formsStore";
import FormComposer from "./FormComposer";
import FormFill from "./FormFill";
import FormProgress from "./FormProgress";
import { StateTag, muted } from "./formsUi";
import { Btn, cardStyle, itemStyle } from "./roomLinkUi";

const STATUS_LABEL = { draft: "没发出", open: "收集中", closed: "已结束" } as const;

export default function FormsPanel() {
  const { incoming, outgoing, loaded, syncError } = useForms();
  const [composing, setComposing] = useState(false);
  const [filling, setFilling] = useState<string | null>(null);
  const [watching, setWatching] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | undefined>();

  return (
    <div style={{ ...cardStyle, height: "auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600 }}>📋 一起填表</h3>
          <div style={{ ...muted, marginTop: 4 }}>
            发一张表请同事各填一份, 同事的小鲶帮他预填、他本人核对后提交; 回答只回到你这台电脑汇总。中央只转交, 取走即清空。
          </div>
        </div>
        <Btn kind="primary" onClick={() => setComposing(true)}>发起填表</Btn>
      </div>
      {syncError && <span style={muted}>暂时收不到新表: {syncError}</span>}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: "var(--space-3)" }}>
        <section style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <strong style={{ fontSize: 13 }}>收到的表</strong>
          {loaded && incoming.length === 0 && <span style={muted}>还没有人发表给你。</span>}
          {incoming.map((f) => (
            <button key={f.form_id} type="button" onClick={() => setFilling(f.form_id)}
                    style={{ ...itemStyle, textAlign: "left", cursor: "pointer", background: "transparent", color: "inherit", font: "inherit" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <span style={{ fontSize: "var(--text-sm)", fontWeight: 600 }}>{f.title}</span>
                <StateTag state={f.closed && (f.state === "待填" || f.state === "草稿") ? "已结束" : f.state} />
              </div>
              <span style={muted}>
                {f.owner} · {f.fields} 项{f.deadline ? ` · ${f.deadline} 截止` : ""}{f.reminded_at && f.state !== "已提交" ? " · 被催了" : ""}
              </span>
            </button>
          ))}
        </section>
        <section style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <strong style={{ fontSize: 13 }}>我发起的表</strong>
          {loaded && outgoing.length === 0 && <span style={muted}>还没发起过。</span>}
          {outgoing.map((f) => (
            <button key={f.form_id} type="button" onClick={() => { setNotice(undefined); setWatching(f.form_id); }}
                    style={{ ...itemStyle, textAlign: "left", cursor: "pointer", background: "transparent", color: "inherit", font: "inherit" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <span style={{ fontSize: "var(--text-sm)", fontWeight: 600 }}>{f.title}</span>
                <StateTag state={STATUS_LABEL[f.status]} />
              </div>
              <span style={muted}>
                {f.recipients} 人{f.deadline ? ` · ${f.deadline} 截止` : ""}
                {Object.keys(f.progress).length ? ` · ${Object.entries(f.progress).map(([k, v]) => `${k} ${v}`).join(" · ")}` : ""}
              </span>
            </button>
          ))}
        </section>
      </div>

      {composing && <FormComposer onClose={() => setComposing(false)} onCreated={(id, n) => { setComposing(false); setNotice(n); setWatching(id); }} />}
      {filling && <FormFill formId={filling} onClose={() => setFilling(null)} />}
      {watching && <FormProgress formId={watching} initialError={notice} onClose={() => { setWatching(null); setNotice(undefined); }} />}
    </div>
  );
}
