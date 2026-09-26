/** 草稿箱里的一封: 核对后由员工亲手发送 (9/26)。
 *
 * 红线: 发送不可撤销, 只能是员工点的。两步确认 (Tauri 里 window.confirm 不可靠,
 * 跟删除按钮同一个做法): 第一次点变成「再次点击确认发送」, 3 秒内再点才发。
 */
import { useEffect, useState } from "react";
import { emailSendMessage } from "../../../lib/tauri_briefing";

export default function DraftSendButton({ id, onSent }: { id: string; onSent: () => void }) {
  const [armed, setArmed] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!armed) return;
    const t = window.setTimeout(() => setArmed(false), 3000);
    return () => window.clearTimeout(t);
  }, [armed]);
  useEffect(() => { setArmed(false); setError(null); }, [id]);

  const click = async () => {
    if (!armed) {
      setArmed(true);
      return;
    }
    setArmed(false);
    setSending(true);
    setError(null);
    try {
      await emailSendMessage(id);
      onSent();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => void click()}
        disabled={sending}
        title="发出这封草稿。发出后不可撤回。"
        style={{
          background: armed ? "rgb(21, 128, 61)" : "var(--catfish-cyan)",
          color: "#fff",
          border: "none",
          borderRadius: 4,
          padding: "8px 16px",
          fontSize: 13,
          fontWeight: 600,
          cursor: sending ? "wait" : "pointer",
          fontFamily: "inherit",
        }}
      >
        {sending ? "发送中…" : armed ? "再次点击确认发送 (3s)" : "发送这封草稿"}
      </button>
      {error && (
        <div style={{ width: "100%", fontSize: 12, color: "rgb(220, 80, 60)" }}>发送失败：{error}</div>
      )}
    </>
  );
}
