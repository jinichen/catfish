/**
 * 可选大组件 (会议纪要组件包等) —— 从中央 /components/ 按需下载 (10/1)。
 *
 * Rust: src-tauri/src/commands/components.rs, 逻辑在 services/components.rs。
 * 设计: docs/MEETING-MINUTES-PLAN.md §4。
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export type ComponentLocalStatus =
  | { state: "ready" }
  | { state: "partial"; downloaded: number }
  /** 文件在但没校验过 (IT 手动放进 ~/.catfish/runtime/ 的), 先调 componentsVerify */
  | { state: "unverified" }
  | { state: "missing" };

export interface ComponentInfo {
  name: string;
  version: string;
  platform: string;
  file: string;
  size: number;
  sha256: string;
  status: ComponentLocalStatus;
  /** 正在下载 / 校验 */
  busy: boolean;
}

export interface ComponentsList {
  platform: string;
  components: ComponentInfo[];
}

export type ComponentPhase = "downloading" | "verifying" | "done" | "error" | "cancelled";

export interface ComponentProgress {
  name: string;
  phase: ComponentPhase;
  downloaded: number;
  total: number;
  error: string | null;
}

export const componentsList = () => rawInvoke<ComponentsList>("components_list");

/** 立即返回, 进度走 onComponentProgress。中断后再调一次会接着下。 */
export const componentsDownload = (name: string) =>
  rawInvoke<void>("components_download", { name });

export const componentsVerify = (name: string) =>
  rawInvoke<void>("components_verify", { name });

export const componentsCancel = (name: string) =>
  rawInvoke<void>("components_cancel", { name });

export const onComponentProgress = (
  cb: (p: ComponentProgress) => void,
): Promise<UnlistenFn> => listen<ComponentProgress>("component_progress", (e) => cb(e.payload));
