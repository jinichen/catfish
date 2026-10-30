//! 邮件调度的进程级状态: 评级缓存、push 历史、以及它们的落盘。
//!
//! 2026-08-15 从 email_scheduler.rs 切出来。纯搬迁, 逻辑一行未改。
//!
//! 两个 OnceLock (URGENCY_CACHE / PUSH_HISTORY) 加上 APP_HANDLE 都在这里 ——
//! 它们是"这个进程记住了什么", 跟调度循环本身是两回事。
//! push 历史落盘是为了让"这封急件今天已经吵过你一次"在重启 Companion 之后
//! 仍然成立。

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;
use tauri::AppHandle;

use super::phishing_scan::PhishingScanResult;

pub(crate) const DEDUP_WINDOW_SECS: u64 = 24 * 3600; // 24h
const URGENCY_CACHE_FILE: &str = "email_urgency.json";
const PUSH_HISTORY_FILE: &str = "email_push_history.json";

/// 解 ~/.catfish/<file>. None = HOME 找不到 (不发声明跳过持久化).
fn catfish_state_file(name: &str) -> Option<PathBuf> {
    // 9/23: Windows 没有 HOME, 原来这里直接返 None → 邮件状态从不持久化。
    let home = crate::util::paths::home_env().ok()?;
    let mut p = PathBuf::from(home);
    p.push(".catfish");
    p.push(name);
    Some(p)
}

pub(crate) fn now_epoch_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// 急邮件 push history: id → 上次 push 的 epoch 秒. 24h 内不重 push.
static PUSH_HISTORY: OnceLock<Mutex<HashMap<String, u64>>> = OnceLock::new();
pub(crate) fn push_history() -> &'static Mutex<HashMap<String, u64>> {
    PUSH_HISTORY.get_or_init(|| Mutex::new(HashMap::new()))
}

/// 启动时调一次 — load urgency_cache + push_history 从 disk.
/// 找不到文件 / 解析失败 → 静默, 当 empty (跟 5/18 老行为兼容).
pub(crate) fn load_persisted_state() {
    // 缓存重新从盘上装载 → 改名表要重新应用一次 (见 apply_id_migration)
    forget_applied_id_migration();
    if let Some(path) = catfish_state_file(URGENCY_CACHE_FILE) {
        if let Ok(content) = std::fs::read_to_string(&path) {
            if let Ok(parsed) = serde_json::from_str::<HashMap<String, String>>(&content) {
                if let Ok(mut cache) = urgency_cache().lock() {
                    *cache = parsed;
                    log::info!(
                        "email_scheduler: loaded {} urgency entries from {}",
                        cache.len(),
                        path.display()
                    );
                }
            }
        }
    }
    if let Some(path) = catfish_state_file(PUSH_HISTORY_FILE) {
        if let Ok(content) = std::fs::read_to_string(&path) {
            if let Ok(parsed) = serde_json::from_str::<HashMap<String, u64>>(&content) {
                if let Ok(mut hist) = push_history().lock() {
                    // 启动时顺手 GC 24h 之前的, 防 file 越涨越大
                    let cutoff = now_epoch_secs().saturating_sub(DEDUP_WINDOW_SECS);
                    *hist = parsed.into_iter().filter(|(_, ts)| *ts >= cutoff).collect();
                    log::info!(
                        "email_scheduler: loaded {} push history entries (GC 后)",
                        hist.len()
                    );
                }
            }
        }
    }
}

/// atomic write JSON map to ~/.catfish/<file>. 失败 log.debug, 不抛.
/// .tmp + rename 防中途崩坏 — 半写文件比丢全部新增 cache 更糟.
fn persist_json_atomic<T: Serialize>(file: &str, data: &T) {
    let Some(path) = catfish_state_file(file) else { return };
    let Some(parent) = path.parent() else { return };
    let _ = std::fs::create_dir_all(parent);
    let json = match serde_json::to_string_pretty(data) {
        Ok(s) => s,
        Err(e) => {
            log::debug!("persist_json {file}: serialize 失败 {e}");
            return;
        }
    };
    let tmp = path.with_extension("json.tmp");
    if let Err(e) = std::fs::write(&tmp, json) {
        log::debug!("persist_json {file}: 写 tmp 失败 {e}");
        return;
    }
    if let Err(e) = std::fs::rename(&tmp, &path) {
        log::debug!("persist_json {file}: rename 失败 {e}");
    }
}

