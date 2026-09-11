/** Runtime checks shared by components that have optional Tauri-only behavior. */

export function isTauriRuntime(): boolean {
  return typeof window !== "undefined" && Boolean(
    (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__,
  );
}
