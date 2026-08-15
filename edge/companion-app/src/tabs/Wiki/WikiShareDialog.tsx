/** 分享到部门 wiki 的 dialog (P3.3.18, 6/10)。
 *
 * 8/15 从 WikiPreview.tsx 搬出来 (1096 行, 过了 CLAUDE.md §1 的 800 红线)。
 *
 * # 这次是纯搬运, 不是重构
 *
 * WikiPreview 是**一个** 1001 行的组件, 不像 WikiTree 那样是一堆平级声明。
 * 这种形状最容易在拆的时候顺手"改好一点", 然后就说不清哪个行为变了 ——
 * 而这个文件**一条测试都没有** (仓里 23 个 test 文件没有一个碰 Wiki 组件)。
 *
 * 所以这里刻意只做机械搬运:
 *   · JSX 一行没改, 只整体减了 6 格缩进
 *   · state 和 handler **全部留在 WikiPreview 里**, 原样当 props 传下来
 *   · props 名字跟原来的变量名逐字一致 (setShareDialogOpen 没改叫 onClose)
 *
 * props 有 12 个, 确实多。但"把 state 提到子组件里"是行为改动 —— 比如
 * shareAck 若改成 dialog 内部 state, 关掉再开就自动重置了, 那跟现在
 * 第 281/293 行显式 setShareAck(false) 的时机不一定一样。没有测试兜底的时候,
 * 宁可多几个 props。
 *
 * # 这个 dialog 守的东西 (别顺手简化掉)
 *
 * 分享是**不可逆**的 —— 别人 pull 走的副本撤不回来 (manifesto 公理 4)。
 * 所以流程是: 第一次提交若返回 warnings (PII / 内网地址 / 敏感词), 不直接发,
 * 而是显警告 + 一个 ack checkbox, 员工勾了才 retry (acknowledge_warnings=true)。
 * namespace 也**不预填**, 必须员工自己写 (公理 3, 不静默自决)。
 *
 * 这两条是产品约束不是交互糖, 改之前先问。
 */
import type { WikiFileFull } from "../../lib/tauri_wiki";

interface WikiShareDialogProps {
  selectedFile: WikiFileFull;
  shareNamespace: string;
  setShareNamespace: (v: string) => void;
  shareAck: boolean;
  setShareAck: (v: boolean) => void;
  sharing: boolean;
  shareWarnings: Array<{ category: string; hits: any[]; advice: string }>;
  shareError: string | null;
  shareSuccess: string | null;
  sensitiveTermsHint: string | null;
  handleShare: (acknowledgeWarnings: boolean) => void;
  handleEnsureSensitiveTerms: () => void;
  setShareDialogOpen: (v: boolean) => void;
}

