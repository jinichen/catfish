/** 数据外发记录: 看某一条发了什么 + 防篡改校验 + 保留期设置 (10/25 鸿波).
 *
 * 内容和指纹链在 Rust transparent_log_store.rs; 每日指纹在 lib/egressAnchor.ts。
 */

import * as React from "react";

import { fetchEgressAnchors } from "../../lib/egressAnchor";
import {
  transparentLogInfo,
  transparentLogPayload,
  transparentLogSetRetention,
  transparentLogVerify,
  type EgressAnchor,
  type TransparentLogInfo,
  type TransparentLogPayload,
  type TransparentLogVerifyReport,
} from "../../lib/tauri";

interface ChatMessage {
  role?: string;
  content?: unknown;
  tool_calls?: unknown;
}

/** 模型调用每次都带整段历史 —— 「这次新发的」= 最后一条助手回复之后的消息 (用户的话、工具结果)。
 *  不是对话格式返回 null。 */
export function newPartOfChat(body: unknown): ChatMessage[] | null {
  if (!body || typeof body !== "object") return null;
  const msgs = (body as { messages?: unknown }).messages;
  if (!Array.isArray(msgs) || msgs.length === 0) return null;
  let i = msgs.length - 1;
  while (i >= 0 && (msgs[i] as ChatMessage)?.role !== "assistant") i--;
  const tail = msgs.slice(i + 1) as ChatMessage[];
  return tail.length ? tail : (msgs.slice(-1) as ChatMessage[]);
}

/** 消息内容 → 能读的文字 (多段内容里的图片标成 [图片]) */
export function messageText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((p) => {
        const part = p as { type?: string; text?: string };
        if (part?.type === "text" && typeof part.text === "string") return part.text;
        if (part?.type === "image_url" || part?.type === "input_image") return "[图片]";
        return JSON.stringify(p);
      })
      .join("\n");
  }
  return content == null ? "" : JSON.stringify(content, null, 2);
}

const ROLE_LABEL: Record<string, string> = { user: "你说的", tool: "工具结果", system: "系统提示", assistant: "助手" };

export function PayloadPanel({ id }: { id: number }) {
  const [p, setP] = React.useState<TransparentLogPayload | null>(null);
  const [err, setErr] = React.useState<string | null>(null);
  React.useEffect(() => {
    transparentLogPayload(id).then(setP).catch((e) => setErr(String(e)));
  }, [id]);
  if (err) return <div style={muted}>读不出内容: {err}</div>;
  if (!p) return <div style={muted}>读取中…</div>;
  if (p.text == null) return <div style={muted}>这条没有请求内容 (比如只是打开一个网址)。</div>;
  let parsed: unknown = null;
  if (p.isJson) {
    try {
      parsed = JSON.parse(p.text);
    } catch {
      parsed = null;
    }
  }
  const fresh = newPartOfChat(parsed);
  return (
    <div style={{ padding: "6px 4px", display: "grid", gap: 6 }}>
      <div style={{ fontSize: 11, color: p.verified ? "var(--catfish-cyan)" : "var(--status-err, #d9534f)" }}>
        {p.verified ? "✓ 内容跟指纹对得上, 没被改过" : "✗ 内容跟指纹对不上 —— 这条记录被改过或部分丢失"}
      </div>
      {fresh && (
        <div style={{ display: "grid", gap: 4 }}>
          <div style={{ fontSize: 12, fontWeight: 600 }}>这次新发出去的</div>
          {fresh.map((m, i) => (
            <div key={i} style={box}>
              <div style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>{ROLE_LABEL[m.role ?? ""] ?? m.role}</div>
              <div style={pre}>{messageText(m.content) || (m.tool_calls ? JSON.stringify(m.tool_calls, null, 2) : "")}</div>
            </div>
          ))}
        </div>
      )}
      <details open={!fresh}>
        <summary style={{ fontSize: 12, cursor: "pointer" }}>
          {fresh ? "完整请求 (含之前的对话、系统提示、工具定义)" : "请求内容"}
        </summary>
        <div style={{ ...pre, ...box, maxHeight: 360, overflow: "auto" }}>{p.text}</div>
      </details>
    </div>
  );
}

