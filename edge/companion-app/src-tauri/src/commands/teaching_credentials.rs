//! 教学流程的本地凭据保存。
//!
//! 密码只通过 Tauri IPC 到 Rust，写入操作系统凭据库。这里绝不记录密码，
//! 也不把密码放进 shell 参数、配置文件或聊天消息。
//!
//! # 为什么要单独维护一份标签索引
//!
//! keyring 3.6.3 的 `Entry` 只有 new / set_password / get_password / get_secret /
//! get_attributes / update_attributes / delete_credential / get_credential ——
//! **没有任何枚举 API**。底层也不好绕: macOS 的 `security` 命令不支持按 service
//! 前缀搜索, `dump-keychain` 会把整个钥匙串倒出来还要用户授权, 拿来做个列表
//! 完全不成比例。
//!
//! 所以标签列表另存一份 `~/.catfish/teaching_credentials.json`。里面**只有标签、
//! 引用串和创建时间, 没有密码** —— 密码始终只在系统凭据库里。
//!
//! ## 索引不是真源
//!
//! 系统凭据库才是。索引可能跟它对不上:
//!   - 员工在「钥匙串访问」里手工删了某一条 → 索引留下残项
//!   - 换了台机器同步过来配置 → 索引在但凭据不在
//!
//! 处理原则:
//!   - **list 绝不去验证凭据还在不在**。验证要调 get_password, 而 macOS 每次
//!     都可能弹授权框 —— 打开个列表弹一串授权框是不能接受的。列表如实说明
//!     "这是本机记过的标签"。
//!   - **delete 对「凭据库里已经没有」容错**, 照样把索引清掉。不然残项永远删不掉。

use keyring::Entry;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

const ACCOUNT: &str = "catfish-teaching";
const SERVICE_PREFIX: &str = "catfish-teaching:";

/// secret_resolver 认得的 scheme (secret_resolver.py:95-102)。
/// 名称框里出现它们 = 员工粘的是引用串不是名字。
const SECRET_SCHEMES: [&str; 3] = ["keychain://", "wincred://", "env://"];

/// 把员工填进"名称"框的东西收敛成一个**纯名字**。
///
/// # 为什么需要这一步 (8/17 实盘撞的)
///
/// 员工机器上出现过这么一条:
///
///   label     = keychain://catfish-teaching:http://eis.ffcs.cn
///   reference = keychain://catfish-teaching:keychain://catfish-teaching:http://eis.ffcs.cn
///
/// 前缀套了两层。成因是这个循环:
///   ① 保存后界面显示"安全引用", 旁边有「复制引用」
///   ② 员工把引用粘回"名称"框 (或者点一下 `本机已存` 那一行 ——
///      EduPopover.tsx 的 `setLabel(item.label)` 会把标签填回输入框)
///   ③ 再存一次 → reference_for(service_name(label)) 又包一层
///   ④ 新的 label 本身又是个引用 → 回到 ②, **每点一次多一层**
///
/// 自洽但没用: 套两层的引用确实能解析 (resolver 的 partition("://") 只切第一个),
/// 但它跟教学流程里写的那个引用对不上, 表现就是"存了也白存"。
fn normalize_label(raw: &str) -> Result<String, String> {
    let mut s = raw.trim();

    // 把本 UI 自己产出的引用剥回纯名字。粘回来的意图是"覆盖这一条", 该让它成立。
    // 循环剥 —— 已经套了两层的历史数据也要能救回来。上限防手工构造的病态输入。
    for _ in 0..8 {
        let before = s;
        for scheme in ["keychain://", "wincred://"] {
            if let Some(rest) = s.strip_prefix(scheme) {
                // 只认**我们自己的命名空间**。别的 scheme+名字剥了会改变含义,
                // 见下面那个 Err。
                if let Some(inner) = rest.strip_prefix(SERVICE_PREFIX) {
                    s = inner;
                }
            }
        }
        if s == before {
            break;
        }
    }

    // 剥完还以 secret scheme 开头 = 员工粘的是**别处**的引用
    // (例 keychain://eis_password, 那是 TEACHING-SOP.md:107 手工
    // `security add-generic-password -s eis_password` 那一套)。
    // 本 UI 产不出那种名字 —— 它总会加 catfish-teaching: 前缀。悄悄剥成
    // eis_password 再加前缀会变成 catfish-teaching:eis_password, 名字对不上,
    // 又是一次"存了也白存"。所以明说, 不猜。
    //
    // ⚠ 判据是"以 secret scheme 开头", 不是"含 ://"。
    //   含 :// 太宽 —— `http://eis.ffcs.cn` 本身就是个合理的名字 (员工机器上
    //   那条坏记录剥完正好是它), 用宽判据会连"把坏数据删掉"这条路一起堵死。
    if SECRET_SCHEMES.iter().any(|p| s.starts_with(*p)) {
        return Err(format!(
            "「{s}」看起来是一个引用串, 不是名称。\n\
             这里只填名字 (例: EIS / OA), 引用串由程序拼出来。\n\
             如果教学流程要的是别的命名空间 (例 keychain://eis_password), \
             本界面产不出那种名字 —— 见 docs/TEACHING-SOP.md 的手工命令。"
        ));
    }

    if s.is_empty() {
        return Err("请填写凭据名称".to_string());
    }
    if s.len() > 80 || s.chars().any(|c| c.is_control()) {
        return Err("凭据名称过长或包含不可用字符".to_string());
    }
    Ok(s.to_string())
}

