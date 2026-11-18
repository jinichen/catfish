/** 邮件页搜索框 (从 EmailTab 拆出来, 11/19)。列表里先筛, 列表以外更早的信见 OlderMailResults。 */
export default function MailSearchBox({ value, onChange, shown, total }: {
  value: string;
  onChange: (v: string) => void;
  shown: number;
  total: number;
}) {
  return (
    <>
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="🔍 搜主题 / 发件人 / 账号 (更早的也搜)"
        aria-label="搜邮件"
        style={{
          width: "100%", padding: "4px 8px", fontSize: 12,
          border: "1px solid var(--catfish-border)", borderRadius: 4,
          background: "var(--catfish-bg)", color: "var(--catfish-text)",
          fontFamily: "inherit", boxSizing: "border-box",
        }}
      />
      {value && (
        <div style={{ fontSize: 10, color: "var(--catfish-text-muted)", marginTop: 4 }}>
          🔍 列表里筛到 {shown} / {total}
        </div>
      )}
    </>
  );
}
