//! 启动时的运行时依赖自检 —— jieba / playwright / agent-browser。
//!
//! 2026-08-15 从 autostart.rs 切出来 (1056 行超限)。纯搬迁, 逻辑一行未改。
//!
//! 这一组的共同点是"缺了不会报错, 只会悄悄变差": jieba 没装, 分词退化成
//! 字符二元组; agent-browser 没装, 走 npx fallback 在受限网络下干等 26 秒。
//! 所以这里一律**真跑一次**再下结论, 而且只打日志不阻塞启动。

use crate::services::{catfish_paths, process};

// ============================================================
// 运行时依赖自检 (BL-DEPS-SILENT-DEGRADE 7/27 鸿波定)
// ============================================================

/// 检查那些"缺了不会报错、只会悄悄变差"的外部依赖。只打日志,不阻塞启动。
///
/// # 为什么要这个
///
/// 7/27 一晚上挖出来的坑里，有两个属于同一类：**依赖没了,但没人知道**。
///
/// 1. `jieba` 不在 hermes venv 里 → style_fingerprint 静默退化成字符二元组。
///    同一批公文语料实测差距:
///      无 jieba: 覆盖 绩材 台账 兄弟 显缺 范围 补充 占优 弃投 项施
///      有 jieba: 资质 完成 服务 对标 施工 工作 调用 情况 公司 其中
///    这个 top_words 是要注进 system prompt 让 LLM 模仿员工用词的,喂碎片
///    等于喂噪音。而代码里是 `except ImportError: 走 char-2gram`,一声不吭。
///
/// 2. `agent-browser` 没装 → hermes 的 browser_navigate 走 npx fallback,
///    npx 去 npm registry 下载,受限网络下干等 26 秒超时。而 UI 当时还把
///    失败渲染成 ✓,鸿波和我一起往 CDP / 页面渲染方向查了好几轮。
///
/// # 为什么必须真跑一次,不能只看文件在不在
///
/// 鸿波实盘出现过一个中间态: `npm i -g agent-browser` 装完了,`which` 找得到
/// `/opt/homebrew/bin/agent-browser`,但 npm 的 allow-scripts 拦掉了 postinstall,
/// symlink 还指着不完整的东西 —— 跑起来照样超时。
///
/// hermes 自己在 `_find_agent_browser` 里踩过同一个坑,注释写着:
///   "A bare shutil.which hit is NOT trusted: ... leaves a dangling link that
///    which still reports but exec fails on with exit 127"
///
/// 所以这里一律**执行一次**再下结论。
///
/// # 为什么只打日志
///
/// 鸿波 7/27 定的:不上面板。这些是 IT 侧该处理的环境问题,不是员工每天要看的
/// 东西;弹窗只会让人学会忽略弹窗。日志里点名,排查时第一眼就能看到。
pub async fn check_runtime_deps() {
    tokio::task::spawn_blocking(|| {
        check_jieba_installed();
        check_playwright_installed();
        check_agent_browser_runnable();
    })
    .await
    .ok();
}

/// jieba 在不在 tool-bridge 实际运行的那个解释器里。
fn check_jieba_installed() {
    let Some(python) = catfish_paths::tool_bridge_python() else {
        return;
    };
    let ok = process::background_command(&python)
        .args(["-c", "import jieba"])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);
    if ok {
        log::info!("deps: jieba ✓ ({})", python.display());
        return;
    }
    log::warn!(
        concat!(
            "deps: ⚠ jieba 不在 {py}\n",
            "     后果: 文书风格 (style_fingerprint) 的中文分词静默退化成字符二元组,\n",
            "     top_words 会变成「覆盖 绩材 台账 兄弟 显缺」这类无意义碎片,\n",
            "     而它是要注进 system prompt 让 LLM 模仿员工用词的。Dashboard 上\n",
            "     「jieba 分词」那栏会显 ❌,但没人会天天去看。\n",
            "     修: {py} -m pip install jieba\n",
            "     (hermes 升级重建 venv 后会再次丢失 —— 这条检查就是为那时准备的)"
        ),
        py = python.display(),
    );
}

