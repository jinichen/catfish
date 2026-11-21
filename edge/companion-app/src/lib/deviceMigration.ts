/** 换机迁移 (BL-L9, docs/DEVICE-MIGRATION-DESIGN.md): Rust 端 commands/device_migration 的包装。
 *
 * 迁移包不经过中央: 旧机导出成一个口令加密的文件, 员工自己带到新机导入。凭证刻意不带。
 */
import { invoke } from "@tauri-apps/api/core";

export type MigrationClass = "required" | "optional" | "merge" | "rebuild" | "secret" | "unclassified";

export interface MigrationEntry {
  root: "catfish" | "hermes";
  name: string;
  class: MigrationClass;
  label: string;
  files: number;
  bytes: number;
}

export interface MigrationScan {
  entries: MigrationEntry[];
  unclassified: string[];
  requiredBytes: number;
}

export interface MigrationExportReport {
  path: string;
  bytes: number;
  files: number;
  unclassified: string[];
}

export interface MigrationInspectReport {
  createdAt: string;
  sourceOs: string;
  userEmail: string;
  companionVersion: string;
  files: number;
  bytes: number;
  entries: MigrationEntry[];
  withFiles: boolean;
  samePerson: boolean;
  loggedInAs: string;
  versionOk: boolean;
  targetHasData: boolean;
}

export interface MigrationImportReport {
  files: number;
  bytes: number;
  pathsRewritten: number;
  keptExisting: number;
  backupDir: string | null;
  redo: string[];
  warnings: string[];
}

export const entryKey = (e: MigrationEntry) => `${e.root}/${e.name}`;

export const migrationScan = () => invoke<MigrationScan>("migration_scan");
export const migrationPickSave = (defaultName: string) => invoke<string | null>("migration_pick_save", { defaultName });
export const migrationPickOpen = () => invoke<string | null>("migration_pick_open");
export const migrationExport = (dest: string, passphrase: string, optional: string[]) =>
  invoke<MigrationExportReport>("migration_export", { dest, passphrase, optional });
export const migrationInspect = (path: string, passphrase: string) =>
  invoke<MigrationInspectReport>("migration_inspect", { path, passphrase });
export const migrationImport = (path: string, passphrase: string, overwrite: boolean) =>
  invoke<MigrationImportReport>("migration_import", { path, passphrase, overwrite });
export const migrationRestartApp = () => invoke<void>("migration_restart_app");

export function formatBytes(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`;
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${n} B`;
}

/** 口令强度提示 (不拦, 只提醒; 至少 8 位由 Rust 端强制) */
export function passphraseHint(p: string): string | null {
  if (p.length === 0) return null;
  if (p.length < 8) return "至少 8 位";
  if (p.length < 12 || /^\d+$/.test(p)) return "口令偏弱: 建议 12 位以上、混用字母和数字";
  return null;
}

// ── 11/21 扫码直传 (docs/DEVICE-MIGRATION-DESIGN.md §12) ──────────────
// 新电脑出二维码 (一次性公钥) → 旧电脑扫码 / 手输配对码 → 迁移包用那把公钥加密, 经中央边传边删 → 新电脑收齐导入。

export type DirectRecvState = "waiting" | "receiving" | "verifying" | "ready" | "failed" | "cancelled";

export interface DirectRecvStatus {
  state: DirectRecvState;
  id: string;
  code: string;
  qr: string;
  safety: string;
  toDevice: string;
  fromDevice: string;
  got: number;
  n: number;
  size: number;
  error: string | null;
  info: MigrationInspectReport | null;
}

export interface DirectTarget {
  id: string;
  recipient: string;
  toDevice: string;
  safety: string;
}

export type DirectSendState = "packing" | "sending" | "finishing" | "done" | "failed" | "cancelled";

export interface DirectSendStatus {
  state: DirectSendState;
  id: string;
  toDevice: string;
  size: number;
  n: number;
  sent: number;
  acked: number;
  files: number;
  error: string | null;
}

export const directReceiveStart = () => invoke<DirectRecvStatus>("migration_direct_receive_start");
export const directReceiveStatus = () => invoke<DirectRecvStatus | null>("migration_direct_receive_status");
export const directReceiveCancel = () => invoke<void>("migration_direct_receive_cancel");
export const directImport = (overwrite: boolean) => invoke<MigrationImportReport>("migration_direct_import", { overwrite });
export const directLookup = (code: string) => invoke<DirectTarget>("migration_direct_lookup", { code });
export const directFromQr = (text: string) => invoke<DirectTarget>("migration_direct_from_qr", { text });
export const directSend = (target: DirectTarget, optional: string[]) =>
  invoke<DirectSendStatus>("migration_direct_send", { target, optional });
export const directSendStatus = () => invoke<DirectSendStatus | null>("migration_direct_send_status");
export const directSendCancel = () => invoke<void>("migration_direct_send_cancel");

/** 8 位配对码: 去掉空格 / 横线, 转大写, 每 4 位一组 (「ABCD-EFGH」); 不够 8 位原样返回 (还在输) */
export function normalizeCode(s: string): string {
  const raw = s.replace(/[\s-]/g, "").toUpperCase().slice(0, 8);
  return raw.length > 4 ? `${raw.slice(0, 4)}-${raw.slice(4)}` : raw;
}

/** 进度百分比 (0–100, 没开始 = 0) */
export function percent(done: number, total: number): number {
  return total > 0 ? Math.min(100, Math.floor((done * 100) / total)) : 0;
}

// ── 11/21 误删保护 (docs/DEVICE-MIGRATION-DESIGN.md §13) ──────────────

export interface SnapshotSettings {
  enabled: boolean;
  keep: number;
  lastError: string | null;
  lastTryAt: string | null;
}

export interface SnapshotInfo {
  name: string;
  createdAt: string;
  bytes: number;
  files: number;
  companionVersion: string;
  entries: MigrationEntry[];
}

export interface SnapshotsStatus {
  settings: SnapshotSettings;
  snapshots: SnapshotInfo[];
  dir: string;
  busy: boolean;
}

export const snapshotsStatus = () => invoke<SnapshotsStatus>("snapshots_status");
export const snapshotsSet = (enabled: boolean, keep: number) => invoke<SnapshotsStatus>("snapshots_set", { enabled, keep });
export const snapshotsMakeNow = () => invoke<SnapshotInfo>("snapshots_make_now");
export const snapshotsRestore = (name: string, only: string[]) =>
  invoke<MigrationImportReport>("snapshots_restore", { name, only });

/** 外发记录 / 审计类不还原 (只追加的链, 退回去会被当成被人改过) —— 跟 Rust snapshots::restorable 一致 */
export function restorable(e: MigrationEntry): boolean {
  const n = e.name.toLowerCase();
  return !(n.startsWith("outbound_log") || n.includes("audit"));
}
