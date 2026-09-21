import { useState } from "react";

import type { EmailSourceDiscovery } from "../../../lib/tauri";
import ImapSetup from "./ImapSetup";

/** 来源的显示名。9/18 加了 imap —— 唯一不依赖邮件客户端的路径。 */
const SOURCE_LABEL: Record<string, string> = {
  "outlook-win": "Outlook",
  "eml-dir": "导出的邮件目录",
  imap: "邮箱直连 (IMAP)",
};

interface Props {
  discovery: EmailSourceDiscovery;
  busy: boolean;
  error: string | null;
  onRescan: () => void;
  /** 「扫描一次」—— 无视"值不值得探"的判断, 本机客户端全探一遍。
   *
   * ⚠ 跟 onRescan 是两件事, 别合并。onRescan 是普通刷新 (比如刚配完 IMAP),
   * 走默认路径; 这个会真的去 Dispatch Outlook COM, 而那正是 9/21 把
   * catfish-email 安装搞挂的动作。只能挂在员工**主动点**的按钮上。 */
  onForceScan: () => void;
  onSelect: (client: string, root?: string) => void;
  onPickMailDirectory: () => void;
}

/**
 * 没配好邮件来源时的引导卡片。
 *
 * # 9/21 重排: 把 IMAP 放到第一位
 *
 * 鸿波截图 + 一句"Windows 都不支持了, 为什么还要扫?"。看了一眼确实站不住:
 *
 * 卡片开头是「自动发现 Windows 邮件客户端」, 然后「暂时没有找到可用邮箱」,
 * 然后三段解释 Outlook COM 为什么起不来、Foxmail 要怎么导出 .eml, 最后才
 * 是能用的那个 IMAP。
 *
 * 但 9/18 之后 Windows 上的事实是:
 *
 *   Foxmail 7.2   邮件文件加密 (熵 7.96), 本地解析已经删了 (908089b)
 *   新版 Outlook  Microsoft.OutlookForWindows 不提供 COM, 装了也读不到
 *   经典 Outlook  还能用, 但装的人越来越少
 *
 * 也就是说 **IMAP 是绝大多数 Windows 员工唯一能走通的路**, 而界面把它排在
 * 一屏失败信息的后面, 还得滚动才看得见 (而且当时滚不动)。
 *
 * 排版顺序应该跟"你该做什么"一致, 不是跟"我们是怎么一路试过来的"一致。
 * 那三段诊断没删 —— 经典 Outlook 用户和有 .eml 导出的人确实需要 —— 但折起来,
 * 默认不占版面。
 */
export default function EmailSourceSetup({
  discovery,
  busy,
  error,
  onRescan,
  onForceScan,
  onSelect,
  onPickMailDirectory,
}: Props) {
  // IMAP 不挑平台 (它不依赖任何邮件客户端), 所以非 Windows 上也要给配置入口。
  if (discovery.platform !== "Windows") return <ImapSetup onConfigured={onRescan} />;

  return (
    <WindowsSetup
      {...{ discovery, busy, error, onRescan, onForceScan, onSelect, onPickMailDirectory }}
    />
  );
}

function WindowsSetup({
  discovery,
  busy,
  error,
  onRescan,
  onForceScan,
  onSelect,
  onPickMailDirectory,
}: Props) {
  const [showOthers, setShowOthers] = useState(false);

  // IMAP 自己那块有完整的状态显示 (已配置 / 未配置), 所以从"其他来源"里排掉,
  // 否则同一件事在一张卡片上出现两次。
  const others = discovery.sources.filter((s) => s.client !== "imap");
  const readyOthers = others.filter((s) => s.status === "ready");

  return (
    <div
      style={{
        margin: "8px 12px",
        fontSize: 12,
        lineHeight: 1.5,
      }}
    >
      {/* ① 能用的那条路放最前面 */}
      <ImapSetup onConfigured={onRescan} />

      {/* ② 有现成可用的本地客户端才值得占版面。
          没有的时候不该拿"暂时没有找到可用邮箱"这种话当开场白 ——
          IMAP 就在上面, 员工并没有无路可走。 */}
      {readyOthers.length > 0 && (
        <div
          style={{
            marginTop: 8,
            padding: "10px 12px",
            border: "1px solid var(--catfish-border)",
            borderRadius: 8,
            background: "var(--catfish-bg)",
          }}
        >
          <strong>也可以用本机已有的邮件客户端</strong>
          {readyOthers.map((source) => (
            <div
              key={source.client}
              style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6 }}
            >
              <span style={{ flex: 1 }}>
                {SOURCE_LABEL[source.client] ?? source.client} · {source.accounts.length} 个账号
              </span>
              <button
                type="button"
                disabled={busy}
                onClick={() => onSelect(source.client, source.root ?? undefined)}
              >
                {discovery.selected_client === source.client ? "当前使用" : "使用"}
              </button>
            </div>
          ))}
        </div>
      )}

      {/* ③ 诊断信息折起来。经典 Outlook 用户和手里有 .eml 导出的人需要它,
          但那是少数, 不该让所有人先读完一屏失败原因才看到能用的那个。 */}
      <button
        type="button"
        onClick={() => setShowOthers((v) => !v)}
        style={{
          marginTop: 8,
          background: "none",
          border: "none",
          padding: 0,
          font: "inherit",
          color: "var(--catfish-text-muted)",
          cursor: "pointer",
          textDecoration: "underline",
        }}
      >
        {showOthers ? "收起" : "读不到本机 Outlook / Foxmail？"}
      </button>

      {showOthers && (
        <div
          style={{
            marginTop: 6,
            padding: "10px 12px",
            border: "1px solid var(--catfish-border)",
            borderRadius: 8,
            background: "var(--catfish-bg)",
            color: "var(--catfish-text-muted)",
          }}
        >
          {others.map((source) => (
            <div key={source.client} style={{ marginBottom: 8 }}>
              <strong style={{ color: "var(--catfish-text)" }}>
                {SOURCE_LABEL[source.client] ?? source.client}
              </strong>
              <div>
                {source.status === "ready"
                  ? `${source.accounts.length} 个账号`
                  : source.reason ?? (source.status === "skipped" ? "已跳过探测" : "暂不可用")}
              </div>
            </div>
          ))}
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <button type="button" disabled={busy} onClick={onPickMailDirectory}>
              选择邮件目录
            </button>
            {/* 「扫描一次」不是「重新扫描」的换皮:
                默认路径根本不去碰这两个来源 (见 discovery._windows_clients),
                所以这里按下去是**第一次**真的去探, 而不是再探一遍。
                名字要对得上行为, 否则员工按了以为没生效。 */}
            <button type="button" disabled={busy} onClick={onForceScan}>
              扫描一次
            </button>
          </div>
        </div>
      )}

      {error && <div style={{ color: "var(--status-danger)", marginTop: 6 }}>{error}</div>}
    </div>
  );
}
