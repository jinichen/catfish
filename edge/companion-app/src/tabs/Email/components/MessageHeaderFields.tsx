import { useState } from "react";
import { alertDialog } from "../../../components/dialogs";

import { emailExportAttachment, openFile } from "../../../lib/tauri";
// ⚠ type-only import: DetailPane 也 import 这个文件, 普通 import 会形成
// 运行时循环。`import type` 编译期就擦掉了, 不产生循环。
import type { FullMessage } from "./DetailPane";
import { _formatSize, _splitFilename } from "./helpers";

/** 邮件头那块 —— 发件人/收件人/抄送/时间/账号/附件。
 *
 * 9/21 从 DetailPane.tsx 搬出来 (加"只在本地档案"提示后 828 行, 越过仓库
 * 的 800 行红线)。挑这块搬是因为它是**纯展示**: 一个 msg 进来, 一块 grid
 * 出去, 跟删除/回复/草稿那几个状态机零耦合, 搬动风险最低。
 *
 * 唯一的交互是附件按钮, 而它自己就是闭合的 (导出 → 系统打开 → 失败弹窗),
 * 不往外抛状态。
 */
/** P3.3.57 (6/12 鸿波): 大群发邮件 header 收件人/抄送默认折叠.
 *  默认显前 N 个 + "... 共 X 人 [展开]", 点击切换 [折叠].
 *  防 81 收件人 + 30 抄送一次铺开把邮件正文挤出 viewport.
 */
function CollapsibleAddresses({ addrs, previewN = 3 }: { addrs: string[]; previewN?: number }) {
  const [expanded, setExpanded] = useState(false);
  if (addrs.length <= previewN) {
    return <>{addrs.join(", ")}</>;
  }
  return (
    <>
      {expanded ? addrs.join(", ") : addrs.slice(0, previewN).join(", ")}
      {!expanded && <span style={{ color: "var(--catfish-text-muted)" }}>... 共 {addrs.length} 人</span>}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        style={{
          marginLeft: 6,
          background: "transparent",
          border: "1px solid var(--catfish-border)",
          borderRadius: 3,
          padding: "0 6px",
          fontSize: 10,
          color: "var(--catfish-cyan)",
          cursor: "pointer",
        }}
      >
        {expanded ? "折叠" : "展开"}
      </button>
    </>
  );
}


/** 附件栏里的一行: 📎 文件名 (太长省中间) …… 大小。点了用系统默认 app 打开。 */
function AttachmentRow({ msgId, filename, sizeBytes }: { msgId: string; filename: string; sizeBytes: number }) {
  const [hover, setHover] = useState(false);
  const [head, tail] = _splitFilename(filename);
  return (
    <button
      type="button"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onClick={async () => {
        // P3.5.103 (6/24 鸿波): 点附件 → CLI 导出到本地 tmp → 系统默认 app 打开.
        // CLI 退出码 4 = adapter 不支持 (outlook 暂未 implement export_attachment).
        try {
          const path = await emailExportAttachment(msgId, filename);
          await openFile(path);
        } catch (e) {
          console.warn("[EmailDetail] 打开附件失败:", e);
          void alertDialog(`打开附件失败: ${e instanceof Error ? e.message : String(e)}`);
        }
      }}
      title={`点击用系统默认 app 打开: ${filename}`}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        width: "100%",
        minWidth: 0,
        padding: "5px 10px",
        border: `1px solid ${hover ? "var(--catfish-cyan)" : "var(--catfish-border)"}`,
        borderRadius: 6,
        background: hover ? "var(--catfish-cyan-tint)" : "var(--catfish-bg)",
        color: "var(--catfish-text)",
        cursor: "pointer",
        fontSize: 12,
        fontFamily: "inherit",
        textAlign: "left",
      }}
    >
      <span aria-hidden="true" style={{ flex: "0 0 auto" }}>📎</span>
      <span style={{ flex: "1 1 auto", minWidth: 0, display: "flex" }}>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{head}</span>
        {tail && <span style={{ flex: "0 0 auto", whiteSpace: "nowrap" }}>{tail}</span>}
      </span>
      <span style={{ flex: "0 0 auto", color: "var(--catfish-text-muted)", fontVariantNumeric: "tabular-nums" }}>
        {_formatSize(sizeBytes)}
      </span>
    </button>
  );
}


export default function MessageHeaderFields({ msg }: { msg: FullMessage }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "auto 1fr",
        gap: "4px 12px",
        fontSize: 12,
        color: "var(--catfish-text-muted)",
        marginTop: 12,
      }}
    >
      <div>发件人</div>
      <div style={{ color: "var(--catfish-text)" }}>{msg.sender}</div>
      {msg.recipients && msg.recipients.length > 0 && (
        <>
          <div>收件人</div>
          <div><CollapsibleAddresses addrs={msg.recipients} /></div>
        </>
      )}
      {msg.cc && msg.cc.length > 0 && (
        <>
          <div>抄送</div>
          <div><CollapsibleAddresses addrs={msg.cc} /></div>
        </>
      )}
      <div>时间</div>
      {/* 8/8 评审: 原样输出 ISO 串 (2026-08-07T15:45:51.210Z) → 格式化为人话.
          解析失败 (老数据格式怪) 时回退原串. */}
      <div>
        {(() => {
          const d = new Date(msg.date);
          return Number.isNaN(d.getTime())
            ? msg.date
            : d.toLocaleString("zh-CN", {
                month: "long",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              });
        })()}
      </div>
      <div>账号</div>
      <div>{msg.account}</div>
      {msg.has_attachments && msg.attachments && msg.attachments.length > 0 && (
        <>
          <div>附件</div>
          {/* 11/13: 一个附件一行、等宽左对齐。原来是按文件名长度各画各的框, 长名字折成
              两行还居中, 三个附件三种宽度。名字太长时省中间、留结尾和扩展名。 */}
          <div style={{ display: "flex", flexDirection: "column", gap: 4, maxWidth: 560, minWidth: 0 }}>
            {msg.attachments.map((a, i) => (
              <AttachmentRow key={i} msgId={msg.id} filename={a.filename} sizeBytes={a.size_bytes} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
