//! P3.5.126 (6/26 鸿波 catch "local_search 目录设置 UI 找不到"):
//! local_search 索引目录管理.
//!
//! 写到 ~/.catfish/search-scope.yaml (跟 catfish-local-search/src/catfish_search/config.py
//! CONFIG_FILE 路径对齐) —— python 端 load_config 唯一读的地方.
//!
//! BL-STYLE-FP-USE-INDEX (7/27): 这里是**唯一**的索引目录入口了.
//! 原来还有 style_fingerprint_dirs.rs 写 companion.yaml 的 style_fingerprint.scan_dirs,
//! 两套割裂让鸿波的文书风格抽了两个多月 0 文档, 已整个删除. Onboarding 第 4 步
//! 和 StyleFingerprintCard 现在都走这套命令.
//!
//! 命令:
//!   - local_search_scope_get → 读 yaml 返当前 include/exclude
//!   - local_search_scope_add → 校验路径 (存在 + 是 dir) + 追加, 不重复
//!   - local_search_scope_remove → 删一项
//!
//! yaml 写回策略:
//!   - 不动其它段 (exclude / file_types / max_file_size_mb), 只改 include
//!   - 读 → 改 → 全文 dump 写回. serde_yaml 会丢 comments — 第一版能接受.
//!     search-scope.yaml 有默认头注释, 鸿波改完后注释丢, 但 UI 加目录不靠注释.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_yaml::Value as YamlValue;

fn search_scope_yaml_path() -> Result<PathBuf, String> {
    let home = crate::util::paths::home_env().map_err(|e| format!("HOME 未设: {e}"))?;
    Ok(PathBuf::from(home).join(".catfish").join("search-scope.yaml"))
}

fn read_root() -> Result<YamlValue, String> {
    let path = search_scope_yaml_path()?;
    if !path.exists() {
        return Ok(YamlValue::Mapping(Default::default()));
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("读 search-scope.yaml 失败: {e}"))?;
    if text.trim().is_empty() {
        return Ok(YamlValue::Mapping(Default::default()));
    }
    serde_yaml::from_str(&text).map_err(|e| format!("解析 yaml 失败: {e}"))
}

fn write_root(root: &YamlValue) -> Result<(), String> {
    let path = search_scope_yaml_path()?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("创建 ~/.catfish 失败: {e}"))?;
    }
    let yaml_text = serde_yaml::to_string(root)
        .map_err(|e| format!("序列化 yaml 失败: {e}"))?;
    std::fs::write(&path, yaml_text)
        .map_err(|e| format!("写 search-scope.yaml 失败: {e}"))
}

fn expand_home(s: &str) -> Result<PathBuf, String> {
    let s = s.trim();
    if s.is_empty() {
        return Err("路径不能空".into());
    }
    if let Some(rest) = s.strip_prefix("~/").or_else(|| s.strip_prefix("~\\")) {
        let home = crate::util::paths::home_env().map_err(|e| format!("HOME 未设: {e}"))?;
        Ok(PathBuf::from(home).join(rest))
    } else if s == "~" {
        let home = crate::util::paths::home_env().map_err(|e| format!("HOME 未设: {e}"))?;
        Ok(PathBuf::from(home))
    } else {
        Ok(PathBuf::from(s))
    }
}

/// 10/18 (鸿波「初始化时输入文件存放目录会出错, 除非是缺省目录」): 员工手输的目录先规整一遍。
///
/// 原来只认 `~/` 开头的写法, 下面这些都直接报「路径不存在」:
///   · 带引号: Windows 资源管理器「复制为路径」出来是 `"D:\工作\资料"`; 也有人顺手加引号
///   · `~\Documents` (Windows 写法)、`$HOME/...`、`%USERPROFILE%\...`
///   · **macOS 中文系统的显示名**: 访达里看到的「文稿 / 桌面 / 下载」只是显示名, 磁盘上叫 Documents / Desktop / Downloads,
///     而界面提示里写的就是 `~/文稿`、`~/桌面/资料` —— 照着提示填反而必错。只在字面路径不存在时才换, 真有个叫「文稿」的目录照用。
fn normalize_input(raw: &str) -> String {
    let mut s = raw.trim().trim_matches(|c| c == '"' || c == '\'' || c == '“' || c == '”').trim().to_string();
    if let Ok(home) = crate::util::paths::home_env() {
        for var in ["$HOME", "${HOME}", "%USERPROFILE%", "%HOMEPATH%"] {
            if let Some(rest) = s.strip_prefix(var) {
                s = format!("{home}{rest}");
            }
        }
    }
    s
}

