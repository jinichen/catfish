//! 日历 — BL-CALENDAR-INTEGRATION (5/20 鸿波).
//!
//! 调 macOS Calendar.app 取今日 events. 用 osascript -l JavaScript (JXA), 比传统
//! AppleScript 快 5-10x 且能直接 emit JSON.
//!
//! 设计:
//!   - 不缓存到本地 (跟 email 简报同原则) — 每次 fetch 现调
//!   - 不写文件 / 不读 ~/.calendar/* — 全靠 osascript object model
//!   - 出错不挂卡: 没装 Calendar.app / 没授权"自动化" 都返 Err(String) 给前端展示
//!
//! 红线:
//!   - 第一次调用 macOS 会弹"催发栖 Companion 想控制 Calendar.app" 授权对话框,
//!     员工点允许才走得通. 拒了 osascript 返 -1743 错误, 前端按"先去系统设置 →
//!     隐私 → 自动化 → 催发栖 Companion → 勾选 Calendar.app" 提示
//!   - 不 cache events 文本 — Calendar 数据是员工本机敏感数据
//!
//! step1 (5/20 上午): 当日 events 列表 (summary / start / end / location 可选)
//! step2 (5/20 下午): 5 分钟内存缓存减少 osascript 调用. force_refresh 参数 bypass.
//! step3 (5/20 下午, 本提交): calendar_week_fetch 跨日 7 天. 复用 osascript path, 独立缓存.
//! step4 (后续): Swift FFI EventKit binding (osascript 仍 ~2-5s, 太慢)

use std::process::Command;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use std::sync::OnceLock;

/// BL-CALENDAR-INTEGRATION step2 (5/20): 5 分钟内存缓存.
///
/// 设计:
///   - 用 OnceLock + Mutex (std-only, 不引新依赖)
///   - Some((fetched_at, json)) | None
///   - 5 分钟内连续 fetch 走缓存 (常见: 切 tab / 打开 dashboard / BriefingCard 重渲染)
///   - 超过 5 分钟 / force_refresh=true / 空结果不缓存
///   - 失败结果不缓存 (避免授权失败挡 5 分钟)
fn cache() -> &'static Mutex<Option<(Instant, String)>> {
    static CACHE: OnceLock<Mutex<Option<(Instant, String)>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(None))
}

/// BL-CALENDAR-WEEK (5/20): 跨日 events 独立缓存. 7 天 events 量大, 跟今日 cache 分桶.
fn week_cache() -> &'static Mutex<Option<(Instant, String)>> {
    static CACHE: OnceLock<Mutex<Option<(Instant, String)>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(None))
}

const CACHE_TTL: Duration = Duration::from_secs(300);  // 5 分钟

/// JXA 脚本 — 取今天 0:00-24:00 所有 calendar 的 events, 返 JSON array.
/// 注意 Calendar.app object model 的 `.events.whose(...)` filter 比 JS 自己滤快 100x
/// (osascript 跑 .events() 会全量加载, 几十秒).
///
/// 5/20 鸿波本机超时 8s — Calendar.app 多账号 + 订阅日历累加查询慢.
/// 优化: 跳订阅日历 (writable=false 的, 多是节假日 / 同事公开日历, 员工不需要看).
/// 这样能把 N 从 30+ 降到 5-10.
const JXA_TODAY_EVENTS: &str = r#"
const Calendar = Application("Calendar");
Calendar.includeStandardAdditions = true;

const today = new Date();
today.setHours(0, 0, 0, 0);
const tomorrow = new Date(today);
tomorrow.setDate(tomorrow.getDate() + 1);

