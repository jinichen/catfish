/**
 * 唯一的 invoke 出口。
 *
 * 任何前端文件需要调 Rust 都从这里 import，绝不直接 `import { invoke } from '@tauri-apps/api'`。
 * 这样：
 *   1. 命令名集中在一处，重命名一次性改完
 *   2. 可以加统一的错误处理 / 日志
 *   3. 类型签名集中，对照 Rust 端 #[tauri::command] 一目了然
 *
 * Rust 命令名和这里的 invoke 字符串必须严格对齐 —— Tauri 的命令名是全局唯一的，
 * 所以三个服务的 start/stop/status 用了 `<service>_<op>` 全限定命名。
 *
 * ── 2026-08-15: 这个文件本身拆成了 7 个域文件, 这里只剩 re-export ──
 *
 * 1309 行超限。边界照抄原文件里已有的 38 个 `// ── xxx ──` 分节, 归成 7 个域,
 * 逻辑一行未改。62 个调用方 import 的 92 个符号全部仍从 "lib/tauri" 可达,
 * 所以那 62 个文件一行没动 —— 下面每条 export 就写着东西搬去了哪儿。
 *
 * ⚠ 上面第 4 行那条规矩**现在并不成立**: 实测有 20+ 个前端文件直接
 *   `from "@tauri-apps/api"`。规矩写在这儿, 但没有任何东西在守它。
 *   要么补一条 eslint no-restricted-imports 让它真生效, 要么把这行删掉别再骗人。
 *   这次是纯搬迁, 不在这里动它, 另记。
 */

export * from "./tauri_services";
export * from "./tauri_sessions";
export * from "./tauri_wiki";
export * from "./wikiActions";
export * from "./tauri_compliance";
export * from "./tauri_briefing";
export * from "./tauri_cron";
export * from "./tauri_app";