/// 中文显示名 → 磁盘上可能的真名 (只换 home 下第一段; 按顺序试, 第一个存在的算)。
/// macOS 访达: 文稿 / 桌面 / 下载 / 影片; Windows 资源管理器: 文档 / 桌面 / 下载 / 视频 —— 磁盘上都是英文名。
const LOCALIZED_HOME_DIRS: &[(&str, &[&str])] = &[
    ("文稿", &["Documents"]), ("文档", &["Documents"]), ("我的文档", &["Documents"]),
    ("桌面", &["Desktop"]), ("下载", &["Downloads"]), ("图片", &["Pictures"]),
    ("影片", &["Movies", "Videos"]), ("视频", &["Videos", "Movies"]), ("音乐", &["Music"]),
];

/// 字面路径不存在时, 换成真名再试的候选 (写成 `~/...`)。认这几种开头:
///   `~/文稿/资料`、`~\文档\资料` · `C:\Users\张三\文档\资料` (home 的绝对路径, Windows 上不分大小写) · `桌面\资料` (没写开头)
/// Windows 上「文档 / 桌面」常被 OneDrive 接管, 实际在 `~\OneDrive\Documents` 或 `~\OneDrive\文档`, 也一并试。
fn localized_candidates(raw: &str) -> Vec<String> {
    localized_candidates_for(raw, &crate::util::paths::home_env().unwrap_or_default(), cfg!(windows))
}

/// 平台当参数传进来, Windows 那几支 (OneDrive、不分大小写) 在 mac / Linux CI 上也测得到。
fn localized_candidates_for(raw: &str, home: &str, windows: bool) -> Vec<String> {
    let rest = if let Some(r) = raw.strip_prefix("~/").or_else(|| raw.strip_prefix("~\\")) {
        r.to_string()
    } else if !home.is_empty() && raw.len() > home.len() && raw.is_char_boundary(home.len())
        && (if windows { raw[..home.len()].eq_ignore_ascii_case(home) } else { &raw[..home.len()] == home })
        && raw[home.len()..].starts_with(['/', '\\'])
    {
        raw[home.len() + 1..].to_string()
    } else if !raw.starts_with(['/', '\\', '~']) && !raw.contains(':') {
        raw.to_string() // 「桌面/资料」这种没写开头的
    } else {
        return Vec::new();
    };
    let (first, tail) = match rest.split_once(['/', '\\']) {
        Some((a, b)) => (a.to_string(), b.trim_matches(['/', '\\']).to_string()),
        None => (rest.clone(), String::new()),
    };
    let Some((_, reals)) = LOCALIZED_HOME_DIRS.iter().find(|(zh, _)| *zh == first) else { return Vec::new() };
    let join = |base: String| if tail.is_empty() { base } else { format!("{base}/{}", tail.replace('\\', "/")) };
    let mut out: Vec<String> = reals.iter().map(|r| join(format!("~/{r}"))).collect();
    if windows {
        for r in *reals {
            out.push(join(format!("~/OneDrive/{r}")));
        }
        out.push(join(format!("~/OneDrive/{first}")));
    }
    out
}

/// 存在且是目录 → Ok; 否则给一句员工看得懂、知道下一步怎么办的话。
/// 区分「没有」和「没权限」: macOS 的 文稿 / 桌面 / 下载 受隐私保护, 没授权时 Rust 的 `exists()` 直接返回 false,
/// 原来一律报「路径不存在」—— 目录明明在, 员工只能怀疑自己打错了。
fn check_dir(p: &std::path::Path) -> Result<(), String> {
    match std::fs::metadata(p) {
        Ok(m) if m.is_dir() => match std::fs::read_dir(p) {
            Ok(_) => Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied => Err(no_permission(p)),
            Err(e) => Err(format!("打不开这个目录: {} ({e})", p.display())),
        },
        Ok(_) => Err(format!("这是文件不是文件夹: {}", p.display())),
        Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied => Err(no_permission(p)),
        Err(_) => Err(format!("找不到这个文件夹: {} —— 点「选择文件夹…」直接选, 不用手打", p.display())),
    }
}

fn no_permission(p: &std::path::Path) -> String {
    if cfg!(target_os = "macos") {
        format!(
            "鲶鱼还没有权限读这个文件夹: {} —— 点「选择文件夹…」选一次就有了; \
             或者在 系统设置 → 隐私与安全性 → 文件与文件夹 里给鲶鱼打开",
            p.display()
        )
    } else {
        format!("没有权限读这个文件夹: {} —— 换一个, 或者点「选择文件夹…」", p.display())
    }
}