/// 归一化 + 加命名空间前缀。**返两个值, 两个都有人用**:
///   .0 纯名字   → 写进标签索引 (存原样的话"名字是引用串"那条会一直留着)
///   .1 service  → 系统凭据库的 service 名
///
/// 8/17 第一版把这个函数拆散写进两个命令里, 结果它只剩测试在调 ——
/// `cargo test` 直接报 `function service_name is never used`。
/// 生产不用、只有测试用的函数就是死代码, 而且测试会因此测了一条没人走的路。
fn service_name(label: &str) -> Result<(String, String), String> {
    let name = normalize_label(label)?;
    let target = format!("{SERVICE_PREFIX}{name}");
    Ok((name, target))
}

/// 引用串 —— 教学流程里 `secret_ref` 用的就是这个。
/// scheme 必须跟 tool-bridge 的 secret_resolver 对得上:
///   macOS   keychain:// → `security find-generic-password -s <target> -w`
///   Windows wincred://  → `keyring.get_password("catfish", <target>)`
fn reference_for(target: &str) -> String {
    #[cfg(target_os = "windows")]
    {
        format!("wincred://{target}")
    }
    #[cfg(not(target_os = "windows"))]
    {
        format!("keychain://{target}")
    }
}

/// 打开凭据库条目。service / user 两个位置的用法必须跟 secret_resolver 一致,
/// 写进去读不出来就白存了 (两边都对过: macOS 查 service 不带 -a, Windows 查
/// service="catfish" + user=target)。
fn entry_for(target: &str) -> Result<Entry, String> {
    #[cfg(target_os = "windows")]
    let entry = Entry::new("catfish", target);
    #[cfg(not(target_os = "windows"))]
    let entry = Entry::new(target, ACCOUNT);
    entry.map_err(|e| format!("无法打开本机凭据库: {e}"))
}

// ─── 标签索引 (只有标签, 没有密码) ────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TeachingCredential {
    /// 员工填的名称, 例 "教学网站"
    pub label: String,
    /// 教学流程里粘贴的那个 secret_ref
    pub reference: String,
    /// ISO-8601, 只用于列表排序和"这是什么时候存的"
    pub created_at: String,
}

fn index_path() -> Result<PathBuf, String> {
    let home = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .ok_or("HOME 未设")?;
    let dir = PathBuf::from(home).join(".catfish");
    std::fs::create_dir_all(&dir).map_err(|e| format!("创建 ~/.catfish/ 失败: {e}"))?;
    Ok(dir.join("teaching_credentials.json"))
}

/// 读索引。读不出来一律当空 —— 索引坏掉不该让员工连保存都做不了,
/// 凭据本体在系统凭据库里, 没受影响。
fn read_index() -> Vec<TeachingCredential> {
    let Ok(path) = index_path() else {
        return Vec::new();
    };
    let Ok(text) = std::fs::read_to_string(&path) else {
        return Vec::new();
    };
    if text.trim().is_empty() {
        return Vec::new();
    }
    serde_json::from_str(&text).unwrap_or_else(|e| {
        log::warn!("teaching_credentials.json 解析失败, 当空处理: {e}");
        Vec::new()
    })
}

fn write_index(items: &[TeachingCredential]) -> Result<(), String> {
    let path = index_path()?;
    let text =
        serde_json::to_string_pretty(items).map_err(|e| format!("serialize 失败: {e}"))?;
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, text).map_err(|e| format!("写 tmp 失败: {e}"))?;
    // 索引里没有密码, 0600 是保守做法不是必需 —— 但它确实泄露"这台机器上
    // 存过哪些系统的凭据", 没必要让同机其它用户看见。
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600));
    }
    std::fs::rename(&tmp, &path).map_err(|e| format!("rename atomic 失败: {e}"))?;
    Ok(())
}

