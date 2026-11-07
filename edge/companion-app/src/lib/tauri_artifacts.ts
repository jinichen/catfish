/** 11/07 对话右侧成果区 —— 后端 src-tauri/src/commands/artifacts.rs */
import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import type { ArtifactKind } from "./artifactKinds";

export interface ArtifactInfo {
  path: string;
  name: string;
  ext: string;
  kind: ArtifactKind;
  size: number;
  mtime_ms: number;
}

export interface ArtifactText {
  text: string;
  truncated: boolean;
  size: number;
}

export type OfficePreview =
  | { kind: "docx"; truncated: boolean; blocks: DocxBlock[] }
  | { kind: "sheet"; truncated: boolean; sheets: { name: string; rows: string[][]; total_rows: number; total_cols: number }[] }
  | { kind: "slides"; truncated: boolean; slides: { title: string; texts: string[]; images: string[] }[] };

export type DocxBlock =
  | { t: "h"; level: number; text: string }
  | { t: "p"; text: string; list: boolean }
  | { t: "table"; rows: string[][] }
  | { t: "img"; src: string };

/** 文件信息; 网页 / PDF / 图片顺带在后端放行这一个文件给 asset 协议。 */
export function artifactStat(path: string): Promise<ArtifactInfo> {
  return invoke<ArtifactInfo>("artifact_stat", { path });
}

/** 字节 → 文字: 先按 UTF-8 严格解, 解不了按 GB18030 (Windows 上 Excel 另存的 CSV 多是 GBK)。 */
export function decodeBytes(bytes: Uint8Array): string {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes).replace(/^﻿/, "");
  } catch {
    return new TextDecoder("gb18030").decode(bytes);
  }
}

export async function artifactReadText(path: string): Promise<ArtifactText> {
  const r = await invoke<{ b64: string; truncated: boolean; size: number }>("artifact_read_text", { path });
  const bin = atob(r.b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  // 截在 2MB 处可能切断一个多字节字符 —— 截断时去掉末尾可能的半个字
  let text = decodeBytes(r.truncated ? trimPartialUtf8(bytes) : bytes);
  if (r.truncated) text = text.replace(/�+$/, "");
  return { text, truncated: r.truncated, size: r.size };
}

/** UTF-8 末尾不完整的字节去掉 (最多 3 个), 免得严格解码因为截断就退回 GB18030。 */
export function trimPartialUtf8(bytes: Uint8Array): Uint8Array {
  let end = bytes.length;
  for (let back = 1; back <= 3 && end - back >= 0; back++) {
    const b = bytes[end - back];
    if ((b & 0xc0) === 0x80) continue; // 续字节, 再往前看
    const need = b >= 0xf0 ? 4 : b >= 0xe0 ? 3 : b >= 0xc0 ? 2 : 1;
    return need > back ? bytes.subarray(0, end - back) : bytes;
  }
  return bytes;
}

export function artifactOfficePreview(path: string): Promise<OfficePreview> {
  return invoke<OfficePreview>("artifact_office_preview", { path });
}

/**
 * 给 iframe / img 用的地址; 带上修改时间, 文件改了地址就变, 不吃缓存。
 *
 * 不直接用 convertFileSrc(path): 它把整条路径编码成一段 (asset://localhost/%2FUsers%2Fx%2Fa.html),
 * 网页里写的相对地址 (旁边的 chart.png) 会解析到 asset://localhost/chart.png, 图全挂 (11/07 在真 WKWebView 里试出来的)。
 * 这里一段一段编码, 保留目录层级: asset://localhost/%2FUsers/x/a.html —— 后端去掉开头的 / 再解码, 还是 /Users/x/a.html;
 * Windows: http://asset.localhost/C%3A/Users/x/a.html → C:/Users/x/a.html (tauri 比对前会规范化路径)。
 */
export function artifactUrl(info: ArtifactInfo): string {
  return `${assetUrlKeepingDirs(info.path)}?v=${info.mtime_ms}`;
}

export function assetUrlKeepingDirs(path: string): string {
  const base = convertFileSrc(""); // 平台前缀: asset://localhost/ 或 http://asset.localhost/
  const parts = path.split(/[\\/]/);
  const encoded = parts[0] === ""
    ? "%2F" + parts.slice(1).map(encodeURIComponent).join("/")
    : parts.map(encodeURIComponent).join("/");
  return base + encoded;
}

/** 小鲶的浏览器 (鲶鱼 Chrome) 的浏览器级调试地址; 没开着会报「小鲶的浏览器还没打开」 */
export function browserViewEndpoint(): Promise<string> {
  return invoke<string>("browser_view_endpoint");
}
