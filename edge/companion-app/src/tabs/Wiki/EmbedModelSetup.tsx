/**
 * 语义检索用的向量模型 (BGE-M3 ONNX, ~560MB) 的下载 / 安装卡片 —— 10/6 鸿波
 * 「仿会议一样, 检查 embedding 模型是否存在, 不存在就支持从中央端下载」。
 *
 * 跟 Meeting/AsrSetup 同一条链: 中央 /components/ 发布 embed-model-<版本>-any.tar.gz
 * → lib/tauri_components 下载 + sha256 校验 → embed_model_install 解包到
 * ~/.catfish/models/。装完不用重启 Companion, 下一次搜索自己加载。
 *
 * 只在 provider 没准备好时显示 (WikiTree 决定)。Intel Mac 没有本地 ONNX 运行时
 * (ort 不发那个目标的预编译包), 装了也没人用 —— 那种机器只说明"要靠中央网关",
 * 不给下载按钮。Windows 用 windows-x64 包 (模型 + onnxruntime.dll)。
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
  EMBED_MODEL_COMPONENT,
  embedModelInstall,
  onEmbedModelInstallProgress,
  type EmbedModelStatus,
} from "../../lib/tauri_embed_model";
import { Btn, ErrorLine } from "../Collab/roomLinkUi";

const INSTALL_STEP: Record<string, string> = {
  unpacking: "解包",
  placing: "放到模型目录",
  finishing: "收尾",
};

function mb(n: number): string {
  return `${(n / 1e6).toFixed(0)} MB`;
}

export default function EmbedModelSetup({ status, onReady }: { status: EmbedModelStatus; onReady: () => void }) {
  const [comp, setComp] = useState<ComponentInfo | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [progress, setProgress] = useState<ComponentProgress | null>(null);
  const [installStep, setInstallStep] = useState<string | null>(status.installing ? "unpacking" : null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await componentsList();
      setComp(list.components.find((c) => c.name === EMBED_MODEL_COMPONENT) ?? null);
      setListError(null);
    } catch (e) {
      setListError(String(e));
    }
  }, []);

  useEffect(() => {
    if (!status.local_supported) return;
    void refresh();
    const unsubs = [
      onComponentProgress((p) => {
        if (p.name !== EMBED_MODEL_COMPONENT) return;
        setProgress(p);
        if (p.phase === "error" || p.phase === "cancelled") setError(p.error);
        if (p.phase === "done" || p.phase === "error" || p.phase === "cancelled") void refresh();
      }),
      onEmbedModelInstallProgress((p) => {
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
  }, [refresh, onReady, status.local_supported]);

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
  if (!status.local_supported) {
    body = (
      <div>
        这台电脑 (Intel 芯片 Mac) 的安装包没有本地向量运行时, 语义检索要靠中央网关的向量服务。现在网关不可用:
        <ErrorLine>{status.not_ready_reason ?? "未知原因"}</ErrorLine>
      </div>
    );
  } else if (installStep) {
    body = <div>正在安装: {INSTALL_STEP[installStep] ?? installStep}…</div>;
  } else if (ready) {
    body = (
      <>
        <div>向量模型包已下载并校验 ({comp?.version ?? ""})。</div>
        <Btn kind="primary" onClick={() => void run(embedModelInstall)}>安装</Btn>
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
        <Btn kind="ghost" onClick={() => void run(() => componentsCancel(EMBED_MODEL_COMPONENT))}>暂停 (下次接着下)</Btn>
      </>
    );
  } else if (comp?.status.state === "unverified") {
    body = (
      <>
        <div>发现 IT 放好的向量模型包, 先校验完整性。</div>
        <Btn kind="primary" onClick={() => void run(() => componentsVerify(EMBED_MODEL_COMPONENT))}>校验</Btn>
      </>
    );
  } else if (comp) {
    const partial = comp.status.state === "partial" ? comp.status.downloaded : 0;
    body = (
      <>
        <div>
          语义检索需要本机向量模型 BGE-M3 ({mb(comp.size)}, 版本 {comp.version})。只下载一次, 之后搜索全在本机算, 内容不上传。
        </div>
        <Btn kind="primary" onClick={() => void run(() => componentsDownload(EMBED_MODEL_COMPONENT))}>
          {partial ? `继续下载 (已下 ${mb(partial)})` : "下载"}
        </Btn>
      </>
    );
  } else {
    body = (
      <div>
        本机向量模型没装, 中央服务器也还没有发布向量模型包, 请联系 IT (或按下面的提示手动放文件)。
        {listError && <ErrorLine>{listError}</ErrorLine>}
        {status.not_ready_reason && (
          <pre className="wiki-semantic-msg--mono" style={{ whiteSpace: "pre-wrap", marginTop: "var(--space-2)" }}>
            {status.not_ready_reason}
          </pre>
        )}
      </div>
    );
  }

  return (
    <div
      data-testid="embed-model-setup"
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