/// 规整 + 校验, 返回 (要写进 yaml 的写法, 展开后的路径)。
fn resolve_input(raw: &str) -> Result<(String, PathBuf), String> {
    let s = normalize_input(raw);
    if s.is_empty() {
        return Err("路径不能空".into());
    }
    let expanded = expand_home(&s)?;
    if std::fs::metadata(&expanded).is_err() {
        for alt in localized_candidates(&s) {
            let alt_path = expand_home(&alt)?;
            if check_dir(&alt_path).is_ok() {
                return Ok((alt, alt_path));
            }
        }
    }
    check_dir(&expanded)?;
    Ok((s, expanded))
}

/// 「选择文件夹…」: 原生选择框 (macOS 上员工自己选的文件夹, 系统会给鲶鱼访问权限)。取消返回 None。
#[tauri::command]
pub fn local_search_pick_dir() -> Option<String> {
    let start = crate::util::paths::home_env().ok().map(PathBuf::from);
    let mut dlg = rfd::FileDialog::new().set_title("选择你的工作文档所在的文件夹");
    if let Some(d) = start {
        dlg = dlg.set_directory(d);
    }
    dlg.pick_folder().map(|p| p.to_string_lossy().into_owned())
}

fn get_string_list(root: &YamlValue, key: &str) -> Vec<String> {
    let list = match root.get(key).and_then(|v| v.as_sequence()) {
        Some(s) => s,
        None => return Vec::new(),
    };
    list.iter()
        .filter_map(|v| v.as_str().map(|s| s.to_string()))
        .collect()
}