pub(crate) fn persist_urgency_cache() {
    if let Ok(cache) = urgency_cache().lock() {
        persist_json_atomic(URGENCY_CACHE_FILE, &*cache);
    }
}

pub(crate) fn persist_push_history() {
    if let Ok(hist) = push_history().lock() {
        persist_json_atomic(PUSH_HISTORY_FILE, &*hist);
    }
}

/// app handle 句柄, 给 background task 用来 emit Tauri 事件给前端.
/// schedule_email_scheduler() 启动时存进来.
pub(crate) static APP_HANDLE: OnceLock<AppHandle> = OnceLock::new();

/// urgency 评级缓存 — scheduler 评完一封, 写这里给 Tauri command email_urgency_map() 读.
/// id -> "急" | "中" | "低" 字符串 (跟 SerializableUrgency 同套). 已读 → tick 时自然
/// 从 baseline 移除 → 下次评级也不会再被加进来. 缓存上限 200 防内存涨, 老的先丢.
static URGENCY_CACHE: OnceLock<Mutex<HashMap<String, String>>> = OnceLock::new();

pub(crate) fn urgency_cache() -> &'static Mutex<HashMap<String, String>> {
    URGENCY_CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

// ── 8/21 分诊升级: action 缓存 (知/回/办 + 截止日) ──────────────────────
//
// **平行新缓存, 不动 urgency 的形状** —— urgency 的 `HashMap<String,String>`
// 穿过 email_urgency.json / 前端 store / localStorage / ListItem badge 整条链,
// 而且 8/15 那次烧 2470 万 token 的永动机三处修复 (ratedRef / 上限 600 /
// store merge) 全是围着它做的。改值形状 = 全链重来, 收益为零。
//
// 上限跟 urgency 同一个常量 —— 8/15 教训: 缓存上限 (200) 低于列表上限 (500)
// 是永动机燃料之一 ("评完 → 缓存丢一半 → unrated 变多 → 再评")。

/// 一封邮件的行动分诊。serde 双向: 进 email_action.json / 出 Tauri command。
#[derive(Clone, Debug, Serialize, serde::Deserialize)]
pub(crate) struct ActionEntry {
    /// "知" / "回" / "办"
    pub(crate) action: String,
    /// YYYY-MM-DD, 只有 回/办 且邮件里有明确日期才有
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) deadline: Option<String>,
    /// 10/31: 分诊规则版本 (老文件没有 → 0)。规则改了, 旧规则判的在启动加载时丢掉重判。
    #[serde(default)]
    pub(crate) rev: u32,
}

/// 当前分诊规则版本。10/31 → 2: 加「收件」一栏 (直接 / 抄送 / 群发) 和群发通知默认 知。
pub(crate) const ACTION_RULES_REV: u32 = 2;

const ACTION_CACHE_FILE: &str = "email_action.json";
static ACTION_CACHE: OnceLock<Mutex<HashMap<String, ActionEntry>>> = OnceLock::new();

pub(crate) fn action_cache() -> &'static Mutex<HashMap<String, ActionEntry>> {
    ACTION_CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

pub(crate) fn persist_action_cache() {
    if let Ok(cache) = action_cache().lock() {
        persist_json_atomic(ACTION_CACHE_FILE, &*cache);
    }
}

