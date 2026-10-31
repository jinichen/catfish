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