/// playwright 在不在 tool-bridge 那个解释器里 (catfish_browser_* 全靠它)。
///
/// # 为什么补这条 (8/5 鸿波 "MACOS 怎么安装了新包, 为什么会缺")
///
/// 员工让鲶鱼开个网页, 回答是"Chrome 那边缺 playwright 包, 导航没走成"。
/// 而他刚装过 Companion 新包 —— 装的是 .app, 跟 ~/.hermes/hermes-agent/venv
/// 是两码事, 重装 .app 完全不碰那个 venv。venv 会被 hermes 升级重建, 额外装
/// 进去的包就没了。
///
/// 跟 jieba 是同一个成因 (见上面那条的最后一行注释), 而自检**只覆盖了 jieba
/// 和 agent-browser** —— 机制建好了, 新依赖没接上去。于是 playwright 丢了没人
/// 吭声, 直到员工撞上才发现, 而且报错还只出现在对话里。
///
/// 判据用 `import playwright.sync_api` 而不是 `import playwright`:
/// catfish_tools_browser.py:139 导入的正是 `playwright.sync_api.sync_playwright`,
/// 检查要跟真实用法一致。
///
/// **修复命令里刻意不含 `playwright install`**: catfish_tools_browser.py:46
/// 写明「不装 chromium binary (Playwright 默认会装 ~150MB), 用 connect_over_cdp
/// 复用员工 Chrome」。写上去会让人白下 150MB, 达华离线现场还会直接失败。
/// `--proxy ''` 是照抄 _import_playwright 里那条 —— 受限网络下必要。
fn check_playwright_installed() {
    let Some(python) = catfish_paths::tool_bridge_python() else {
        return;
    };
    let ok = process::background_command(&python)
        .args(["-c", "import playwright.sync_api"])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);
    if ok {
        log::info!("deps: playwright ✓ ({})", python.display());
        return;
    }
    log::warn!(
        concat!(
            "deps: ⚠ playwright 不在 {py}\n",
            "     后果: catfish_browser_* 全部不可用 —— 员工让鲶鱼开网页会得到\n",
            "     「缺 playwright 包, 导航没走成」, 而这句只出现在对话里, 没人\n",
            "     会去翻它是环境问题还是网站问题。\n",
            "     修: HTTPS_PROXY= HTTP_PROXY= {py} -m pip install --proxy '' playwright\n",
            "     (**不要**跑 playwright install —— 我们走 connect_over_cdp 复用员工\n",
            "      已登录的 Chrome, 不需要它自带的 chromium, 那是 150MB 白下)\n",
            "     (hermes 升级重建 venv 后会再次丢失 —— 跟 jieba 同一个成因)"
        ),
        py = python.display(),
    );
}

/// agent-browser 能不能真跑起来 (hermes 的 browser_* 全靠它)。
fn check_agent_browser_runnable() {
    let Some(bin) = find_agent_browser() else {
        log::warn!(
            concat!(
                "deps: ⚠ 找不到 agent-browser\n",
                "     后果: hermes 的 browser_navigate / browser_click 等全部不可用 ——\n",
                "     它会 fallback 到 `npx agent-browser`,npx 再去 npm registry 下载,\n",
                "     受限网络下就是干等到超时 (实测 26s),错误信息还只说 timed out。\n",
                "     修: npm i -g agent-browser\n",
                "     (catfish 自己的 catfish_browser_* 走 Playwright 直连 CDP,不依赖它)"
            )
        );
        return;
    };
    // 关键: 真执行一次。见函数头注释里 postinstall / dangling symlink 那段。
    match process::background_command(&bin).arg("--version").output() {
        Ok(o) if o.status.success() => {
            let ver = String::from_utf8_lossy(&o.stdout).trim().to_string();
            log::info!("deps: agent-browser ✓ {} ({})", ver, bin.display());
        }
        Ok(o) => log::warn!(
            concat!(
                "deps: ⚠ agent-browser 存在但跑不起来 (exit {code:?}): {bin}\n",
                "     多半是 npm 的 allow-scripts 拦了 postinstall,symlink 还指着\n",
                "     不完整的东西 —— `which` 查得到,执行就废。\n",
                "     修: node $(npm root -g)/agent-browser/scripts/postinstall.js"
            ),
            code = o.status.code(),
            bin = bin.display(),
        ),
        Err(e) => log::warn!("deps: ⚠ agent-browser 执行失败 {}: {e}", bin.display()),
    }
}

/// 找 agent-browser 二进制。
///
/// Companion 是 GUI app,继承的 PATH 通常只有 `/usr/bin:/bin:/usr/sbin:/sbin`,
/// **不含** Homebrew 和 npm global 的目录 —— 所以不能只靠 `which`,得显式找。
pub(crate) fn find_agent_browser() -> Option<std::path::PathBuf> {
    let mut candidates: Vec<std::path::PathBuf> = vec![
        "/opt/homebrew/bin/agent-browser".into(), // Apple Silicon Homebrew
        "/usr/local/bin/agent-browser".into(),    // Intel Homebrew / npm 默认 prefix
    ];
    if let Ok(home) = crate::util::paths::home_env() {
        let h = std::path::PathBuf::from(home);
        candidates.push(h.join(".npm-global/bin/agent-browser"));
        candidates.push(h.join(".nvm/versions/node/current/bin/agent-browser"));
    }
    if let Ok(path) = std::env::var("PATH") {
        for dir in path.split(':') {
            candidates.push(std::path::Path::new(dir).join("agent-browser"));
        }
    }
    candidates.into_iter().find(|p| p.exists())
}
