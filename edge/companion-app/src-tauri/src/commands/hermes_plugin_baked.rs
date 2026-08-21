//! catfish-xcatfish-user 插件的 baked 文件表 (8/21 从 hermes_plugin.rs 纯搬迁)。
//!
//! 搬迁原因: 8/21 补 9 个漏掉的 sibling 后 hermes_plugin.rs 越过 800 行红线。
//! 逻辑一行未改; include_str! 相对路径不变 (同在 src/commands/ 目录)。
//! 「拆出新 sibling 必须同时加进 BAKED_FILES」的守护测试仍在
//! hermes_plugin.rs::tests::baked_files_覆盖仓库里所有_py。

// ─────────────────────────────────────────────
//
// include_str!() 编译时把 plugin 的全部 .py + 1 个 .yaml 嵌进 binary. 路径相对本 .rs:
//   edge/companion-app/src-tauri/src/commands/hermes_plugin.rs
//   → ../../../../hermes-plugins/catfish-xcatfish-user/<file>
//
// ⚠⚠ 8/9 P0: **拆出新的 sibling 模块, 必须同时加到下面这张表。**
//
// 事故: plugin.py 按军规拆分协议抽出了 plugin_weixin_zh / plugin_wechat_qr /
// plugin_memory_gate 三个 sibling, 并在 plugin.py **模块级**做
// `_import_sibling("plugin_weixin_zh")` re-export。但这张烘焙表没跟着加。
//
// 后果不是"新功能不生效", 是**整个 plugin 死掉**:
//   1. Companion 启动同步 baked → ~/.hermes/plugins/ (只写表里这几个文件)
//   2. hermes 加载 plugin → plugin.py 模块级 _import_sibling("plugin_weixin_zh")
//   3. 三段 fallback 全落空 → `raise ImportError("plugin_weixin_zh.py 不存在")`
//   4. plugin 加载失败 → **P1-P11 一个都没打上**
//      (X-Catfish-User 多租户注入 / P7 proxy / CORS / picker model override)
//
// 8/9 在鸿波本机实测: ~/.hermes/plugins/catfish-xcatfish-user/ 只有 8 个 .py,
// 直接 exec_module 那份已装的 plugin.py → ImportError。也就是说 8/8 19:11
// 那次同步之后, 这个 plugin 一直是死的。
//
// **这条比"漏个文件"严重的地方在于失败方向**: 拆分协议本身是对的 (re-export
// 保 import 兼容), 但这个 plugin 多一条隐藏要求 —— sibling 还得进二进制。
// 拆的人不会想到要来改 Rust。所以下面加了 tests::baked_files_覆盖仓库里所有_py
// 把这条钉死: 仓库里多一个 .py 而表里没有 → cargo test 红。
//
// 编译时若 source 缺 (catfish 仓库不全) → cargo build 报错早发现.
const BAKED_INIT: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/__init__.py");
const BAKED_PLUGIN: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin.py");
const BAKED_PLUGIN_YAML: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin.yaml");
const BAKED_RESOLVER: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/resolver.py");
const BAKED_SESSION_REGISTRY: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/session_registry.py");
const BAKED_SESSION_SEARCH_ROUTER: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/session_search_router.py");
const BAKED_MEMORY_ROUTER: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/memory_router.py");
const BAKED_MEMORY_ENFORCE: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/memory_enforce.py");
const BAKED_HERMES_TOKEN_RENEWAL: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/hermes_token_renewal.py");
// ── 8/9 补: plugin.py 拆出来的 sibling, 之前漏了 (见上面 P0 说明) ──
const BAKED_PLUGIN_WEIXIN_ZH: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_weixin_zh.py");
const BAKED_PLUGIN_WECHAT_QR: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_wechat_qr.py");
const BAKED_PLUGIN_MEMORY_GATE: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_memory_gate.py");
const BAKED_ACTIVITY_PROBE: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/activity_probe.py");
const BAKED_MODEL_AUTHORITY: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/model_authority.py");
const BAKED_ROUTE_AUTH: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_route_auth.py");

// ── 8/15 补: 8/13 加进仓库的 4 个 .py, BAKED_FILES 没跟上 ──
//
// 前三个是 plugin.py 顶层 `_import_sibling(...)` 的对象 (180 / 197 / 212 行)。
// 不在这张表里 = Companion 同步时不写它们 = 那三行当场 ImportError = 整个
// plugin 加载失败。**只靠 Companion 同步拿 plugin 的机器 (也就是新装机) 全中**;
// 开发机因为 deploy.sh 软链到仓库目录, 文件一直都在, 所以看不出来。
//
// approvals_bridge.py (P47) 眼下没有任何人 import —— 它的 register_routes()
// 没有调用方。留着烤进去而不是删掉, 是因为它 8/13 那次才刚从"只存在于运行
// 目录、换台机器就没了"的状态被抢救回仓库; 现在删等于把抢救回来的再丢一次。
// 它没被接上这件事另记, 不在这个 commit 里动。
const BAKED_PLUGIN_CORE_TOOLS: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_core_tools.py");
const BAKED_PLUGIN_CODEX_SESSION: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_codex_session.py");
const BAKED_PLUGIN_CRON: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_cron.py");
const BAKED_APPROVALS_BRIDGE: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/approvals_bridge.py");

