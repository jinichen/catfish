//! 10/27: 小鲶后台 (hermes 网关) 没在跑就拉起来 —— 从 hermes_plugin.rs 拆出来 (那边 800 行线)。
/// 10/27: 确认 hermes 网关真在跑, 没在跑就 `hermes gateway start` 拉一次。只在后台线程里调 (最多阻塞约 3 分钟)。
///
/// Windows 的 `hermes gateway restart` 是先停再起, 只等新网关 15 秒就报失败; 插件加载、MCP 发现慢的
/// 机器上新网关常常 15 秒还没好, 有时干脆没起来 —— 10/27 鸿波机器 14:51 重启后网关就一直停着,
/// Companion 只记了一行日志, 员工发消息没人回。他手动跑 `hermes gateway start` (直接拉起) 就好了,
/// 这里做同样的事。先给 90 秒 (正常起得慢), 还没通再拉, 再等 90 秒。
#[cfg(not(target_os = "macos"))]
pub(crate) fn ensure_gateway_up(when: &str) {
    use std::time::{Duration, Instant};
    let ready_within = |secs: u64| {
        let start = Instant::now();
        while start.elapsed() < Duration::from_secs(secs) {
            if super::codex_gateway::hermes_api_ready() {
                return true;
            }
            std::thread::sleep(Duration::from_secs(2));
        }
        super::codex_gateway::hermes_api_ready()
    };
    if ready_within(90) {
        return;
    }
    log::warn!("[hermes-gateway] {when}网关 90 秒没起来, 用 `hermes gateway start` 再拉一次");
    // 10/29: 起不来的一个原因是 hermes 登记了没建好依赖环境的自管 Python (`gateway start` 用它)
    super::hermes_store_guard::set_aside_uncommitted_store_python(when);
    if let Err(e) = super::hermes_plugin::run_hermes_cli(&["gateway", "start"]) {
        log::warn!("[hermes-gateway] `hermes gateway start` 报错: {e}");
    }
    if ready_within(90) {
        log::info!("[hermes-gateway] {when}网关已重新拉起");
    } else {
        log::error!(
            "[hermes-gateway] {when}网关还是没起来, 小鲶暂时回不了话。手动: \
             %LOCALAPPDATA%\\hermes\\hermes-agent\\venv\\Scripts\\hermes.exe gateway start, 看 hermes\\logs\\gateway.log \
             和 gateway-stdio.log (启动即退出的报错只在后者)"
        );
    }
}
