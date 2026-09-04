/** Platform-specific guidance for the email integration. */

export type EmailPlatform = "macos" | "windows" | "other";

export function emailPlatformFromUserAgent(userAgent: string): EmailPlatform {
  const ua = userAgent.toLowerCase();
  if (ua.includes("windows")) return "windows";
  if (ua.includes("mac os") || ua.includes("macintosh")) return "macos";
  return "other";
}

export function currentEmailPlatform(): EmailPlatform {
  return typeof navigator === "undefined"
    ? "other"
    : emailPlatformFromUserAgent(navigator.userAgent);
}

export function emailClientName(platform = currentEmailPlatform()): string {
  if (platform === "windows") return "Outlook / Foxmail";
  if (platform === "macos") return "Mail.app";
  return "系统邮件客户端";
}

export function emailFailureHint(platform = currentEmailPlatform()): string {
  if (platform === "windows") {
    return "常见原因：Foxmail 尚未配置账号、Foxmail 尚未完成同步，或邮件组件尚未完成安装。鲶鱼会自动读取 Foxmail 参数和注册表寻找 Storage；如果仍找不到，再在 %USERPROFILE%\\.catfish\\companion.yaml 的 email.foxmail_root 配置真实目录。日志位于 %LOCALAPPDATA%\\hermes\\logs\\catfish-companion-bootstrap.log。";
  }
  if (platform === "macos") {
    return "常见原因：Mail.app 没打开，或系统没有给鲶鱼 Companion 自动化权限（系统设置 → 隐私与安全性 → 自动化 → 邮件）。若仍失败，打开仪表盘点“重新安装 Hermes”，然后重启鲶鱼 Companion。";
  }
  return "请先启动系统邮件客户端并确认已配置账号；若仍失败，打开仪表盘点“重新安装 Hermes”，然后重启鲶鱼 Companion。";
}
