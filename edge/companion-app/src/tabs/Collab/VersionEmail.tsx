/**
 * 把一份协同文档的最新版发邮件 (11/12 鸿波「这个是不是也应该支持邮件 …… 给出一个合理建议」→「按这个做 …… 附件的大小最好有地方设置」)。
 *
 * 只在最新一版上有 (旧版本留着是对比 / 追溯用的, 很少要再发; 真要发, 旧版本的文件在群消息里能「在文件夹中显示」再附)。
 *  · 附件: 默认干净版; 修订版 (每处标着谁改的) 勾上才带, 给要逐条审的人。
 *  · 收件人: 这一轮请来改的同事 (都是群成员, 群里有邮箱), 员工能删; 抄送框照样能从知识库搜人。
 *  · 正文: 第几版、合并说明 (合并了谁几处、冲突谁定的、发合并版时写的那句话)。
 *  · 附件超过员工设的上限 (「邮件 → 设置」, 默认 25 MB): ComposeDialog 当场能改上限, 或不带附件、正文写明文件在群里。
 * 存草稿还是发送员工自己点。
 */
import { useEffect, useState } from "react";

import { roomFileSave } from "../../lib/tauri_colleague_rooms";
import type { PickedAttachment } from "../../lib/tauri_cron";
import type { ReviseVersion } from "../../lib/tauri_room_revise";
import ComposeDialog from "../Email/components/ComposeDialog";

export function versionMailBody(title: string, v: ReviseVersion): string {
  const lines = (v.text ?? "").split("\n").slice(1).map((l) => l.trim()).filter(Boolean);
  const head = v.kind === "merged" ? `《${title}》v${v.version} 合并版, 请查收附件。` : `《${title}》v${v.version}, 请查收附件。`;
  return [head, ...(lines.length ? ["", "这一版:", ...lines.map((l) => `· ${l}`)] : [])].join("\n");
}

export default function VersionEmail({ roomId, title, version, me, onClose, onDone }: {
  roomId: string; title: string; version: ReviseVersion; me: string; onClose: () => void; onDone: (text: string) => void;
}) {
  const clean = version.files.find((f) => !f.name.includes("修订版")) ?? version.files[0];
  const tracked = version.files.find((f) => f.name.includes("修订版") && f !== clean);
  const [withTracked, setWithTracked] = useState(false);
  const [paths, setPaths] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const editors = (version.editors ?? []).filter((e) => e.email !== me);

  // 群里的文件先存到本机 (下载目录的「鲶鱼群文件」下), 才能当邮件附件
  const want = [clean, withTracked ? tracked : undefined].filter((f): f is NonNullable<typeof f> => !!f);
  const wantKey = want.map((f) => f.id).join(",");
  useEffect(() => {
    let alive = true;
    for (const f of want) {
      if (paths[f.id]) continue;
      roomFileSave(roomId, f.id)
        .then((r) => { if (alive) setPaths((p) => ({ ...p, [f.id]: r.path })); })
        .catch((e) => { if (alive) setError(`「${f.name}」取不到 (${e instanceof Error ? e.message : String(e)}), 可能过了群记录保留期`); });
    }
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId, wantKey]);

  const ready = want.every((f) => paths[f.id]) || !!error;
  const attachments: PickedAttachment[] = want.filter((f) => paths[f.id]).map((f) => ({ path: paths[f.id], name: f.name, size: f.size }));
  const muted = { fontSize: 12, color: "var(--catfish-text-muted)", lineHeight: 1.6 } as const;

  return (
    <ComposeDialog
      title={`发邮件 · 《${title}》v${version.version}`}
      subject={`《${title}》v${version.version}${version.kind === "merged" ? " 合并版" : ""}`}
      body={versionMailBody(title, version)}
      initialTo={editors.map((e) => e.email).join(", ")}
      attachments={attachments}
      dropNote={`文件在协同群里 (《${title}》v${version.version}), 太大没随邮件附上, 请到群里取。`}
      ready={ready}
      resetKey={`ver-${version.msg_id}-${wantKey}`}
      onClose={onClose}
      onDone={onDone}
      notes={<>
        {editors.length > 0
          ? <span style={{ ...muted, color: "var(--status-ok)" }}>收件人已填这一轮请来改的同事: {editors.map((e) => e.name).join("、")} (可删)</span>
          : <span style={muted}>这一版没有记到请了谁改, 收件人请自己填。</span>}
        {tracked && (
          <label style={{ ...muted, display: "flex", gap: 6, alignItems: "center", color: "var(--catfish-text)" }}>
            <input type="checkbox" checked={withTracked} onChange={(e) => setWithTracked(e.target.checked)} />
            同时附上修订版 (每处修改标着是谁改的, 给要逐条审阅的人)
          </label>
        )}
        {error && <span style={{ fontSize: 12, color: "var(--status-warn)" }}>{error}</span>}
      </>}
    />
  );
}