export default function WikiShareDialog({
  selectedFile,
  shareNamespace,
  setShareNamespace,
  shareAck,
  setShareAck,
  sharing,
  shareWarnings,
  shareError,
  shareSuccess,
  sensitiveTermsHint,
  handleShare,
  handleEnsureSensitiveTerms,
  setShareDialogOpen,
}: WikiShareDialogProps) {
  return (
  <div
    style={{
      position: "fixed",
      inset: 0,
      background: "rgba(0,0,0,0.5)",
      zIndex: 1000,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      padding: 20,
    }}
    onClick={() => setShareDialogOpen(false)}
  >
    <div
      onClick={(e) => e.stopPropagation()}
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: 24,
        maxWidth: 560,
        width: "100%",
        maxHeight: "90vh",
        overflowY: "auto",
      }}
    >
      <h3 style={{ margin: "0 0 12px", display: "flex", alignItems: "center", gap: 8 }}>
        📤 分享 wiki 到部门
      </h3>
      <div style={{ fontSize: 13, color: "var(--catfish-text-muted)", marginBottom: 16 }}>
        要分享: <code>{selectedFile.info.rel_path}</code>
      </div>

      {/* 强警告 banner — 用户拍要的, manifesto 公理 4 提醒 */}
      <div
        style={{
          background: "#fef3c7",
          border: "1px solid #f59e0b",
          color: "#78350f",
          padding: 12,
          borderRadius: 6,
          fontSize: 13,
          lineHeight: 1.6,
          marginBottom: 16,
        }}
      >
        <strong>⚠️ 重要提醒</strong> (manifesto 公理 4)
        <ul style={{ margin: "6px 0 0", paddingLeft: 20 }}>
          <li>分享后, 部门所有同事能看到 / 装这条 wiki 到本机</li>
          <li>
            你后面 <strong>哪怕撤回</strong>, 中央那一份会清零 + 标 stale, 但 <strong>已 pull 装本机的副本撤不回</strong>
          </li>
          <li>已扩散的信息在部门里"永久存在", 跟 OneNote / Confluence 一回事</li>
          <li>客户名 / 项目细节 / 关键人名 这种敏感内容, 决定前想清楚</li>
        </ul>
      </div>

      {/* P3.3.18 Phase 4 P2: 敏感词文件 onboarding hint */}
      <div
        style={{
          background: "rgba(74,158,255,0.08)",
          border: "1px solid rgba(74,158,255,0.2)",
          padding: "8px 12px",
          borderRadius: 4,
          fontSize: 12,
          marginBottom: 12,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <span style={{ flex: 1 }}>
          💡 想扫客户名/项目代号? 配 <code>~/.catfish/wiki/sensitive_terms.txt</code>
        </span>
        <button
          onClick={() => void handleEnsureSensitiveTerms()}
          disabled={sharing}
          style={{
            fontSize: 11,
            padding: "3px 10px",
            background: "transparent",
            color: "var(--catfish-text)",
            border: "1px solid var(--catfish-border)",
            borderRadius: 3,
            cursor: "pointer",
            whiteSpace: "nowrap",
          }}
        >
          创建模板
        </button>
      </div>
      {sensitiveTermsHint && (
        <div
          style={{
            fontSize: 11,
            marginBottom: 12,
            color: sensitiveTermsHint.startsWith("✗") ? "#dc2626" : "#16a34a",
            padding: "4px 8px",
            background: sensitiveTermsHint.startsWith("✗") ? "#fee2e2" : "#d1fae5",
            borderRadius: 3,
          }}
        >
          {sensitiveTermsHint}
        </div>
      )}

      <div style={{ marginBottom: 16 }}>
        <label
          style={{
            display: "block",
            fontSize: 12,
            color: "var(--catfish-text-muted)",
            marginBottom: 6,
          }}
        >
          目标部门 namespace (格式 dept/&lt;部门&gt;)
        </label>
        <input
          type="text"
          value={shareNamespace}
          onChange={(e) => setShareNamespace(e.target.value)}
          placeholder="dept/finance"
          style={{
            width: "100%",
            padding: "8px 12px",
            fontSize: 13,
            border: "1px solid var(--catfish-border)",
            borderRadius: 4,
            background: "var(--catfish-bg)",
            color: "var(--catfish-text)",
            fontFamily: "var(--font-mono)",
          }}
          disabled={sharing}
        />
        <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: 4 }}>
          例: dept/finance, dept/sales, dept/it. 当前只接受 dept/ 开头.
        </div>
      </div>

      <div style={{ marginBottom: 16 }}>
        <label style={{ fontSize: 13, display: "flex", gap: 8, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={shareAck}
            onChange={(e) => setShareAck(e.target.checked)}
            disabled={sharing}
          />
          <span>
            <strong>我知道</strong> 已 pull 副本撤不回, 信息会在部门里扩散
          </span>
        </label>
      </div>

      {/* warnings 显 (PII / 内网 / 敏感词命中) */}
      {shareWarnings.length > 0 && (
        <div
          style={{
            background: "#fee2e2",
            border: "1px solid #dc2626",
            color: "#7f1d1d",
            padding: 12,
            borderRadius: 6,
            fontSize: 12,
            marginBottom: 16,
          }}
        >
          <strong>扫到 {shareWarnings.length} 类内容警告</strong>:
          <ul style={{ margin: "6px 0 0", paddingLeft: 20 }}>
            {shareWarnings.map((w, i) => (
              <li key={i}>
                <strong>{w.category}</strong>: {w.advice} ({w.hits.length} 处)
              </li>
            ))}
          </ul>
          <div style={{ marginTop: 8, color: "#7f1d1d" }}>
            确认要继续 share 这些内容到部门? 点"我看过了, 强发"再发一次.
          </div>
        </div>
      )}

      {shareError && (
        <div
          style={{
            background: "#fee2e2",
            border: "1px solid #dc2626",
            color: "#7f1d1d",
            padding: 10,
            borderRadius: 4,
            fontSize: 12,
            marginBottom: 16,
          }}
        >
          {shareError}
        </div>
      )}

      {shareSuccess && (
        <div
          style={{
            background: "#d1fae5",
            border: "1px solid #16a34a",
            color: "#14532d",
            padding: 10,
            borderRadius: 4,
            fontSize: 12,
            marginBottom: 16,
          }}
        >
          ✓ {shareSuccess}
        </div>
      )}

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <button
          className="approval-banner__btn-link"
          onClick={() => setShareDialogOpen(false)}
          disabled={sharing}
        >
          {shareSuccess ? "关闭" : "取消"}
        </button>
        {!shareSuccess && (
          <button
            className="approval-banner__btn-primary"
            onClick={() => void handleShare(shareWarnings.length > 0)}
            disabled={sharing || !shareAck}
          >
            {sharing
              ? "分享中…"
              : shareWarnings.length > 0
                ? "我看过了, 强发"
                : "确认分享"}
          </button>
        )}
      </div>
    </div>
  </div>
  );
}