/// 同名的换掉, 没有的追加。返回更新后的列表。
fn upsert(mut items: Vec<TeachingCredential>, item: TeachingCredential) -> Vec<TeachingCredential> {
    // 用 position() 先拿下标, 不用 iter_mut().find() ——
    // `if let Some(x) = v.iter_mut().find(..) { } else { v.push(..) }` 是借用
    // 检查过不去的经典写法: 可变借用被认为在 else 分支里仍然存活。
    match items.iter().position(|i| i.label == item.label) {
        Some(idx) => {
            // created_at 保留最早那次 —— 覆盖密码不算"新建"
            let created = items[idx].created_at.clone();
            items[idx] = TeachingCredential { created_at: created, ..item };
        }
        None => items.push(item),
    }
    items
}

// ─── Tauri 命令 ──────────────────────────────────────────────────

#[tauri::command]
pub fn teaching_credential_save(label: String, password: String) -> Result<String, String> {
    // 归一化一次, 后面 service / 索引都用它 —— 8/17 之前索引存的是**原样**,
    // 于是"名字本身是引用串"那条会一直留在列表里, 点一下又填回输入框, 再存
    // 就多套一层。存归一化后的名字, 这个循环就断了。
    let (name, target) = service_name(&label)?;
    if password.is_empty() {
        return Err("请填写密码".to_string());
    }
    entry_for(&target)?
        .set_password(&password)
        .map_err(|e| format!("保存到本机凭据库失败: {e}"))?;

    let reference = reference_for(&target);
    // 索引写失败不能让"密码已经存进去了"这件事变成报错 —— 密码是主线,
    // 索引只是为了后面能列出来。写不进去就记一条日志, 员工仍然拿得到引用串。
    if let Err(e) = write_index(&upsert(
        read_index(),
        TeachingCredential {
            label: name.clone(),
            reference: reference.clone(),
            created_at: chrono::Utc::now().to_rfc3339(),
        },
    )) {
        log::warn!("凭据已存入系统凭据库, 但标签索引没写成: {e}");
    }
    Ok(reference)
}

/// 列出本机记过的标签。**不校验凭据库里是否还在** —— 见文件头。
#[tauri::command]
pub fn teaching_credential_list() -> Result<Vec<TeachingCredential>, String> {
    let mut items = read_index();
    items.sort_by(|a, b| b.created_at.cmp(&a.created_at)); // 新的在前
    Ok(items)
}