function fmtBytes(n: number): string {
  if (n >= 1024 * 1024 * 1024) return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024).toFixed(1)} KB`;
}

/** 保留期 + 占用空间 + 「检查记录有没有被改过」 */
export function IntegrityPanel() {
  const [info, setInfo] = React.useState<TransparentLogInfo | null>(null);
  const [days, setDays] = React.useState("");
  const [msg, setMsg] = React.useState<string | null>(null);
  const [report, setReport] = React.useState<(TransparentLogVerifyReport & { anchorNote?: string }) | null>(null);
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(() => {
    transparentLogInfo()
      .then((i) => {
        setInfo(i);
        setDays(String(i.retentionDays));
      })
      .catch((e) => setMsg(String(e)));
  }, []);
  React.useEffect(load, [load]);

  const saveRetention = async () => {
    const n = Number(days);
    if (!Number.isInteger(n) || n < 1 || n > 3650) {
      setMsg("保留期要填 1 到 3650 之间的整数天");
      return;
    }
    if (info && n < info.retentionDays && !window.confirm(`改成 ${n} 天会马上删掉 ${n} 天以前的记录, 确定吗?`)) return;
    setBusy(true);
    try {
      const removed = await transparentLogSetRetention(n);
      setMsg(removed > 0 ? `已改成 ${n} 天, 清掉 ${removed} 条过期记录` : `已改成 ${n} 天`);
      load();
    } catch (e) {
      setMsg(String(e));
    } finally {
      setBusy(false);
    }
  };

  const verify = async () => {
    setBusy(true);
    setReport(null);
    let anchors: EgressAnchor[] | undefined;
    let anchorNote: string | undefined;
    try {
      anchors = await fetchEgressAnchors();
    } catch (e) {
      anchorNote = `取不到中央存的每日指纹 (${String(e)}), 这次只查了本机这条链; 末尾被删查不出来`;
    }
    try {
      setReport({ ...(await transparentLogVerify(anchors)), anchorNote });
    } catch (e) {
      setMsg(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section style={{ ...box, display: "grid", gap: 8, marginBottom: "var(--space-3)" }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, fontSize: 12 }}>
        <span>保留</span>
        <input
          value={days}
          onChange={(e) => setDays(e.target.value.replace(/\D/g, ""))}
          style={{ width: 56, padding: "2px 6px", fontSize: 12 }}
          aria-label="保留天数"
        />
        <span>天</span>
        <button onClick={() => void saveRetention()} disabled={busy || !info || days === String(info.retentionDays)}>
          保存
        </button>
        <span style={muted}>
          {info ? `共 ${info.rows} 条, 占 ${fmtBytes(info.dbBytes)}` : ""}
          {info?.anchorLastSent ? ` · 上次报指纹 ${info.anchorLastSent.slice(0, 16).replace("T", " ")}` : ""}
        </span>
        <span style={{ flex: 1 }} />
        <button onClick={() => void verify()} disabled={busy}>
          {busy ? "检查中…" : "检查记录有没有被改过"}
        </button>
      </div>
      <div style={muted}>
        每条记录都带指纹, 跟前一条串成链: 改或删中间任何一条都查得出。每天把最新的指纹报给公司 (只有指纹, 没有内容),
        末尾被删也查得出。
      </div>
      {msg && <div style={{ fontSize: 12 }}>{msg}</div>}
      {report && (
        <div style={{ fontSize: 12, display: "grid", gap: 2 }}>
          <div style={{ color: report.ok ? "var(--catfish-cyan)" : "var(--status-err, #d9534f)", fontWeight: 600 }}>
            {report.ok
              ? `✓ 没被改过: 查了 ${report.checked} 条${report.anchorsChecked ? `, 跟公司存的 ${report.anchorsChecked} 个每日指纹都对得上` : ""}`
              : `✗ 发现问题 (从第 ${report.firstBadId ?? "?"} 条开始)`}
          </div>
          {report.problems.map((p, i) => (
            <div key={i}>· {p}</div>
          ))}
          {report.unchained > 0 && <div style={muted}>另有 {report.unchained} 条是升级前记的, 没有指纹</div>}
          {report.anchorNote && <div style={muted}>{report.anchorNote}</div>}
        </div>
      )}
    </section>
  );
}

const muted: React.CSSProperties = { fontSize: 11, color: "var(--catfish-text-muted)" };
const box: React.CSSProperties = {
  border: "1px solid var(--catfish-border)",
  borderRadius: 6,
  padding: 8,
  background: "var(--catfish-bg)",
};
const pre: React.CSSProperties = {
  fontFamily: "monospace",
  fontSize: 12,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
};