const out = [];
try {
  const cals = Calendar.calendars();
  for (let i = 0; i < cals.length; i++) {
    const cal = cals[i];
    // 5/20 性能优化: 跳订阅 / readonly calendar — 多账号员工有 30+ calendar,
    // 多数是节假日订阅 / 公共日历, 员工自己的事件在 writable calendar 里.
    // writable=false 的 events.whose() 一样慢, 但里面基本没真要看的事.
    try {
      if (cal.writable && cal.writable() === false) continue;
    } catch (_) {
      // 老 Calendar.app 没 writable 属性 — 不跳, 走老路径
    }
    let evts;
    try {
      // whose 子句: 用 Calendar.app object model 服务端过滤, 不要 .events() 全量
      evts = cal.events.whose({
        _and: [
          { startDate: { _greaterThanEquals: today } },
          { startDate: { _lessThan: tomorrow } },
        ],
      })();
    } catch (e) {
      continue;  // 某些 calendar (订阅 / 节假日) 无 events 属性, 跳
    }
    for (let j = 0; j < evts.length; j++) {
      const e = evts[j];
      try {
        const item = {
          calendar: cal.name(),
          summary: e.summary() || "(无标题)",
          start: e.startDate().toISOString(),
          end: e.endDate().toISOString(),
          all_day: e.alldayEvent(),
        };
        try { item.location = e.location() || ""; } catch (_) {}
        // BL-COMPANION-BRIEFING-V2 (5/20): 抽 attendees / description.
        // 早安播报 v2 单条 event 展开时显参会人 + 事件描述.
        // try/catch 包死 — 订阅日历 / 老 macOS / 损坏事件 这两个字段可能取不出.
        try {
          const atts = e.attendees();
          if (atts && atts.length > 0) {
            const names = [];
            for (let k = 0; k < atts.length; k++) {
              try {
                // 优先 displayName, fallback emailAddress
                const nm = atts[k].displayName();
                const em = atts[k].emailAddress();
                if (nm) names.push(nm);
                else if (em) names.push(em);
              } catch (_) {}
            }
            if (names.length > 0) item.attendees = names;
          }
        } catch (_) {}
        try {
          const desc = e.description();
          if (desc) {
            // 截 500 字防展开区被超长描述撑爆
            item.description = desc.length > 500 ? desc.slice(0, 500) + "…" : desc;
          }
        } catch (_) {}
        out.push(item);
      } catch (e2) {
        // 单条 event 读不出 (权限 / 损坏), 跳
      }
    }
  }
} catch (e) {
  // 整个 Calendar 拉不出 (没装 / 没授权), 返空 + 不抛, Rust 那边按 stdout 空判断
}
// 按 start 升序
out.sort((a, b) => a.start.localeCompare(b.start));
JSON.stringify(out);
"#;

/// BL-CALENDAR-WEEK (5/20): 跨日 7 天 events JXA 脚本.
/// 跟 JXA_TODAY_EVENTS 区别: tomorrow 改 +7 days, 多带 1 天 (从今天 0 点开始).
const JXA_WEEK_EVENTS: &str = r#"
const Calendar = Application("Calendar");
Calendar.includeStandardAdditions = true;

const today = new Date();
today.setHours(0, 0, 0, 0);
const weekEnd = new Date(today);
weekEnd.setDate(weekEnd.getDate() + 7);

const out = [];
try {
  const cals = Calendar.calendars();
  for (let i = 0; i < cals.length; i++) {
    const cal = cals[i];
    // 同 today: 跳订阅 / readonly calendar 减查询次数 (5/20 鸿波报超时优化)
    try {
      if (cal.writable && cal.writable() === false) continue;
    } catch (_) {}
    let evts;
    try {
      evts = cal.events.whose({
        _and: [
          { startDate: { _greaterThanEquals: today } },
          { startDate: { _lessThan: weekEnd } },
        ],
      })();
    } catch (e) {
      continue;
    }
    for (let j = 0; j < evts.length; j++) {
      const e = evts[j];
      try {
        const item = {
          calendar: cal.name(),
          summary: e.summary() || "(无标题)",
          start: e.startDate().toISOString(),
          end: e.endDate().toISOString(),
          all_day: e.alldayEvent(),
        };
        try { item.location = e.location() || ""; } catch (_) {}
        // BL-COMPANION-BRIEFING-V2 (5/20): attendees / description (跟 today 同套路)
        try {
          const atts = e.attendees();
          if (atts && atts.length > 0) {
            const names = [];
            for (let k = 0; k < atts.length; k++) {
              try {
                const nm = atts[k].displayName();
                const em = atts[k].emailAddress();
                if (nm) names.push(nm);
                else if (em) names.push(em);
              } catch (_) {}
            }
            if (names.length > 0) item.attendees = names;
          }
        } catch (_) {}
        try {
          const desc = e.description();
          if (desc) {
            item.description = desc.length > 500 ? desc.slice(0, 500) + "…" : desc;
          }
        } catch (_) {}
        out.push(item);
      } catch (e2) {}
    }
  }
} catch (e) {}
out.sort((a, b) => a.start.localeCompare(b.start));
JSON.stringify(out);
"#;