// ── 8/21 补: 8/15 大拆分 (plugin.py 拆成 plugin_approval / plugin_cors /
// plugin_ctx / plugin_misc / plugin_runtime / plugin_service_lean /
// plugin_session / plugin_verify) + 8/19 P45 (plugin_deferred_tool_guard),
// 九个文件 BAKED_FILES 都没跟上 ——
// 又一次「开发机软链看不出来, 新装机 ImportError 全中」, 跟上面 8/15 那段
// 是同一种病。这次是员工机 cargo test 守护测试抓红的
// (baked_files_覆盖仓库里所有_py), 不是靠人想起来。
const BAKED_PLUGIN_APPROVAL: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_approval.py");
const BAKED_PLUGIN_CORS: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_cors.py");
const BAKED_PLUGIN_CTX: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_ctx.py");
const BAKED_PLUGIN_DEFERRED_TOOL_GUARD: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_deferred_tool_guard.py");
const BAKED_PLUGIN_MISC: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_misc.py");
const BAKED_PLUGIN_RUNTIME: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_runtime.py");
const BAKED_PLUGIN_SERVICE_LEAN: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_service_lean.py");
const BAKED_PLUGIN_SESSION: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_session.py");
const BAKED_PLUGIN_VERIFY: &str =
    include_str!("../../../../hermes-plugins/catfish-xcatfish-user/plugin_verify.py");

/// Plugin 的全部文件 (filename, baked content).
///
/// 加新文件到 `edge/hermes-plugins/catfish-xcatfish-user/*.py` 就必须加这里,
/// 由 `tests::baked_files_覆盖仓库里所有_py` 守着。
pub(crate) const BAKED_FILES: &[(&str, &str)] = &[
    ("__init__.py", BAKED_INIT),
    ("plugin.py", BAKED_PLUGIN),
    ("plugin.yaml", BAKED_PLUGIN_YAML),
    ("resolver.py", BAKED_RESOLVER),
    ("session_registry.py", BAKED_SESSION_REGISTRY),
    ("session_search_router.py", BAKED_SESSION_SEARCH_ROUTER),
    ("memory_router.py", BAKED_MEMORY_ROUTER),
    ("memory_enforce.py", BAKED_MEMORY_ENFORCE),
    ("hermes_token_renewal.py", BAKED_HERMES_TOKEN_RENEWAL),
    ("plugin_weixin_zh.py", BAKED_PLUGIN_WEIXIN_ZH),
    ("plugin_wechat_qr.py", BAKED_PLUGIN_WECHAT_QR),
    ("plugin_memory_gate.py", BAKED_PLUGIN_MEMORY_GATE),
    ("activity_probe.py", BAKED_ACTIVITY_PROBE),
    ("model_authority.py", BAKED_MODEL_AUTHORITY),
    ("plugin_route_auth.py", BAKED_ROUTE_AUTH),
    ("plugin_core_tools.py", BAKED_PLUGIN_CORE_TOOLS),
    ("plugin_codex_session.py", BAKED_PLUGIN_CODEX_SESSION),
    ("plugin_cron.py", BAKED_PLUGIN_CRON),
    ("approvals_bridge.py", BAKED_APPROVALS_BRIDGE),
    // 8/21 补 (8/15 大拆分 + 8/19 P45, 见上面那段注释)
    ("plugin_approval.py", BAKED_PLUGIN_APPROVAL),
    ("plugin_cors.py", BAKED_PLUGIN_CORS),
    ("plugin_ctx.py", BAKED_PLUGIN_CTX),
    ("plugin_deferred_tool_guard.py", BAKED_PLUGIN_DEFERRED_TOOL_GUARD),
    ("plugin_misc.py", BAKED_PLUGIN_MISC),
    ("plugin_runtime.py", BAKED_PLUGIN_RUNTIME),
    ("plugin_service_lean.py", BAKED_PLUGIN_SERVICE_LEAN),
    ("plugin_session.py", BAKED_PLUGIN_SESSION),
    ("plugin_verify.py", BAKED_PLUGIN_VERIFY),
];
