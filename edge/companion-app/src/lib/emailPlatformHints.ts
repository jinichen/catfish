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
  if (platform === "windows") return "Outlook / 导出的邮件目录";
  if (platform === "macos") return "Mail.app";
  return "系统邮件客户端";
}

export function emailFailureHint(platform = currentEmailPlatform()): string {
  if (platform === "windows") {
    return "常见原因：还没有选择邮件目录，或选的目录里没有 .eml 文件。请先在邮件客户端里把邮件导出为 .eml（Foxmail：选中邮件右键「另存为」），再回来选择导出目录。也可以在 %USERPROFILE%\\.catfish\\companion.yaml 的 email.mail_dir 里直接写目录。日志位于 %LOCALAPPDATA%\\hermes\\logs\\catfish-companion-bootstrap.log。";
  }
  if (platform === "macos") {
    return "常见原因：Mail.app 没打开，或系统没有给鲶鱼 Companion 自动化权限（系统设置 → 隐私与安全性 → 自动化 → 邮件）。若仍失败，打开仪表盘点“重新安装 Hermes”，然后重启鲶鱼 Companion。";
  }
  return "请先启动系统邮件客户端并确认已配置账号；若仍失败，打开仪表盘点“重新安装 Hermes”，然后重启鲶鱼 Companion。";
}