/// 删一条。凭据库里已经没有也算成功 —— 否则手工删过的残项永远清不掉。
#[tauri::command]
pub fn teaching_credential_delete(label: String) -> Result<(), String> {
    let (name, target) = service_name(&label)?;
    match entry_for(&target)?.delete_credential() {
        Ok(()) => {}
        Err(keyring::Error::NoEntry) => {
            log::info!("凭据库里已无此条 ({target}), 只清索引");
        }
        Err(e) => return Err(format!("从本机凭据库删除失败: {e}")),
    }
    // 索引清理**同时认两种写法**:
    //   name = 归一化后的 (8/17 之后存进去的都长这样)
    //   raw  = 员工点「删除」时传进来的原样 —— 历史上存过"名字本身是引用串"
    //          的条目 (8/17 那条 keychain://catfish-teaching:http://eis.ffcs.cn),
    //          只按 name 过滤的话那种残项永远清不掉。
    let raw = label.trim();
    let kept: Vec<TeachingCredential> = read_index()
        .into_iter()
        .filter(|i| i.label != name && i.label != raw)
        .collect();
    write_index(&kept)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_namespaced_service_name() {
        assert_eq!(service_name("教学登录").unwrap().1, "catfish-teaching:教学登录");
    }

    #[test]
    fn rejects_empty_name() {
        assert!(service_name("  ").is_err());
    }

    // ── 8/17: 引用串套娃 ─────────────────────────────────────────
    //
    // 员工机器上真出现过 (截图 + ~/.catfish/teaching_credentials.json):
    //   label     = keychain://catfish-teaching:http://eis.ffcs.cn
    //   reference = keychain://catfish-teaching:keychain://catfish-teaching:http://eis.ffcs.cn
    // 而且会自我放大 —— 点一次 `本机已存` 就把引用填回名称框, 再存又一层。

    #[test]
    fn strips_our_own_reference_pasted_back() {
        // 粘回来的意图是"覆盖这一条", 该跟直接填名字得到同一个 service
        assert_eq!(
            service_name("keychain://catfish-teaching:EIS").unwrap().1,
            service_name("EIS").unwrap().1,
        );
    }

    #[test]
    fn unwraps_already_doubled_history() {
        // 已经套了两层的历史数据要能救回来, 否则那条记录永远删不掉
        let doubled = "keychain://catfish-teaching:keychain://catfish-teaching:EIS";
        assert_eq!(service_name(doubled).unwrap().1, "catfish-teaching:EIS");
    }

    #[test]
    fn name_containing_a_url_is_a_valid_name() {
        // ★ 判据必须是"以 secret scheme 开头", 不是"含 ://"。
        //   员工机器上那条坏记录剥完正好是 http://eis.ffcs.cn —— 用宽判据会把
        //   "清理坏数据"这条路一起堵死。
        assert_eq!(
            service_name("keychain://catfish-teaching:http://eis.ffcs.cn").unwrap().1,
            "catfish-teaching:http://eis.ffcs.cn",
        );
        assert!(service_name("http://eis.ffcs.cn").is_ok());
    }

    #[test]
    fn rejects_foreign_namespace_reference() {
        // keychain://eis_password 是 TEACHING-SOP.md:107 手工那一套。
        // 本 UI 产不出那种名字 (总会加 catfish-teaching:), 悄悄剥成
        // catfish-teaching:eis_password 名字对不上 —— 明说, 不猜。
        let e = service_name("keychain://eis_password").unwrap_err();
        assert!(e.contains("引用串"), "错误信息要说清是引用串不是名称: {e}");
        assert!(service_name("env://EIS_PASSWORD").is_err());
    }

    #[test]
    fn reference_never_doubles_the_prefix() {
        // 兜底: 不管输入怎么套, 产出的引用里前缀只能出现一次
        for input in [
            "EIS",
            "keychain://catfish-teaching:EIS",
            "keychain://catfish-teaching:keychain://catfish-teaching:EIS",
        ] {
            let r = reference_for(&service_name(input).unwrap().1);
            assert_eq!(
                r.matches(SERVICE_PREFIX).count(),
                1,
                "输入 {input:?} 产出 {r:?} —— 前缀出现了不止一次",
            );
        }
    }

    #[test]
    fn reference_scheme_matches_secret_resolver() {
        // tool-bridge/secret_resolver.py 只认这两个 scheme。这里拼错了,
        // 员工拿到的引用串在教学时会 resolve 失败, 而失败信息还会把人
        // 引导去命令行 —— 正好绕开这个界面存在的理由。
        let r = reference_for("catfish-teaching:教学登录");
        assert!(
            r.starts_with("keychain://") || r.starts_with("wincred://"),
            "引用串 scheme 不对: {r}"
        );
        assert!(r.ends_with("catfish-teaching:教学登录"));
    }

    fn cred(label: &str, created: &str) -> TeachingCredential {
        TeachingCredential {
            label: label.to_string(),
            reference: format!("keychain://catfish-teaching:{label}"),
            created_at: created.to_string(),
        }
    }

    #[test]
    fn upsert_replaces_same_label_and_keeps_created_at() {
        // 同名再存一次 = 覆盖密码, 不是新建。created_at 要留最早那次,
        // 不然列表里"最近存的"排序会骗人。
        let items = vec![cred("EIS", "2026-08-01T00:00:00Z")];
        let out = upsert(items, cred("EIS", "2026-08-10T00:00:00Z"));
        assert_eq!(out.len(), 1, "同名不该出现两条");
        assert_eq!(out[0].created_at, "2026-08-01T00:00:00Z");
    }

    #[test]
    fn upsert_appends_new_label() {
        let items = vec![cred("EIS", "2026-08-01T00:00:00Z")];
        let out = upsert(items, cred("OA", "2026-08-10T00:00:00Z"));
        assert_eq!(out.len(), 2);
        assert!(out.iter().any(|i| i.label == "OA"));
    }

    #[test]
    fn index_never_carries_a_password_field() {
        // 这条是红线的结构化表达: 索引文件是明文 JSON, 里面**只能**有
        // label / reference / createdAt 三个字段。哪天有人往
        // TeachingCredential 上加个 password, 这里会红。
        let json = serde_json::to_string(&cred("EIS", "2026-08-01T00:00:00Z")).unwrap();
        let v: serde_json::Value = serde_json::from_str(&json).unwrap();
        // 排序后再比 —— serde_json 默认用 BTreeMap, 键序是字典序不是声明序,
        // 按声明序断言会挂在一个跟本意无关的地方。
        let mut keys: Vec<&str> = v.as_object().unwrap().keys().map(|s| s.as_str()).collect();
        keys.sort_unstable();
        assert_eq!(
            keys,
            vec!["createdAt", "label", "reference"],
            "索引结构变了 —— 密码只能待在系统凭据库里, 绝不能进这个文件"
        );
    }
}