/// 启动时 load (跟 load_persisted_state 同款, 单独一个函数免得改老函数签名)。
pub(crate) fn load_persisted_action_cache() {
    forget_applied_id_migration();
    if let Some(path) = catfish_state_file(ACTION_CACHE_FILE) {
        if let Ok(content) = std::fs::read_to_string(&path) {
            if let Ok(mut parsed) = serde_json::from_str::<HashMap<String, ActionEntry>>(&content) {
                // 10/31: 旧规则判的丢掉, 打开邮件页时按新规则重判 (只这一次, 之后 rev 对上就留着)
                let before = parsed.len();
                parsed.retain(|_, e| e.rev >= ACTION_RULES_REV);
                if let Ok(mut cache) = action_cache().lock() {
                    *cache = parsed;
                    log::info!(
                        "email_scheduler: loaded {} action entries from {} (旧规则判的 {} 封丢掉重判)",
                        cache.len(),
                        path.display(),
                        before - cache.len(),
                    );
                }
            }
        }
    }
}

// ── 10/30: 缓存超上限时怎么腾地方 ──────────────────────────────────────────
//
// 原来四处 (评级 ×2 / 分诊 / 钓鱼) 都是「超 600 就随便丢一半」: HashMap 的
// `keys().take(300)` 是随机的 300 个, 正在列表里显示的邮件也照丢。员工下次打开
// 邮件页, 被丢的那些就当成"没评过"重新送模型 —— 10/30 22:06 实测缓存从 598 掉到
// 323 后, 一打开邮件页连评 4 批 (每批 11–17 封, 约 1 万 token)。
//
// 现在: 上限抬到 URGENCY_CACHE_MAX (5000, 十倍于列表上限, 几个月才满一次); 真满了
// 先丢不在 `keep` (这一批刚评的) 里的, 丢到上限一半。
pub(crate) fn trim_cache<V>(map: &mut HashMap<String, V>, cap: usize, keep: &[&str]) {
    if map.len() <= cap {
        return;
    }
    let excess = map.len() - cap / 2;
    let victims: Vec<String> = map
        .keys()
        .filter(|k| !keep.contains(&k.as_str()))
        .take(excess)
        .cloned()
        .collect();
    for k in victims {
        map.remove(&k);
    }
}

// ── 10/30: 钓鱼扫描结果落盘 ──────────────────────────────────────────────
//
// 原来只在内存 (P3.3.58 段 2A 注释写着"段 2B/2C 再加 persist", 一直没加)。
// Companion 每重启一次, 员工打开邮件页就把整张列表 20 封一批重新送 LLM 复审 ——
// 10/30 20:15 一次重扫 10+ 批, 每批约 1 万 token, 早上 14:00 / 15:49 / 16:02 三次
// 重启后也各来一轮。评级 / 分诊早就落盘了 (email_urgency.json / email_action.json),
// 这里照同款补上。
const PHISHING_CACHE_FILE: &str = "email_phishing.json";
static PHISHING_STORE: OnceLock<Mutex<HashMap<String, PhishingScanResult>>> = OnceLock::new();

pub(crate) fn phishing_store() -> &'static Mutex<HashMap<String, PhishingScanResult>> {
    PHISHING_STORE.get_or_init(|| Mutex::new(HashMap::new()))
}

pub(crate) fn persist_phishing_store() {
    if let Ok(store) = phishing_store().lock() {
        persist_json_atomic(PHISHING_CACHE_FILE, &*store);
    }
}

/// 10/31: 按旧复审规则扫的丢掉, 打开邮件页时按新规则重扫一次 (之后 rev 对上就留着)。
/// 旧规则两处错: 本单位群发的预警被判营销; 「本单位域」把每个发件人都算进去, 冒充发件人的
/// 三条规则从没生效过 —— 已经在收件箱里的冒充邮件, 不重扫就一直漏着。
fn drop_stale_phishing(
    mut map: HashMap<String, PhishingScanResult>,
) -> (HashMap<String, PhishingScanResult>, usize) {
    let before = map.len();
    map.retain(|_, r| r.review_rev >= super::phishing_scan::PHISHING_REVIEW_REV);
    let dropped = before - map.len();
    (map, dropped)
}