/// 取未来 7 天 events 列表 (今天 0 点 — 7 天后 0 点). BL-CALENDAR-WEEK (5/20).
///
/// 设计同 calendar_today_fetch: osascript JXA + 5min 缓存 + force_refresh 跳缓存.
/// 7 天 events 通常 20-100 条 (员工日历不同), JXA 单次拉, 不分日轮询.
/// 前端按 start ISO 日期分组 (今天 / 明天 / 后天 / ...).
///
/// 超时: 12 秒 (比 today 8s 宽, 7 天 events 多 JXA 跑长一点).
#[tauri::command]
pub async fn calendar_week_fetch(force_refresh: Option<bool>) -> Result<String, String> {
    let force = force_refresh.unwrap_or(false);

    if !force {
        let guard = week_cache().lock().map_err(|e| format!("week_cache lock 坏 {e}"))?;
        if let Some((fetched_at, json)) = guard.as_ref() {
            if fetched_at.elapsed() < CACHE_TTL {
                return Ok(json.clone());
            }
        }
    }

    // BL-CALENDAR-EVENTKIT (5/21): 优先 EventKit binary, osascript fallback.
    let result = tokio::task::spawn_blocking(|| {
        match run_eventkit("week", Duration::from_secs(3)) {
            Ok(json) => Ok(json),
            Err(e) => {
                eprintln!("[calendar] EventKit binary 不可用 ({}), fallback osascript", e);
                run_osascript(JXA_WEEK_EVENTS, Duration::from_secs(20))
            }
        }
    })
    .await
    .map_err(|e| format!("join error: {e}"))?;

    if let Ok(json) = &result {
        if let Ok(mut guard) = week_cache().lock() {
            *guard = Some((Instant::now(), json.clone()));
        }
    }

    result
}

/// 取今日 events 列表. 返 raw JSON 字符串, 前端自己 parse.
///
/// force_refresh=Some(true) → 跳缓存直接调 osascript. 默认 false 走 5 分钟缓存.
///
/// 超时: 8 秒 (osascript 第一次启动 ~1-3s, 加 Calendar.app cold start 2-4s).
/// 超时 → 返 Err. 员工点⟳ retry 一般第二次就快.
#[tauri::command]
pub async fn calendar_today_fetch(force_refresh: Option<bool>) -> Result<String, String> {
    let force = force_refresh.unwrap_or(false);

    // BL-CALENDAR-INTEGRATION step2: 5 分钟缓存命中?
    if !force {
        let guard = cache().lock().map_err(|e| format!("cache lock 坏 {e}"))?;
        if let Some((fetched_at, json)) = guard.as_ref() {
            if fetched_at.elapsed() < CACHE_TTL {
                return Ok(json.clone());
            }
        }
    }

    // BL-CALENDAR-EVENTKIT (5/21): 优先调 Swift binary (EventKit), osascript 兜底.
    // EventKit 直调 macOS 原生 API < 100ms 稳定, 不走 AppleScript subprocess 冷启动慢.
    // Swift binary 不存在 (未编译 / 跨平台) → fallback osascript 老路径.
    let result = tokio::task::spawn_blocking(|| {
        match run_eventkit("today", Duration::from_secs(3)) {
            Ok(json) => Ok(json),
            Err(e) => {
                eprintln!("[calendar] EventKit binary 不可用 ({}), fallback osascript 15s timeout", e);
                run_osascript(JXA_TODAY_EVENTS, Duration::from_secs(15))
            }
        }
    })
    .await
    .map_err(|e| format!("join error: {e}"))?;

    // 成功 → 写缓存. 失败结果不缓存 (避免授权错误挡 5 分钟)
    if let Ok(json) = &result {
        // 空 array "[]" 也缓存 — "今天没排事" 是有效结果
        if let Ok(mut guard) = cache().lock() {
            *guard = Some((Instant::now(), json.clone()));
        }
    }

    result
}

/// BL-CALENDAR-EVENTKIT (5/21): spawn catfish-calendar Swift binary 调 EventKit.
///
/// 路径查找顺序:
///   1. .app/Contents/Resources/catfish-calendar  (生产 build 后打包路径)
///   2. ../../catfish-calendar  (开发期 tauri dev, swift/ 目录里 swiftc -o ../catfish-calendar)
///   3. src-tauri/swift/catfish-calendar         (开发期, 跟源码同目录)
///   4. PATH 找  (员工手动 swiftc 编译装 /usr/local/bin/)
///
/// 子命令: today / week / list-cals / create
/// 超时 3s (EventKit < 100ms 正常, 给到 3s 应对系统繁忙)
fn run_eventkit(subcmd: &str, timeout: Duration) -> Result<String, String> {
    use std::io::Read;

    // 找 binary
    let bin_path = locate_eventkit_binary()
        .ok_or_else(|| "catfish-calendar binary 未找到 (尚未编译? cd src-tauri/swift && bash build.sh)".to_string())?;

    let mut child = Command::new(&bin_path)
        .args([subcmd, "--json"])
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("spawn catfish-calendar 失败: {e}"))?;

    let start = std::time::Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let mut stdout = String::new();
                let mut stderr = String::new();
                if let Some(mut so) = child.stdout.take() {
                    let _ = so.read_to_string(&mut stdout);
                }
                if let Some(mut se) = child.stderr.take() {
                    let _ = se.read_to_string(&mut stderr);
                }
                if !status.success() {
                    return Err(format!(
                        "catfish-calendar exit {:?}: {}",
                        status.code(),
                        stderr.trim()
                    ));
                }
                let trimmed = stdout.trim();
                if trimmed.is_empty() {
                    return Ok("[]".to_string());
                }
                return Ok(trimmed.to_string());
            }
            Ok(None) => {
                if start.elapsed() > timeout {
                    let _ = child.kill();
                    return Err(format!("catfish-calendar 超时 {}s (EventKit 应该 < 100ms, 超时异常)", timeout.as_secs()));
                }
                std::thread::sleep(Duration::from_millis(20));
            }
            Err(e) => {
                return Err(format!("catfish-calendar wait 失败: {e}"));
            }
        }
    }
}

