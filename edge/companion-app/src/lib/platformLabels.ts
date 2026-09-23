/** 界面上跟平台有关的叫法 —— 一处定义, 别在组件里写死 (9/23).
 *
 * 9/23 盘 Windows 缺口时, 员工能看到的文案里写死了 "在 Finder 显示" / "⌘ + Enter"
 * / "⌘+Shift+D" 七处。Windows 员工看到 Finder 不知道是什么, 按 ⌘ 按不出来。
 * 平台判断复用 emailPlatformHints 那套 (userAgent), 不再造一份。
 */
import { currentEmailPlatform, type EmailPlatform } from "./emailPlatformHints";

export function isMacPlatform(p: EmailPlatform = currentEmailPlatform()): boolean {
  return p === "macos";
}

/** 修饰键: mac ⌘, 其它 Ctrl */
export function modKey(p: EmailPlatform = currentEmailPlatform()): string {
  return isMacPlatform(p) ? "⌘" : "Ctrl";
}

/** 文件管理器: mac Finder, Windows 资源管理器 */
export function fileManagerName(p: EmailPlatform = currentEmailPlatform()): string {
  if (p === "macos") return "Finder";
  if (p === "windows") return "资源管理器";
  return "文件管理器";
}
