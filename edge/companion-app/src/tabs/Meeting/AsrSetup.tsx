/**
 * 会议组件包 (FunASR + 模型, ~2.3GB) 的下载 / 安装 (10/1)。
 *
 * 不进安装包, 从中央 /components/ 按需下载 (P0, lib/tauri_components) →
 * 校验 sha256 → 本机离线装进独立 venv (meeting_asr_install)。
 * IT 手动放进 ~/.catfish/runtime/ 的包状态是 unverified: 先校验再装。
 */
import { useCallback, useEffect, useState } from "react";

import {
  componentsCancel,
  componentsDownload,
  componentsList,
  componentsVerify,
  onComponentProgress,
  type ComponentInfo,
  type ComponentProgress,
} from "../../lib/tauri_components";
import { meetingAsrInstall, onAsrInstallProgress, type AsrStatus } from "../../lib/tauri_meeting";
import { Btn, ErrorLine } from "../Collab/roomLinkUi";

const COMPONENT = "meeting-asr";

const INSTALL_STEP: Record<string, string> = {
  unpacking: "解包",
  creating_venv: "建 Python 环境",
  installing: "安装依赖 (约半分钟)",
  checking: "自检",
  finishing: "收尾",
};

function mb(n: number): string {
  return `${(n / 1e6).toFixed(0)} MB`;
}

export default function AsrSetup({ status, onReady }: { status: AsrStatus; onReady: () => void }) {
  const [comp, setComp] = useState<ComponentInfo | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [progress, setProgress] = useState<ComponentProgress | null>(null);
  const [installStep, setInstallStep] = useState<string | null>(status.installing ? "installing" : null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await componentsList();
      setComp(list.components.find((c) => c.name === COMPONENT) ?? null);
      setListError(null);
    } catch (e) {
      setListError(String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const unsubs = [
      onComponentProgress((p) => {
        if (p.name !== COMPONENT) return;
        setProgress(p);
        if (p.phase === "error" || p.phase === "cancelled") setError(p.error);
        if (p.phase === "done" || p.phase === "error" || p.phase === "cancelled") void refresh();
      }),
      onAsrInstallProgress((p) => {
        if (p.done) {
          setInstallStep(null);
          if (p.error) setError(p.error);
          else onReady();
        } else {
          setInstallStep(p.step);
        }
      }),
    ];
    return () => unsubs.forEach((u) => void u.then((f) => f()));
  }, [refresh, onReady]);

  async function run(fn: () => Promise<void>) {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(String(e));
    }
  }

  const busy = comp?.busy || (progress && (progress.phase === "downloading" || progress.phase === "verifying"));
  const ready = comp?.status.state === "ready" || status.pack_ready;

  let body;
  if (installStep) {
    body = <div>正在安装: {INSTALL_STEP[installStep] ?? installStep}…</div>;
  } else if (ready) {
    body = (
      <>
        <div>会议组件包已下载并校验 ({comp?.version ?? ""})。</div>
        <Btn kind="primary" onClick={() => void run(meetingAsrInstall)}>安装</Btn>
      </>
    );
  } else if (busy && progress) {
    const pct = progress.total ? Math.floor((progress.downloaded / progress.total) * 100) : 0;
    body = (
      <>
        <div>
          {progress.phase === "verifying" ? "校验中…" : `下载中 ${mb(progress.downloaded)} / ${mb(progress.total)} (${pct}%)`}
        </div>
        <div style={{ height: 6, background: "var(--catfish-bg-cream)", borderRadius: 3 }}>
          <div style={{ width: `${pct}%`, height: "100%", background: "var(--catfish-cyan)", borderRadius: 3 }} />
        </div>
        <Btn kind="ghost" onClick={() => void run(() => componentsCancel(COMPONENT))}>暂停 (下次接着下)</Btn>
      </>
    );
  } else if (comp?.status.state === "unverified") {
    body = (
      <>
        <div>发现 IT 放好的组件包, 先校验完整性。</div>
        <Btn kind="primary" onClick={() => void run(() => componentsVerify(COMPONENT))}>校验</Btn>
      </>
    );
  } else if (comp) {
    const partial = comp.status.state === "partial" ? comp.status.downloaded : 0;
    body = (
      <>
        <div>
          会议转写需要会议组件包 ({mb(comp.size)}, 版本 {comp.version})。只下载一次, 转写全程在本机进行, 录音不上传。
        </div>
        <Btn kind="primary" onClick={() => void run(() => componentsDownload(COMPONENT))}>
          {partial ? `继续下载 (已下 ${mb(partial)})` : "下载"}
        </Btn>
      </>
    );
  } else {
    body = (
      <div>
        中央服务器还没有发布会议组件包{listError ? "" : ` (本机平台)`}, 请联系 IT。
        {listError && <ErrorLine>{listError}</ErrorLine>}
      </div>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
        fontSize: 13,
        padding: "var(--space-3)",
        border: "1px dashed var(--catfish-border-strong)",
        borderRadius: "var(--radius-sm)",
      }}
    >
      {body}
      {error && <ErrorLine>{error}</ErrorLine>}
    </div>
  );
}
