/** 「附件上限」设置 (11/12): 邮件设置里一张小卡; 发邮件弹窗里附件太大时也内嵌一份, 当场能改。 */
import { useEffect, useState } from "react";

import {
  DEFAULT_ATTACH_MB, MAX_ATTACH_MB, MIN_ATTACH_MB, getAttachLimitMb, onAttachLimitChange, setAttachLimitMb,
} from "../../../lib/emailAttachLimit";

export function useAttachLimitMb(): number {
  const [mb, setMb] = useState(getAttachLimitMb);
  useEffect(() => onAttachLimitChange(() => setMb(getAttachLimitMb())), []);
  return mb;
}

export function AttachLimitInput({ compact }: { compact?: boolean }) {
  const mb = useAttachLimitMb();
  const [draft, setDraft] = useState(String(mb));
  useEffect(() => setDraft(String(mb)), [mb]);
  const commit = () => setDraft(String(setAttachLimitMb(Number(draft))));
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: compact ? 12 : 13 }}>
      附件合计上限
      <input aria-label="附件合计上限 (MB)" type="number" min={MIN_ATTACH_MB} max={MAX_ATTACH_MB} value={draft}
             onChange={(e) => setDraft(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === "Enter") commit(); }}
             style={{ width: 64, padding: "3px 6px", borderRadius: 4, border: "1px solid var(--catfish-border)",
                      background: "var(--catfish-bg-elevated)", color: "var(--catfish-text)", fontSize: 12 }} />
      MB
    </span>
  );
}

export default function AttachLimitSetting() {
  return (
    <div style={{ margin: "8px 12px", padding: "10px 12px", border: "1px solid var(--catfish-border)", borderRadius: 8,
                  background: "var(--catfish-bg)", fontSize: 12, lineHeight: 1.6, display: "flex", flexDirection: "column", gap: 4 }}>
      <strong>发邮件的附件</strong>
      <AttachLimitInput />
      <span style={{ color: "var(--catfish-muted)", fontSize: 11 }}>
        超过这个大小, 写信时会提醒「收件方邮箱多半收不了」, 也不让存草稿。默认 {DEFAULT_ATTACH_MB} MB, 可设 {MIN_ATTACH_MB}–{MAX_ATTACH_MB} MB ——
        按你们公司邮箱实际能收多大来设; 收件方是外部邮箱时以对方的上限为准。
      </span>
    </div>
  );
}
