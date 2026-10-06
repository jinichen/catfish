/** 向量模型组件包 (BGE-M3, 语义检索用) 的状态 / 安装 —— 10/6。
 *  下载 / 校验走 lib/tauri_components (组件名 embed-model, 平台 any)。 */
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { invoke as rawInvoke } from "@tauri-apps/api/core";

export const EMBED_MODEL_COMPONENT = "embed-model";

export interface EmbedModelInstalled {
  version: string | null;
  model_path: string;
  tokenizer_path: string;
  model_bytes: number;
}

export interface EmbedModelStatus {
  /** 本机架构带 ONNX 运行时 (Apple 芯片); false = 只能靠中央网关算向量 */
  local_supported: boolean;
  provider_ready: boolean;
  provider_remote: boolean;
  not_ready_reason: string | null;
  installed: EmbedModelInstalled | null;
  pack_ready: string | null;
  installing: boolean;
}

export interface EmbedModelInstallProgress {
  step: string;
  done: boolean;
  error?: string | null;
}

export const embedModelStatus = () => rawInvoke<EmbedModelStatus>("embed_model_status");
export const embedModelInstall = () => rawInvoke<void>("embed_model_install");
export const onEmbedModelInstallProgress = (cb: (p: EmbedModelInstallProgress) => void): Promise<UnlistenFn> =>
  listen<EmbedModelInstallProgress>("embed_model_progress", (e) => cb(e.payload));
