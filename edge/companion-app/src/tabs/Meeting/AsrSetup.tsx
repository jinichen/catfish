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
import {
  meetingAsrInstall, meetingHqInstall, onAsrInstallProgress, onHqInstallProgress, type AsrStatus,
} from "../../lib/tauri_meeting";
import CentralComponentMissing from "../../components/CentralComponentMissing";
import { Btn, cardStyle, ErrorLine } from "../Collab/roomLinkUi";
import { COL_CARD } from "./meetingHelpers";

const COMPONENT = "meeting-asr";
/** 11/17 高精度转写组件 (Qwen3-ASR): 同一套下载 → 校验 → 安装, 只是组件名 / 安装命令 / 说明不同 */
const KINDS = {
  base: { component: COMPONENT, install: meetingAsrInstall, onProgress: onAsrInstallProgress, what: "会议组件包" },
  hq: { component: "meeting-asr-hq", install: meetingHqInstall, onProgress: onHqInstallProgress, what: "高精度转写组件" },
} as const;

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

/** 版本号比大小: "2.0.0" > "1.10.0" > "1.9.0" */
export function newerVersion(a: string, b: string): boolean {
  const pa = a.split(".").map((x) => Number(x) || 0);
  const pb = b.split(".").map((x) => Number(x) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    if ((pa[i] ?? 0) !== (pb[i] ?? 0)) return (pa[i] ?? 0) > (pb[i] ?? 0);
  }
  return false;
}

/** 11/17: 已经装了会议组件、中央有更新的版本 → 升级卡。原来只有「没装」时才有下载入口, 装过的永远停在老版本
 *  (鸿波这台中央已有 1.1.0, 本机还是 1.0.0)。中央没有更新的版本时什么都不显示。 */
export function AsrUpgrade({ status, onReady }: { status: AsrStatus; onReady: () => void }) {
  const [latest, setLatest] = useState<string | null>(null);
  useEffect(() => {
    componentsList()
      .then((l) => setLatest(l.components.find((c) => c.name === COMPONENT)?.version ?? null))
      .catch(() => setLatest(null));
  }, [status.installed?.version]);
  const from = status.installed?.version;
  if (!from || !latest || !newerVersion(latest, from)) return null;
  return (
    <div style={{ ...cardStyle, ...COL_CARD }}>
      <AsrSetup status={status} onReady={onReady} upgradeFrom={from} />
    </div>
  );
}

export default function AsrSetup({ status, onReady, upgradeFrom, kind = "base" }: {
  status: Pick<AsrStatus, "pack_ready" | "installing"> & Partial<Pick<AsrStatus, "transcribing">>;
  onReady: () => void; upgradeFrom?: string; kind?: keyof typeof KINDS;
}) {
  const K = KINDS[kind];
  const [comp, setComp] = useState<ComponentInfo | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [progress, setProgress] = useState<ComponentProgress | null>(null);
  const [installStep, setInstallStep] = useState<string | null>(status.installing ? "installing" : null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await componentsList();
      setComp(list.components.find((c) => c.name === K.component) ?? null);
      setListError(null);
    } catch (e) {
      setListError(String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const unsubs = [
      onComponentProgress((p) => {
        if (p.name !== K.component) return;
        setProgress(p);
        if (p.phase === "error" || p.phase === "cancelled") setError(p.error);
        if (p.phase === "done" || p.phase === "error" || p.phase === "cancelled") void refresh();
      }),
      K.onProgress((p) => {
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
  }, [refresh, onReady, K]);

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
        <div>{K.what}已下载并校验 ({comp?.version ?? ""})。{upgradeFrom ? " 装的时候先别转写 (约一两分钟)。" : ""}</div>
        <Btn kind="primary" disabled={!!status.transcribing} onClick={() => void run(K.install)}>
          {upgradeFrom ? (status.transcribing ? "等转写完再装" : "安装新版") : "安装"}
        </Btn>
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
        <Btn kind="ghost" onClick={() => void run(() => componentsCancel(K.component))}>暂停 (下次接着下)</Btn>
      </>
    );
  } else if (comp?.status.state === "unverified") {
    body = (
      <>
        <div>发现 IT 放好的组件包, 先校验完整性。</div>
        <Btn kind="primary" onClick={() => void run(() => componentsVerify(K.component))}>校验</Btn>
      </>
    );
  } else if (comp) {
    const partial = comp.status.state === "partial" ? comp.status.downloaded : 0;
    body = (
      <>
        <div>
          {kind === "hq"
            ? <>高精度转写组件 ({mb(comp.size)}, 版本 {comp.version}): 用 Qwen3-ASR 重新识别已转写的会议, 说得清楚的地方明显更准
                (远场 / 很小声的照样认不出); 比普通转写慢 (两小时的会 Mac 约 40 分钟, Windows 约 80 分钟), 运行时占 4GB 左右内存。只下载一次,
                全程在本机, 录音不上传。</>
            : upgradeFrom
              ? <>会议组件有新版 {comp.version} (你现在是 {upgradeFrom})。下载 {mb(comp.size)}, 转写仍然全程在本机, 录音不上传。</>
              : <>会议转写需要会议组件包 ({mb(comp.size)}, 版本 {comp.version})。只下载一次, 转写全程在本机进行, 录音不上传。</>}
        </div>
        <Btn kind="primary" onClick={() => void run(() => componentsDownload(K.component))}>
          {partial ? `继续下载 (已下 ${mb(partial)})` : upgradeFrom ? "下载新版" : "下载"}
        </Btn>
      </>
    );
  } else {
    body = <CentralComponentMissing what={K.what} listError={listError} onRetry={() => void refresh()} />;
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
