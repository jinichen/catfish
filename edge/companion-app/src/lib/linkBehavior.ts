/** Link classification for the desktop shell and the browser preview. */

export function isExternalLink(href: string | undefined): boolean {
  if (!href) return false;
  try {
    const url = new URL(href, window.location.href);
    if (url.protocol !== "http:" && url.protocol !== "https:") return true;
    // localhost links are service/app addresses, not user-facing web pages.
    // Never let them reload the SPA or open a second browser page.
    if (["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)) return false;
    return url.origin !== window.location.origin;
  } catch {
    return false;
  }
}
