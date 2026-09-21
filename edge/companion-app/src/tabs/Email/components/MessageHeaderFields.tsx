import { useState } from "react";

import { emailExportAttachment, openFile } from "../../../lib/tauri";
// ⚠ type-only import: DetailPane 也 import 这个文件, 普通 import 会形成
// 运行时循环。`import type` 编译期就擦掉了, 不产生循环。
import type { FullMessage } from "./DetailPane";

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
          <div>
            {msg.attachments.map((a, i) => (
              <button
                key={i}
                onClick={async () => {
                  // P3.5.103 (6/24 鸿波): 点附件 → CLI 导出到本地 tmp → 系统默认 app 打开.
                  // CLI 退出码 4 = adapter 不支持 (outlook 暂未 implement export_attachment).
                  try {
                    const path = await emailExportAttachment(msg.id, a.filename);
                    await openFile(path);
                  } catch (e) {
                    console.warn("[EmailDetail] 打开附件失败:", e);
                    alert(`打开附件失败: ${e instanceof Error ? e.message : String(e)}`);
                  }
                }}
                title={`点击下载并用系统默认 app 打开: ${a.filename}`}
                style={{
                  marginRight: 8,
                  padding: "3px 10px",
                  border: "1px solid var(--catfish-accent, #0d9488)",
                  borderRadius: 4,
                  background: "transparent",
                  color: "var(--catfish-accent, #0d9488)",
                  cursor: "pointer",
                  fontSize: 12,
                }}
              >
                📎 {a.filename} ({Math.round(a.size_bytes / 1024)} KB)
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