fn set_string_list(root: &mut YamlValue, key: &str, values: Vec<String>) {
    let map = match root {
        YamlValue::Mapping(m) => m,
        _ => {
            *root = YamlValue::Mapping(Default::default());
            if let YamlValue::Mapping(m) = root {
                m
            } else {
                unreachable!()
            }
        }
    };
    let k = YamlValue::String(key.to_string());
    let seq: Vec<YamlValue> = values.into_iter().map(YamlValue::String).collect();
    map.insert(k, YamlValue::Sequence(seq));
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ScopeResult {
    /// include 列表 — 员工想索引的目录 (yaml include 段)
    pub include: Vec<String>,
    /// exclude 列表 — 排除模式 (yaml exclude 段, 只读不让 UI 改)
    pub exclude: Vec<String>,
    /// yaml 文件路径 (员工想 vim 改 file_types / max_file_size_mb 自己去)
    pub yaml_path: String,
    /// 只有 add 返回: 实际记下的那条 (规整过, 例 `~/文稿/资料` → `~/Documents/资料`), 前端拿它去索引
    #[serde(skip_serializing_if = "Option::is_none")]
    pub added: Option<String>,
}

/// 改完 include 之后重启 watcher。
///
/// # 为什么必须重启 (8/14)
///
/// `watcher.py:run_watch` 开头是 `cfg = cfg or load_config()` —— **配置只在启动时
/// 读一次**, 之后 observer.schedule 出来的监听列表就固定了。没有 SIGHUP, 没有
/// reload。所以改了 search-scope.yaml 对**正在跑的** watcher 毫无作用。
///
/// 这个坑 6/15 (P3.4.2) 已经踩过一次并修过 —— 但只修了「Companion 冷启动」那条路
/// (ensure_local_search_running 改成每次 pkill+respawn)。**面板加/删目录这条路
/// 一直没修**, 于是同一个病换了个触发方式继续活着:
///
///   员工在面板加 ~/Documents → onAdd 跑 `index --only` 把**当时**的存量文件
///   补进索引 → 看起来完全正常, 当场就搜得到
///   → 但运行中的 watcher 不监听这个新根
///   → 之后往 ~/Documents 新增的文件**再也进不了索引**, 直到下次重启 Companion
///
/// 症状极其隐蔽: 加完当场是好的, 过两天才发现"新文件搜不到", 而那时候没人会
/// 把它跟"几天前加过一个目录"联系起来 —— 看着像索引坏了。
///
/// ensure_local_search_running 本身就是 pkill + spawn (没有"在跑就跳过"的分支),
/// 直接调即可。
///
/// # 会阻塞约 500ms, 这是有意接受的
///
/// pkill_local_search_watchers 成功杀掉进程后有一句
/// `std::thread::sleep(500ms)` (等 fsnotify subscribe 释放)。而
/// ensure_local_search_running 虽然签名是 async, **体内一个 .await 都没有** ——
/// 它是个披着 async 外衣的同步函数。所以这里 .await 它会占住一个 tokio worker
/// 线程半秒。
///
/// 按仓里的约定 (local_search.rs:150「不占 tokio runtime 线程」) 本该丢进
/// spawn_blocking, 但那需要先把 autostart 那段拆出一个同步函数 —— 改动落在
/// **启动路径**上。为了省掉一次手动点击的 500ms 去动启动路径, 不划算。
///
/// 实际影响可以忽略: 只在员工手点加/删目录时触发, 前端本来就在 busy 态,
/// 而紧接着的 `index --only` 要跑几秒。真要优化, 单独开一个 ticket 做那次拆分。
async fn restart_watcher_after_scope_change(what: &str) {
    log::info!("local_search_scope: {what} —— 重启 watcher 让新配置生效");
    crate::services::autostart::ensure_local_search_running().await;
}

#[tauri::command]
pub async fn local_search_scope_get() -> Result<ScopeResult, String> {
    let root = read_root()?;
    Ok(ScopeResult {
        include: get_string_list(&root, "include"),
        exclude: get_string_list(&root, "exclude"),
        yaml_path: search_scope_yaml_path()?.to_string_lossy().into_owned(),
        added: None,
    })
}

#[tauri::command]
pub async fn local_search_scope_add(path: String) -> Result<ScopeResult, String> {
    if path.trim().is_empty() {
        return Err("路径不能空".into());
    }
    if path.len() > 500 {
        return Err("路径太长 (>500)".into());
    }
    // 规整 (引号 / ~\ / $HOME / 中文显示名) + 校验 (在、是文件夹、读得了)
    let (raw, expanded) = resolve_input(&path)?;
    let mut root = read_root()?;
    let mut dirs = get_string_list(&root, "include");
    // 去重 — 同 raw 或 同 expanded path 都算重复
    let expanded_str = expanded.to_string_lossy().to_string();
    if dirs.iter().any(|d| {
        d == &raw
            || expand_home(d)
                .map(|p| p.to_string_lossy() == expanded_str)
                .unwrap_or(false)
    }) {
        // 已存在不报错, 静默返回当前列表
        return Ok(ScopeResult { added: Some(raw), ..local_search_scope_get().await? });
    }
    dirs.push(raw.clone());
    set_string_list(&mut root, "include", dirs);
    write_root(&root)?;
    restart_watcher_after_scope_change("加了目录").await;
    Ok(ScopeResult { added: Some(raw), ..local_search_scope_get().await? })
}

#[tauri::command]
pub async fn local_search_scope_remove(path: String) -> Result<ScopeResult, String> {
    let target = path.trim().to_string();
    if target.is_empty() {
        return Err("路径不能空".into());
    }
    let mut root = read_root()?;
    let dirs = get_string_list(&root, "include");
    let new_dirs: Vec<String> = dirs.into_iter().filter(|d| d != &target).collect();
    set_string_list(&mut root, "include", new_dirs);
    write_root(&root)?;
    // 删也要重启 —— 否则老 watcher 继续监听这个目录, 员工点了"删"但鲶鱼还在看。
    // 索引里已有的数据由前端接着调的 local_search_clean 清 (onRemove)。
    restart_watcher_after_scope_change("删了目录").await;
    local_search_scope_get().await
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::util::test_env::env_lock;

    #[test]
    fn expand_home_handles_tilde() {
        let _guard = env_lock();
        std::env::set_var("HOME", "/tmp/fakehome");
        assert_eq!(
            expand_home("~/foo/bar").unwrap(),
            PathBuf::from("/tmp/fakehome/foo/bar")
        );
        assert_eq!(expand_home("~").unwrap(), PathBuf::from("/tmp/fakehome"));
        assert_eq!(
            expand_home("/abs/path").unwrap(),
            PathBuf::from("/abs/path")
        );
        assert!(expand_home("").is_err());
        assert!(expand_home("   ").is_err());
    }

    #[test]
    fn 手输的目录先规整_引号_环境变量_中文显示名() {
        let _guard = env_lock();
        let home = tempfile::tempdir().unwrap();
        std::env::set_var("HOME", home.path());
        std::fs::create_dir_all(home.path().join("Documents/资料")).unwrap();
        std::fs::create_dir_all(home.path().join("工作")).unwrap();
        let docs = home.path().join("Documents/资料");
        assert_eq!(resolve_input("~/文稿/资料").unwrap(), ("~/Documents/资料".to_string(), docs.clone()));
        assert_eq!(resolve_input("\"~/Documents/资料\"").unwrap().1, docs);
        assert_eq!(resolve_input("$HOME/工作").unwrap().1, home.path().join("工作"));
        assert_eq!(resolve_input("  ~/工作 ").unwrap().0, "~/工作");
        assert_eq!(resolve_input(&docs.to_string_lossy()).unwrap().1, docs, "绝对路径照用");
        // 真有个叫「文稿」的目录就用它, 不换
        std::fs::create_dir_all(home.path().join("文稿")).unwrap();
        assert_eq!(resolve_input("~/文稿").unwrap().0, "~/文稿");
        // home 的绝对路径 + 中文名、没写开头的、Windows 反斜杠
        let abs = format!("{}/文稿/资料", home.path().display());
        assert_eq!(resolve_input(&abs).unwrap(), ("~/Documents/资料".to_string(), docs.clone()));
        std::fs::create_dir_all(home.path().join("Desktop/项目")).unwrap();
        assert_eq!(resolve_input("桌面/项目").unwrap().1, home.path().join("Desktop/项目"));
        assert_eq!(resolve_input("~\\桌面\\项目").unwrap().0, "~/Desktop/项目");
        let e = resolve_input("~/没有这个").unwrap_err();
        assert!(e.contains("找不到这个文件夹") && e.contains("选择文件夹"), "{e}");
        std::fs::write(home.path().join("a.txt"), "x").unwrap();
        assert!(resolve_input("~/a.txt").unwrap_err().contains("文件不是文件夹"));
    }

    #[test]
    fn windows_中文名_onedrive_不分大小写() {
        let home = r"C:\Users\张三";
        let c = localized_candidates_for(r"c:\users\张三\文档\资料", home, true);
        assert_eq!(c, vec!["~/Documents/资料", "~/OneDrive/Documents/资料", "~/OneDrive/文档/资料"]);
        assert_eq!(localized_candidates_for(r"桌面\项目\", home, true)[0], "~/Desktop/项目");
        assert_eq!(localized_candidates_for(r"~\视频", home, true)[..2], ["~/Videos".to_string(), "~/Movies".to_string()]);
        assert!(localized_candidates_for(r"D:\工作\文档", home, true).is_empty(), "别的盘不猜");
        assert!(localized_candidates_for(r"c:\users\张三\文档", home, false).is_empty(), "mac 上区分大小写");
        assert_eq!(localized_candidates_for("~/文稿", "/Users/z", false), vec!["~/Documents"], "mac 不试 OneDrive");
    }

    #[cfg(unix)]
    #[test]
    fn 没权限要说没权限_不说不存在() {
        use std::os::unix::fs::PermissionsExt;
        let _guard = env_lock();
        let home = tempfile::tempdir().unwrap();
        std::env::set_var("HOME", home.path());
        let locked = home.path().join("locked");
        std::fs::create_dir_all(locked.join("inner")).unwrap();
        std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o000)).unwrap();
        let enforced = std::fs::read_dir(&locked).is_err(); // root 跑测试时权限不生效, 那就不断言
        let r = resolve_input("~/locked/inner");
        let r2 = resolve_input("~/locked");
        std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o755)).unwrap();
        if enforced {
            assert!(r.unwrap_err().contains("权限"), "子目录被挡");
            assert!(r2.unwrap_err().contains("权限"), "目录本身读不了");
        }
    }

    #[test]
    fn get_set_include_roundtrip() {
        let mut root: YamlValue =
            serde_yaml::from_str("exclude:\n  - \"**/node_modules\"\n").unwrap();
        assert_eq!(get_string_list(&root, "include"), Vec::<String>::new());
        set_string_list(&mut root, "include", vec!["~/foo".to_string(), "~/bar".to_string()]);
        assert_eq!(
            get_string_list(&root, "include"),
            vec!["~/foo".to_string(), "~/bar".to_string()]
        );
        // exclude 段没丢
        assert!(root.get("exclude").is_some());
    }

    #[test]
    fn set_on_empty_yaml() {
        let mut root = YamlValue::Mapping(Default::default());
        set_string_list(&mut root, "include", vec!["~/x".to_string()]);
        assert_eq!(get_string_list(&root, "include"), vec!["~/x".to_string()]);
    }
}
