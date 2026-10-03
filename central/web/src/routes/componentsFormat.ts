/** 组件页共用的格式化 (10/3)。 */
export const PLATFORM_LABEL: Record<string, string> = {
  "mac-arm64": "Mac (Apple 芯片)",
  "mac-x64": "Mac (Intel)",
  "windows-x64": "Windows x64",
  any: "通用 (所有平台)",
};

export function platformLabel(p: string): string {
  return PLATFORM_LABEL[p] ?? p;
}

export function formatSize(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  return `${Math.max(1, Math.round(bytes / 1e3))} KB`;
}