fn locate_eventkit_binary() -> Option<std::path::PathBuf> {
    use std::path::PathBuf;

    // 1. .app/Contents/Resources/catfish-calendar (tauri build 后路径)
    if let Ok(exe) = std::env::current_exe() {
        // exe 在 .app/Contents/MacOS/<binary>, Resources 同级
        if let Some(macos_dir) = exe.parent() {
            if let Some(contents_dir) = macos_dir.parent() {
                let candidate = contents_dir.join("Resources").join("catfish-calendar");
                if candidate.exists() {
                    return Some(candidate);
                }
            }
        }
    }

    // 2 + 3. 开发期: src-tauri/swift/catfish-calendar / src-tauri/catfish-calendar
    let dev_candidates: [PathBuf; 2] = [
        PathBuf::from("src-tauri/swift/catfish-calendar"),
        PathBuf::from("src-tauri/catfish-calendar"),
    ];
    for c in dev_candidates {
        if c.exists() {
            return Some(c);
        }
    }

    // 4. PATH
    if let Ok(output) = Command::new("which").arg("catfish-calendar").output() {
        if output.status.success() {
            let path_str = String::from_utf8_lossy(&output.stdout).trim().to_string();
            if !path_str.is_empty() {
                return Some(PathBuf::from(path_str));
            }
        }
    }

    None
}

fn run_osascript(script: &str, timeout: Duration) -> Result<String, String> {
    use std::io::Read;

    let mut child = Command::new("osascript")
        .args(["-l", "JavaScript", "-e", script])
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("osascript 启动失败: {e}"))?;

    // 简单 timeout: 轮询 try_wait 直到超时
    let start = std::time::Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let mut stdout = String::new();
                let mut stderr = String::new();
                if let Some(mut so) = child.stdout.take() {
                    let _ = so.read_to_string(&mut stdout);
                }
                if let Some(mut se) = child.stderr.take() {
                    let _ = se.read_to_string(&mut stderr);
                }
                if !status.success() {
                    let stderr_trim = stderr.trim();
                    return Err(format!(
                        "osascript 失败 (exit {:?}): {}",
                        status.code(),
                        if stderr_trim.is_empty() {
                            // -1743 = 没授权 "自动化" Calendar.app
                            "没输出 (常见: 未授权 catfish Companion 控制 Calendar — 系统设置 → 隐私与安全性 → 自动化 → catfish Companion → 勾 Calendar)"
                        } else {
                            stderr_trim
                        }
                    ));
                }
                let trimmed = stdout.trim();
                if trimmed.is_empty() {
                    return Ok("[]".to_string());
                }
                return Ok(trimmed.to_string());
            }
            Ok(None) => {
                if start.elapsed() > timeout {
                    let _ = child.kill();
                    return Err(format!(
                        "osascript 超时 {}s. 常见原因: \
                        (1) **macOS 14+ 日历权限给的是 '仅添加访问权限', 读取被静默拒**: \
                           系统设置 → 隐私与安全性 → 日历 → Catfish Companion → 点 '选项...' → 改成 '完全日历访问权限'. \
                           '仅添加' 只能创建事件, 不能读今天日程. \
                        (1.5) **改完权限要 Cmd+Q 完全退出 Companion 再重开**: TCC 权限按进程启动时快照, 改完不重启用的还是旧权限快照. \
                        (2) macOS 弹了首次授权对话框被忽略: 系统设置 → 隐私 → 自动化 → 鲶鱼 Companion → 勾 Calendar; \
                        (3) Calendar.app 多账号 / 订阅日历多 — 已自动跳订阅 calendar; \
                        (4) Calendar.app 冷启动 — 重试一次一般快; \
                        长期解决: BL-CALENDAR-INTEGRATION step3 Swift FFI EventKit",
                        timeout.as_secs()
                    ));
                }
                std::thread::sleep(Duration::from_millis(80));
            }
            Err(e) => {
                return Err(format!("osascript wait 失败: {e}"));
            }
        }
    }
}
