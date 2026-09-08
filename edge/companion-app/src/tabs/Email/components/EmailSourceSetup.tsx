import type { EmailSourceDiscovery } from "../../../lib/tauri";

interface Props {
  discovery: EmailSourceDiscovery;
  busy: boolean;
  error: string | null;
  onRescan: () => void;
  onSelect: (client: string, root?: string) => void;
  onPickFoxmailDirectory: () => void;
}

export default function EmailSourceSetup({
  discovery,
  busy,
  error,
  onRescan,
  onSelect,
  onPickFoxmailDirectory,
}: Props) {
  if (discovery.platform !== "Windows") return null;

  const ready = discovery.sources.filter((source) => source.status === "ready");
  return (
    <div
      style={{
        margin: "8px 12px",
        padding: "10px 12px",
        border: "1px solid var(--catfish-border)",
        borderRadius: 8,
        background: "var(--catfish-bg)",
        fontSize: 12,
        lineHeight: 1.5,
      }}
    >
      <strong>自动发现 Windows 邮件客户端</strong>
      {ready.length === 0 && <div style={{ marginTop: 4 }}>暂时没有找到可用邮箱，请选择 Foxmail 数据目录或检查 Outlook 配置。</div>}
      {ready.length > 1 && <div style={{ marginTop: 4 }}>找到多个邮件来源，请选择本次使用的客户端。</div>}
      {discovery.sources.map((source) => (
        <div key={source.client} style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6 }}>
          <span style={{ flex: 1 }}>
            {source.client === "outlook-win" ? "Outlook" : "Foxmail"} · {source.status === "ready" ? `${source.accounts.length} 个账号` : source.reason ?? "暂不可用"}
          </span>
          {source.status === "ready" && (
            <button type="button" disabled={busy} onClick={() => onSelect(source.client, source.root ?? undefined)}>
                {discovery.selected_client === source.client ? "当前使用" : "使用"}
            </button>
          )}
        </div>
      ))}
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <button type="button" disabled={busy} onClick={onPickFoxmailDirectory}>选择 Foxmail 目录</button>
        <button type="button" disabled={busy} onClick={onRescan}>重新扫描</button>
      </div>
      {error && <div style={{ color: "var(--status-danger)", marginTop: 6 }}>{error}</div>}
    </div>
  );
}