pub(crate) fn load_persisted_phishing_store() {
    let Some(path) = catfish_state_file(PHISHING_CACHE_FILE) else { return };
    let Ok(content) = std::fs::read_to_string(&path) else { return };
    let Ok(parsed) = serde_json::from_str::<HashMap<String, PhishingScanResult>>(&content) else {
        return;
    };
    let (parsed, dropped) = drop_stale_phishing(parsed);
    if let Ok(mut store) = phishing_store().lock() {
        *store = parsed;
        log::info!(
            "email_scheduler: loaded {} phishing entries from {} (旧规则扫的 {} 封丢掉重扫)",
            store.len(),
            path.display(),
            dropped,
        );
    }
}

// ── 9/27: 邮件 id 迁移 (UIDVALIDITY 读错期间的旧 id → 核实后的新 id) ─────────
//
// catfish-email 以前把 IMAP 邮件 id 里的 UIDVALIDITY 一律写成 0 (读错了, 见
// email-agent adapters/imap_uidvalidity.py)。修好之后 id 会变成真值, 而上面三份
// 缓存 (急缓评级 / 行动分诊 / 推送去重) 都以 id 为键 —— 不跟着改名, 全部邮件在
// 缓存里都"没评过", 分诊会整批重新调模型。
//
// catfish-email 逐封核实 (按 UID 取 Message-ID 比对) 后把改名表导出成
// email_id_migration.json; 这里照表改键。只认这份表, 不自己猜: 表里没有的旧 id
// 是核实没通过的 (邮箱真的重建过), 它的评级本来就不该挪给新 UID 下的另一封信。

const ID_MIGRATION_FILE: &str = "email_id_migration.json";
static ID_MIGRATION_APPLIED: OnceLock<Mutex<Option<SystemTime>>> = OnceLock::new();

fn forget_applied_id_migration() {
    if let Some(applied) = ID_MIGRATION_APPLIED.get() {
        if let Ok(mut seen) = applied.lock() {
            *seen = None;
        }
    }
}

/// 改名表 {旧 id: 新 id}。没有文件 / 读不了 = 空表。
pub(crate) fn id_migration_map() -> HashMap<String, String> {
    catfish_state_file(ID_MIGRATION_FILE)
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}

/// 照表改键。新键已经有值时保留新键的值 (新 id 下评过的更可信)。返回是否改了。
pub(crate) fn rename_keys<V>(map: &mut HashMap<String, V>, mapping: &HashMap<String, String>) -> bool {
    let mut changed = false;
    for (old, new) in mapping {
        if let Some(value) = map.remove(old) {
            map.entry(new.clone()).or_insert(value);
            changed = true;
        }
    }
    changed
}

/// 改名表有更新就应用到三份缓存并落盘。调用点: 评级 / 读评级 / 取列表之前,
/// 保证"拿新 id 去查缓存"之前缓存已经是新键。文件没变就什么都不做 (一次 stat)。
pub(crate) fn apply_id_migration() {
    let Some(path) = catfish_state_file(ID_MIGRATION_FILE) else { return };
    let Ok(modified) = std::fs::metadata(&path).and_then(|m| m.modified()) else { return };
    let applied = ID_MIGRATION_APPLIED.get_or_init(|| Mutex::new(None));
    if applied.lock().map(|seen| *seen == Some(modified)).unwrap_or(true) {
        return;
    }
    let mapping = id_migration_map();
    let urgency = urgency_cache().lock().map(|mut c| rename_keys(&mut *c, &mapping)).unwrap_or(false);
    let action = action_cache().lock().map(|mut c| rename_keys(&mut *c, &mapping)).unwrap_or(false);
    let push = push_history().lock().map(|mut c| rename_keys(&mut *c, &mapping)).unwrap_or(false);
    let phishing = phishing_store().lock().map(|mut c| rename_keys(&mut *c, &mapping)).unwrap_or(false);
    if urgency {
        persist_urgency_cache();
    }
    if action {
        persist_action_cache();
    }
    if push {
        persist_push_history();
    }
    if phishing {
        persist_phishing_store();
    }
    if let Ok(mut seen) = applied.lock() {
        *seen = Some(modified);
    }
    log::info!(
        "email id 迁移: 表 {} 条, 改了 urgency={urgency} action={action} push={push} phishing={phishing}",
        mapping.len()
    );
}

