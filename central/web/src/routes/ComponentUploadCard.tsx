/** 管理员上传组件包 (10/3)。分块 + 续传: 页面关了 / 断网了, 重新选同一个文件接着传。 */
import { useEffect, useRef, useState } from "react";

import { Card } from "../components/Card";
import { BTN, BTN_PRIMARY } from "../components/DataTable";
import {
  cancelUpload, completeUpload, listComponentsAdmin, parseComponentFilename, startUpload, uploadFrom,
  type PendingUpload,
} from "../lib/components_admin";
import { formatSize, platformLabel } from "./componentsFormat";

type Phase = "idle" | "uploading" | "paused" | "verifying" | "done" | "error";

export function ComponentUploadCard({ onPublished }: { onPublished: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [overwrite, setOverwrite] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [received, setReceived] = useState(0);
  const [speed, setSpeed] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingUpload[]>([]);
  const stopRef = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const reloadPending = () => listComponentsAdmin().then((r) => setPending(r.uploads)).catch(() => undefined);
  useEffect(() => { void reloadPending(); }, []);

  const parsed = file ? parseComponentFilename(file.name) : null;
  const busy = phase === "uploading" || phase === "verifying";

  function pick(f: File | null) {
    setFile(f);
    setPhase("idle");
    setReceived(0);
    setMessage(f && !parseComponentFilename(f.name)
      ? "文件名要是 <名字>-<x.y.z>-<平台>.tar.gz, 平台是 mac-arm64 / mac-x64 / windows-x64 / any (跟平台无关的模型用 any)"
      : null);
  }

  async function run() {
    if (!file || !parsed) return;
    stopRef.current = false;
    setMessage(null);
    setPhase("uploading");
    try {
      const st = await startUpload(file.name, file.size, overwrite);
      setReceived(st.received);
      let lastBytes = st.received;
      let lastTime = performance.now();
      const result = await uploadFrom(file, st.received, (n) => {
        setReceived(n);
        const now = performance.now();
        if (now - lastTime > 1000) {
          setSpeed(((n - lastBytes) / (now - lastTime)) * 1000);
          lastBytes = n;
          lastTime = now;
        }
      }, () => stopRef.current);
      if (result === "stopped") {
        setPhase("paused");
        void reloadPending();
        return;
      }
      setPhase("verifying"); // 服务端校验 gzip、读 pack.json、算 sha256, 两个多 G 要一会儿
      const out = await completeUpload(file.name);
      setPhase("done");
      setMessage(`✓ 已发布 ${out.published?.file ?? file.name}, 员工的 Companion 下次打开对应功能就能装`);
      void reloadPending();
      onPublished();
    } catch (e) {
      setPhase("error");
      setMessage(e instanceof Error ? e.message : String(e));
      void reloadPending();
    }
  }

  async function abandon(name: string) {
    await cancelUpload(name).catch((e) => setMessage(String(e)));
    if (file?.name === name) pick(null);
    void reloadPending();
  }

  const pct = file && file.size ? Math.floor((received / file.size) * 100) : 0;
  const eta = speed > 0 && file ? Math.ceil((file.size - received) / speed / 60) : null;
  const resumable = file ? pending.find((p) => p.file === file.name && p.size === file.size) : undefined;

  return (
    <Card title="上传组件包 (管理员)">
      <div style={{ display: "flex", flexDirection: "column", gap: 10, fontSize: 13 }}>
        <div style={{ color: "var(--text-muted)", lineHeight: 1.7 }}>
          选 <code>.tar.gz</code> 安装包, 分块上传, 断了重新选同一个文件接着传; 传完自动校验并更新清单。
          会议转写包这类带 <code>pack.json</code> 的, 文件名里的版本和平台要跟包里对得上。
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <input ref={inputRef} type="file" accept=".gz,application/gzip" style={{ display: "none" }}
            onChange={(e) => pick(e.target.files?.[0] ?? null)} />
          <button type="button" style={BTN} disabled={busy} onClick={() => inputRef.current?.click()}>选择安装包…</button>
          {file && <span>{file.name} · {formatSize(file.size)}</span>}
        </div>
        {parsed && (
          <div style={{ color: "var(--text-muted)" }}>
            将发布: <b style={{ color: "var(--text)" }}>{parsed.name}</b> · 版本 {parsed.version} · {platformLabel(parsed.platform)}
            {resumable && resumable.received > 0 && phase === "idle" && (
              <> · 上次传到 {formatSize(resumable.received)}, 接着传</>
            )}
          </div>
        )}
        {parsed && (
          <label style={{ display: "flex", gap: 6, alignItems: "center", color: "var(--text-muted)" }}>
            <input type="checkbox" checked={overwrite} disabled={busy} onChange={(e) => setOverwrite(e.target.checked)} />
            已经有同名的包就覆盖 (一般应该换个版本号, 不要覆盖)
          </label>
        )}
        {phase !== "idle" && file && (
          <div>
            <div style={{ height: 8, borderRadius: 4, background: "var(--border)", overflow: "hidden" }}>
              <div style={{ width: `${phase === "done" || phase === "verifying" ? 100 : pct}%`, height: "100%",
                background: phase === "error" ? "var(--status-err)" : "var(--accent)", transition: "width .3s" }} />
            </div>
            <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>
              {phase === "uploading" && `${pct}% · ${formatSize(received)} / ${formatSize(file.size)}`
                + (speed > 0 ? ` · ${formatSize(speed)}/s` : "") + (eta !== null ? ` · 约 ${eta} 分钟` : "")}
              {phase === "paused" && `已暂停 · ${pct}%, 点「继续上传」接着传`}
              {phase === "verifying" && "传完了, 服务端正在校验并更新清单 (两个多 G 要几十秒)…"}
            </div>
          </div>
        )}
        <div style={{ display: "flex", gap: 8 }}>
          {phase === "uploading" ? (
            <button type="button" style={BTN} onClick={() => { stopRef.current = true; }}>暂停</button>
          ) : (
            <button type="button" style={BTN_PRIMARY} disabled={!parsed || busy || phase === "done"} onClick={() => void run()}>
              {phase === "paused" || (resumable && resumable.received > 0) ? "继续上传" : "开始上传"}
            </button>
          )}
          {file && resumable && !busy && phase !== "done" && (
            <button type="button" style={BTN} onClick={() => void abandon(file.name)}>放弃这次上传</button>
          )}
        </div>
        {message && (
          <div style={{ fontSize: 12, whiteSpace: "pre-wrap",
            color: phase === "done" ? "var(--status-ok)" : phase === "error" || !parsed ? "var(--status-err)" : "var(--text-muted)" }}>
            {message}
          </div>
        )}
        {pending.filter((p) => p.file !== file?.name).length > 0 && (
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            没传完的:
            {pending.filter((p) => p.file !== file?.name).map((p) => (
              <div key={p.file} style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 4 }}>
                <span>{p.file} · {p.size ? Math.floor((p.received / p.size) * 100) : 0}% (重新选这个文件就接着传)</span>
                <button type="button" style={BTN} onClick={() => void abandon(p.file)}>放弃</button>
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