#[cfg(test)]
mod tests_id_migration {
    use super::rename_keys;
    use std::collections::HashMap;

    #[test]
    fn renames_only_listed_ids_and_keeps_newer_value() {
        let mapping: HashMap<String, String> = [
            ("imap|INBOX|0|1".to_string(), "imap|INBOX|1|1".to_string()),
            ("imap|INBOX|0|2".to_string(), "imap|INBOX|1|2".to_string()),
        ].into_iter().collect();
        let mut cache: HashMap<String, String> = [
            ("imap|INBOX|0|1".to_string(), "急".to_string()),
            ("imap|INBOX|0|2".to_string(), "低".to_string()),
            ("imap|INBOX|1|2".to_string(), "中".to_string()),
            ("imap|INBOX|0|9".to_string(), "低".to_string()),
        ].into_iter().collect();
        assert!(rename_keys(&mut cache, &mapping));
        assert_eq!(cache.get("imap|INBOX|1|1").map(String::as_str), Some("急"));
        assert_eq!(cache.get("imap|INBOX|1|2").map(String::as_str), Some("中"), "新键已有值时保留");
        assert!(cache.contains_key("imap|INBOX|0|9"), "表里没有的旧 id 不动");
        assert!(!cache.contains_key("imap|INBOX|0|1"));
        assert!(!rename_keys(&mut cache, &mapping), "再跑一遍什么都不改");
    }
}

#[cfg(test)]
mod tests_phishing_persist {
    use super::PhishingScanResult;
    use std::collections::HashMap;

    /// 落盘用的是 serde_json pretty, 启动 load 用 from_str —— 两头格式必须对得上,
    /// 否则 load 静默失败, 又回到每次重启整表重扫。
    #[test]
    fn phishing_store_round_trips_through_json() {
        let mut m = HashMap::new();
        m.insert(
            "imap:1:42".to_string(),
            PhishingScanResult {
                message_id: "imap:1:42".into(),
                llm_verdict: Some("marketing".into()),
                ..Default::default()
            },
        );
        let json = serde_json::to_string_pretty(&m).unwrap();
        let back: HashMap<String, PhishingScanResult> = serde_json::from_str(&json).unwrap();
        assert_eq!(back["imap:1:42"].llm_verdict.as_deref(), Some("marketing"));
    }
}

#[cfg(test)]
mod tests_trim_cache {
    use super::trim_cache;
    use std::collections::HashMap;

    #[test]
    fn 没超上限不动() {
        let mut m: HashMap<String, u8> = (0..10).map(|i| (i.to_string(), 0)).collect();
        trim_cache(&mut m, 10, &[]);
        assert_eq!(m.len(), 10);
    }

    #[test]
    fn 超了丢到一半_刚评的那批一个不丢() {
        let mut m: HashMap<String, u8> = (0..11).map(|i| (i.to_string(), 0)).collect();
        let keep = ["0", "1", "2", "3", "4"];
        trim_cache(&mut m, 10, &keep);
        assert_eq!(m.len(), 5);
        for k in keep {
            assert!(m.contains_key(k), "{k} 是刚评的, 不该被丢");
        }
    }
}

#[cfg(test)]
mod tests_phishing_rev {
    use super::{drop_stale_phishing, PhishingScanResult};
    use std::collections::HashMap;

    fn r(verdict: &str, rev: u32) -> PhishingScanResult {
        PhishingScanResult { llm_verdict: Some(verdict.into()), review_rev: rev, ..Default::default() }
    }

    #[test]
    fn 旧规则扫的都丢_新规则的留着() {
        let rev = super::super::phishing_scan::PHISHING_REVIEW_REV;
        let mut m = HashMap::new();
        m.insert("old-mkt".to_string(), r("marketing", 0));
        m.insert("old-safe".to_string(), r("safe", 0));
        m.insert("new-mkt".to_string(), r("marketing", rev));
        let (m, dropped) = drop_stale_phishing(m);
        assert_eq!(dropped, 2);
        assert!(m.contains_key("new-mkt") && m.len() == 1);
    }
}
