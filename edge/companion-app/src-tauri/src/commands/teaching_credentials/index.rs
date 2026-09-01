//! 标签索引的**纯逻辑** —— 名字归一化 / 站点归一化 / 合并 / 读写。
//!
//! # 为什么单独一个文件
//!
//! 这里一行 tauri、一行 keyring 都没有。因为 tauri 在 Linux 上要链 GTK,
//! 而 CI / 沙箱里装不了 —— 逻辑跟它绑在一起, `cargo test` 就一次都跑不了。
//!
//! 8/18 撞到的就是这个: 站点判据是这次改动里最容易出错的一块 (见 normalize_site),
//! 却因为整个 crate 编不动而完全没法验。拆开之后这些函数可以脱离 GUI 依赖跑,
//! 见 `companion-app/pure-tests/`。
//!
//! 上面那层 (`mod.rs`) 只剩四个 #[tauri::command] 和一个 keyring 的 entry_for。

use serde::{Deserialize, Serialize};
use std::path::PathBuf;
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub(crate) const ACCOUNT: &str = "catfish-teaching";
/// 教学凭据在系统凭据库里的命名空间。**这是唯一一份定义** —— socket_proto.rs 的
/// 白名单直接 use 它, 不另抄。抄一份的后果是"存进去了但取不出来", 而且两边各自
/// 看都正常 (8/17 那个 bug 的形状)。
pub(crate) const SERVICE_PREFIX: &str = "catfish-teaching:";

/// 浏览器地址栏只可能是这两个。见 `normalize_site` 里为什么必须限定。
const WEB_SCHEMES: [&str; 2] = ["http", "https"];

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
pub(crate) fn normalize_label(raw: &str) -> Result<String, String> {
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
pub(crate) fn service_name(label: &str) -> Result<(String, String), String> {
    let name = normalize_label(label)?;
    let target = format!("{SERVICE_PREFIX}{name}");
    Ok((name, target))
}

/// 把员工填的东西收敛成一个 **hostname**。取不出来返 Err。
///
/// # 判据只有一份
///
/// tool-bridge 的 `credential_sites.host_of()` 是同一套规则, 两边的测试都读
/// `edge/shared-fixtures/hostname_cases.json`。
///
/// 必须是同一套 —— 存进去的 key 和查的 key 对不上, 表现就是「员工明明存了,
/// 教学还说没存过」, 而两边各自看都正常。这正是 8/17 那个 bug 的形状。
///
/// # 三条不能放宽的
///
///   · **限定 http/https**。`"keychain://catfish-teaching:EIS"` 拿去当 URL 解析,
///     `catfish-teaching` 会被当成 host 且全程不报错 —— 凭空造出一个假站点,
///     任何 host 是它的页面都会拿到别人的密码。8/17 那条坏 label 正是这形状。
///   · **不剥 `www.`**。两个 host 就是两个, 要一起用就在 UI 里显式加。
///   · **裸串要求含 `.`**。否则 "EIS" / "教学登录" 这种员工起的名字会被当成
///     单标签域名, 索引里就多出一堆匹配不到任何真站点的鬼条目。
pub(crate) fn normalize_site(raw: &str) -> Result<String, String> {
    let s = raw.trim();
    if s.is_empty() {
        return Err("站点不能为空".to_string());
    }
    if s.contains("://") {
        let parsed = url::Url::parse(s).map_err(|e| format!("「{s}」不是合法网址: {e}"))?;
        if !WEB_SCHEMES.contains(&parsed.scheme()) {
            return Err(format!(
                "「{s}」不是网页地址 (只认 http / https)。\n\
                 这里填的是网站, 例: eis.ffcs.cn 或 http://eis.ffcs.cn/"
            ));
        }
        // Url 对 IPv6 返 "[::1]", 而 Python 的 urlparse().hostname 返 "::1"。
        // 两边要一致, 以 Python 为准 —— 查找那侧是它说了算。
        let host = parsed
            .host_str()
            .ok_or_else(|| format!("「{s}」里没有网站地址"))?
            .trim_start_matches('[')
            .trim_end_matches(']')
            .to_lowercase();
        return Ok(host);
    }
    if s.contains('.') && !s.contains(' ') && !s.contains('/') {
        return Ok(s.to_lowercase());
    }
    Err(format!(
        "「{s}」看不出是哪个网站。\n\
         填网站地址, 例: eis.ffcs.cn / http://eis.ffcs.cn/ —— 不是给它起的名字。"
    ))
}

/// 归一化一串站点, 去重保序。空列表原样返回 (= 这条凭据不参与按站点查找)。
pub(crate) fn normalize_sites(raw: &[String]) -> Result<Vec<String>, String> {
    let mut out: Vec<String> = Vec::with_capacity(raw.len());
    for s in raw {
        let h = normalize_site(s)?;
        if !out.contains(&h) {
            out.push(h);
        }
    }
    Ok(out)
}

/// 引用串 —— 教学流程里 `secret_ref` 用的就是这个。
/// scheme 必须跟 tool-bridge 的 secret_resolver 对得上:
///   macOS   keychain:// → `security find-generic-password -s <target> -w`
///   Windows wincred://  → `keyring.get_password("catfish", <target>)`
pub(crate) fn reference_for(target: &str) -> String {
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
    /// 这条凭据管哪些网站 (hostname)。tool-bridge 按 `page.url` 查的就是它。
    ///
    /// 空 = 不参与按站点查找。老数据都是空的, 但 `credential_sites.sites_of()`
    /// 会退回把 `label` 当 URL 解析一次 —— 8/17 存的那条 label 正好是
    /// `http://eis.ffcs.cn`, 所以不迁移也能立刻匹配上。
    ///
    /// `skip_serializing_if` 是为了让没配站点的老行**保持原样三个字段**,
    /// 不平白多出一个 `"sites": []`。
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub sites: Vec<String>,
}

fn index_path() -> Result<PathBuf, String> {
    let home = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .ok_or("HOME 未设")?;
    let dir = PathBuf::from(home).join(".catfish");
    std::fs::create_dir_all(&dir).map_err(|e| format!("创建 ~/.catfish/ 失败: {e}"))?;
    Ok(dir.join("teaching_credentials.json"))
}

/// 把历史上写进去的"名字是引用串"的条目就地归一化, 并合并重名 (8/17)。
///
/// # 为什么读的时候做, 而不是等下次保存
///
/// 8/17 只加了 normalize_label 之后, 新存的是干净名字, **老那条还躺在索引里**,
/// 于是列表变成两行:
///
/// ```text
/// http://eis.ffcs.cn
/// keychain://catfish-teaching:http://eis.ffcs.cn
/// ```
///
/// ⚠ 上面这个 fence 必须带 `text`。不带的话 rustdoc 会把缩进块当 Rust 代码去编,
///   doctest 直接报 `expected one of ! or ::` —— 8/18 拆文件后第一次真跑
///   `cargo test` 就撞到了 (在这之前 tauri 依赖让整个 crate 在 Linux 上编不动,
///   这条 doctest 从写下那天起就没运行过)。
///
/// 两行指的是同一件事, 看着比修之前还乱。而且点老那行的「删除」会归一化成
/// 同一个 service, 把**新**那条的凭据删掉 —— 点"删垃圾"删掉了好的。
///
/// 归一化后重名的只留一条, created_at 取最早的 (跟 upsert 一个语义:
/// 覆盖密码不算新建)。reference 按归一化后的名字**重算**, 不能沿用旧值 ——
/// 旧值正是那个套了两层的串。
///
/// ⚠ 归一化不了的 (例如手工塞进去的怪数据) **原样留着**, 不丢。
///   列表里能看见, 也就还能点删除清掉。
fn migrate_index(items: Vec<TeachingCredential>) -> Vec<TeachingCredential> {
    let mut out: Vec<TeachingCredential> = Vec::with_capacity(items.len());
    for it in items {
        // 站点归一化不了的**丢掉那一个**, 不是丢整行 —— 整行没了凭据就删不掉了。
        // 留着一个匹配不到任何页面的假站点更糟: 它会静默地什么都不匹配。
        let sites = it
            .sites
            .iter()
            .filter_map(|s| normalize_site(s).ok())
            .fold(Vec::new(), |mut acc: Vec<String>, h| {
                if !acc.contains(&h) {
                    acc.push(h);
                }
                acc
            });
        let fixed = match service_name(&it.label) {
            Ok((name, target)) => TeachingCredential {
                label: name,
                reference: reference_for(&target),
                created_at: it.created_at,
                sites,
            },
            Err(_) => TeachingCredential { sites, ..it },
        };
        match out.iter().position(|x| x.label == fixed.label) {
            Some(i) => {
                if fixed.created_at < out[i].created_at {
                    out[i].created_at = fixed.created_at;
                }
                // ★ 被合掉那行的站点要并进来, 不能跟着它一起消失。
                //   两行本来就指同一条凭据 (归一化后同名), 站点是并集。
                for s in fixed.sites {
                    if !out[i].sites.contains(&s) {
                        out[i].sites.push(s);
                    }
                }
            }
            None => out.push(fixed),
        }
    }
    out
}

/// 读索引。读不出来一律当空 —— 索引坏掉不该让员工连保存都做不了,
/// 凭据本体在系统凭据库里, 没受影响。
pub(crate) fn read_index() -> Vec<TeachingCredential> {
    let Ok(path) = index_path() else {
        return Vec::new();
    };
    let Ok(text) = std::fs::read_to_string(&path) else {
        return Vec::new();
    };
    if text.trim().is_empty() {
        return Vec::new();
    }
    let parsed: Vec<TeachingCredential> = serde_json::from_str(&text).unwrap_or_else(|e| {
        log::warn!("teaching_credentials.json 解析失败, 当空处理: {e}");
        Vec::new()
    });
    migrate_index(parsed)
}

pub(crate) fn write_index(items: &[TeachingCredential]) -> Result<(), String> {
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
///
/// `item.sites` 为空表示**这次调用没提站点**, 不表示"要清空站点"。
/// 改密码时前端只传 label + password, 站点得原样留着。
pub(crate) fn upsert(mut items: Vec<TeachingCredential>, item: TeachingCredential) -> Vec<TeachingCredential> {
    // 用 position() 先拿下标, 不用 iter_mut().find() ——
    // `if let Some(x) = v.iter_mut().find(..) { } else { v.push(..) }` 是借用
    // 检查过不去的经典写法: 可变借用被认为在 else 分支里仍然存活。
    match items.iter().position(|i| i.label == item.label) {
        Some(idx) => {
            // created_at 保留最早那次 —— 覆盖密码不算"新建"
            let created = items[idx].created_at.clone();
            // ★ 站点同理, 而且这条更要紧: `..item` 会把它**静默清空**。
            //   编译得过、测试不看就过 —— 表现是员工改完密码, 多入口那几个
            //   站点全部失联, 而他只是改了个密码。
            let sites = if item.sites.is_empty() {
                items[idx].sites.clone()
            } else {
                item.sites.clone()
            };
            items[idx] = TeachingCredential { created_at: created, sites, ..item };
        }
        None => items.push(item),
    }
    items
}

/// 这个站点已经被别的凭据管着了吗。返回那条的 label。
///
/// 同一个站点挂两条凭据时, `credential_sites.ref_for_url()` 取 createdAt 最新的 ——
/// 能跑, 但员工看不出来为什么用的是那条。所以宁可在写入时拦下来让他自己决定,
/// 不替他猜。
pub(crate) fn site_owner(items: &[TeachingCredential], site: &str, except: &str) -> Option<String> {
    items
        .iter()
        .find(|i| i.label != except && i.sites.iter().any(|s| s == site))
        .map(|i| i.label.clone())
}

// ─── Tauri 命令 ──────────────────────────────────────────────────

/// 存一条凭据。
///
/// `sites` 三态, 别混:

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

    /// 三个字段全自己给的构造器。
    ///
    /// 跟下面那个 `cred(label, created)` 并存, 因为职责不同:
    ///   cred     —— reference 按 label 自动拼**正确**的, 测 upsert / 序列化用
    ///   raw_cred —— reference 由调用方给, 才能塞一个**错的** (套两层的) 进去,
    ///               验 migrate_index 会不会重算它
    /// 用 cred 就测不出"重算"这件事 —— 它给的本来就是对的。
    fn raw_cred(label: &str, reference: &str, created: &str) -> TeachingCredential {
        TeachingCredential {
            label: label.into(),
            reference: reference.into(),
            created_at: created.into(),
            sites: Vec::new(),
        }
    }

    // ── 8/18: 站点判据必须跟 tool-bridge 一模一样 ───────────────────

    /// ★★★ 这条是整个"按站点找密码"能不能成立的地基。
    ///
    /// 存密码在 Rust, 查密码在 Python。两边各写一份 hostname 归一化, 迟早会漂 ——
    /// 而漂开的表现是**员工存了、教学说没存过**, 两边各自看都完全正常, 日志里
    /// 什么都没有。跟 8/17 那个 secret_ref 对不上是同一个形状。
    ///
    /// 所以判据只留一份 JSON, 两边测试都读它。谁想放宽, 改那个文件, 两边一起红。
    /// 跟 wiki_visibility_cases / wiki_resolve_cases 同一套安排, 见 edge/contracts/。
    ///
    /// ⚠ 这里用 `include_str!` 而不是 wiki_read.rs 那种 `CARGO_MANIFEST_DIR` +
    ///   运行时读。不是随手写的: 本模块**被两个 crate 编译** —— src-tauri 自己,
    ///   以及 companion-app/pure-tests (它用 #[path] 包这个真文件, 好在没有 GTK
    ///   的机器上也能跑)。`CARGO_MANIFEST_DIR` 在两边不同, 那条路必断一边;
    ///   `include_str!` 认的是**源文件**位置, 两边都对。
    #[test]
    fn hostname_rules_match_the_python_side() {
        let raw = include_str!("../../../../../contracts/credential_site_cases.json");
        let v: serde_json::Value = serde_json::from_str(raw).expect("契约表不是合法 JSON");
        let cases = v["cases"].as_array().expect("契约表缺 cases 数组");
        assert!(cases.len() >= 20, "契约表被删剩 {} 条了", cases.len());

        let mut bad = Vec::new();
        for c in cases {
            let name = c["name"].as_str().unwrap_or("");
            let input = c["input"].as_str().expect("case 缺 input");
            let want = c["expect"].as_str().expect("case 缺 expect");
            // Python 侧用空串表示"不是站点", Rust 侧用 Err —— 这里对齐。
            let got = normalize_site(input).unwrap_or_default();
            if got != want {
                bad.push(format!("  [{name}] {input:?} 期望 {want:?} 实得 {got:?}"));
            }
        }
        assert!(
            bad.is_empty(),
            "跟 tool-bridge 的 host_of() 判据不一致 —— \
             存进去的 key 和查的 key 会对不上:\n{}",
            bad.join("\n")
        );
    }

    #[test]
    fn normalize_sites_dedupes_and_keeps_order() {
        let got = normalize_sites(&[
            "http://eis.ffcs.cn/login".into(),
            "NEIS.ffcs.cn".into(),
            "eis.ffcs.cn".into(), // 跟第一条同一个 host
        ])
        .unwrap();
        assert_eq!(got, vec!["eis.ffcs.cn", "neis.ffcs.cn"]);
    }

    #[test]
    fn normalize_sites_rejects_the_whole_list_on_one_bad_entry() {
        // 悄悄丢掉填错的那个, 员工会以为它生效了 —— 直到某天登录失败才发现。
        let e = normalize_sites(&["eis.ffcs.cn".into(), "教学登录".into()]).unwrap_err();
        assert!(e.contains("教学登录"), "报错要指出是哪一个: {e}");
    }

    #[test]
    fn migrate_merges_the_doubled_row_into_the_clean_one() {
        // ★★★ 8/17 现场: 加了 normalize_label 之后列表变成两行, 指的是同一件事。
        //     鸿波原话"混乱了"。
        let out = migrate_index(vec![
            raw_cred(
                "keychain://catfish-teaching:http://eis.ffcs.cn",
                "keychain://catfish-teaching:keychain://catfish-teaching:http://eis.ffcs.cn",
                "2026-08-01T00:00:00Z",
            ),
            raw_cred(
                "http://eis.ffcs.cn",
                "keychain://catfish-teaching:http://eis.ffcs.cn",
                "2026-08-17T00:00:00Z",
            ),
        ]);
        assert_eq!(out.len(), 1, "同一件事该合成一行, 得到 {out:?}");
        assert_eq!(out[0].label, "http://eis.ffcs.cn");
        assert_eq!(
            out[0].created_at, "2026-08-01T00:00:00Z",
            "created_at 取最早的 —— 跟 upsert 一个语义"
        );
    }

    #[test]
    fn migrate_recomputes_reference_instead_of_trusting_the_stored_one() {
        // ★★ 存着的那个 reference 正是套两层的串, 沿用就等于没修
        let out = migrate_index(vec![raw_cred(
            "keychain://catfish-teaching:EIS",
            "keychain://catfish-teaching:keychain://catfish-teaching:EIS",
            "2026-08-01T00:00:00Z",
        )]);
        assert_eq!(out[0].reference.matches(SERVICE_PREFIX).count(), 1);
        assert_eq!(out[0].reference, "keychain://catfish-teaching:EIS");
    }

    #[test]
    fn migrate_keeps_rows_it_cannot_normalize() {
        // 归一化不了的原样留着 —— 丢了就再也删不掉了
        let out = migrate_index(vec![raw_cred("keychain://eis_password", "x", "2026-01-01T00:00:00Z")]);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].label, "keychain://eis_password");
    }

    #[test]
    fn migrate_is_idempotent() {
        let once = migrate_index(vec![raw_cred(
            "keychain://catfish-teaching:EIS",
            "keychain://catfish-teaching:keychain://catfish-teaching:EIS",
            "2026-08-01T00:00:00Z",
        )]);
        let twice = migrate_index(once.clone());
        assert_eq!(format!("{once:?}"), format!("{twice:?}"));
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
            sites: Vec::new(),
        }
    }

    fn cred_with_sites(label: &str, created: &str, sites: &[&str]) -> TeachingCredential {
        TeachingCredential {
            sites: sites.iter().map(|s| s.to_string()).collect(),
            ..cred(label, created)
        }
    }

    // ── 8/18: sites 在合并路径上不能被吃掉 ─────────────────────────

    #[test]
    fn upsert_keeps_sites_when_the_caller_does_not_mention_them() {
        // ★★★ 改密码就是这条路: 前端只传 label + password, sites 是 None。
        //
        //   `TeachingCredential { created_at, ..item }` 会把 sites 换成 item 的
        //   (空的) —— **编译得过, 不看就发现不了**。表现是员工改了个密码,
        //   多入口那几个站点全失联, 而他什么都没动过。
        let items = vec![cred_with_sites("EIS", "2026-08-01T00:00:00Z", &["eis.ffcs.cn", "neis.ffcs.cn"])];
        let out = upsert(items, cred("EIS", "2026-08-18T00:00:00Z"));
        assert_eq!(out.len(), 1);
        assert_eq!(
            out[0].sites,
            vec!["eis.ffcs.cn", "neis.ffcs.cn"],
            "改密码不该动站点配置"
        );
    }

    #[test]
    fn upsert_replaces_sites_when_the_caller_gives_them() {
        // 员工在 UI 里改站点列表 → 以他给的为准
        let items = vec![cred_with_sites("EIS", "2026-08-01T00:00:00Z", &["old.ffcs.cn"])];
        let out = upsert(items, cred_with_sites("EIS", "2026-08-18T00:00:00Z", &["new.ffcs.cn"]));
        assert_eq!(out[0].sites, vec!["new.ffcs.cn"]);
    }

    #[test]
    fn migrate_unions_sites_of_rows_it_merges() {
        // ★★ 两行归一化后同名会合成一行。被合掉那行的站点要并进来 ——
        //    直接丢掉的话, 员工配的入口莫名其妙少一个。
        let out = migrate_index(vec![
            cred_with_sites("keychain://catfish-teaching:EIS", "2026-08-01T00:00:00Z", &["eis.ffcs.cn"]),
            cred_with_sites("EIS", "2026-08-17T00:00:00Z", &["neis.ffcs.cn"]),
        ]);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].sites, vec!["eis.ffcs.cn", "neis.ffcs.cn"]);
    }

    #[test]
    fn migrate_drops_only_the_bad_site_not_the_whole_row() {
        // 整行丢掉的话这条凭据就再也删不掉了 (列表里看不见)。
        // 留着假站点更糟 —— 它会静默地什么都匹配不到。
        let out = migrate_index(vec![cred_with_sites(
            "EIS",
            "2026-08-01T00:00:00Z",
            &["eis.ffcs.cn", "这不是站点"],
        )]);
        assert_eq!(out.len(), 1, "行要留着");
        assert_eq!(out[0].sites, vec!["eis.ffcs.cn"]);
    }

    #[test]
    fn migrate_normalizes_sites() {
        let out = migrate_index(vec![cred_with_sites(
            "EIS",
            "2026-08-01T00:00:00Z",
            &["http://EIS.ffcs.cn/cas/login", "eis.ffcs.cn"],
        )]);
        assert_eq!(out[0].sites, vec!["eis.ffcs.cn"], "归一化后重复的要去掉");
    }

    #[test]
    fn migrate_normalizes_sites_even_when_the_label_cannot_be_normalized() {
        // ★★ 8/18 做变异时发现的缺口: 把 `Err(_) => TeachingCredential { sites, ..it }`
        //    改回 `Err(_) => it`, 27 条测试**全绿** —— 没有一条走"label 归一化不了
        //    但配了站点"这个组合。
        //
        //    漏掉的后果不是没影响: site_owner / add_site 是按 `s == site` 精确比的,
        //    索引里留着 `http://EIS.ffcs.cn/x` 这种原样串, 冲突检查就照不到它,
        //    于是同一个站点被两条凭据同时认领, 而 UI 上看不出任何异常。
        let out = migrate_index(vec![cred_with_sites(
            "keychain://eis_password", // 外部命名空间, normalize_label 会拒
            "2026-01-01T00:00:00Z",
            &["http://EIS.ffcs.cn/cas"],
        )]);
        assert_eq!(out.len(), 1, "行要留着 —— 丢了就再也删不掉");
        assert_eq!(out[0].label, "keychain://eis_password", "label 原样保留");
        assert_eq!(
            out[0].sites,
            vec!["eis.ffcs.cn"],
            "站点仍然要归一化 —— 否则冲突检查按字符串比会漏掉它"
        );
    }

    #[test]
    fn migrate_sites_is_idempotent() {
        let once = migrate_index(vec![cred_with_sites(
            "EIS", "2026-08-01T00:00:00Z", &["http://EIS.ffcs.cn/x", "坏的"],
        )]);
        let twice = migrate_index(once.clone());
        assert_eq!(format!("{once:?}"), format!("{twice:?}"));
    }

    #[test]
    fn site_owner_finds_the_conflict_and_ignores_self() {
        let items = vec![
            cred_with_sites("EIS", "2026-08-01T00:00:00Z", &["eis.ffcs.cn"]),
            cred_with_sites("OA", "2026-08-02T00:00:00Z", &["oa.ffcs.cn"]),
        ];
        assert_eq!(site_owner(&items, "oa.ffcs.cn", "EIS").as_deref(), Some("OA"));
        // 自己的站点不算冲突 —— 否则改自己的密码都会被拦下来
        assert_eq!(site_owner(&items, "eis.ffcs.cn", "EIS"), None);
        assert_eq!(site_owner(&items, "别的.ffcs.cn", "EIS"), None);
    }

    #[test]
    fn old_rows_without_sites_still_parse() {
        // ★ 员工机器上现在那条就是三个字段。加了新字段之后要还读得进来,
        //   否则升级一装, 索引整个当空 —— 存过的密码在钥匙串里但列表空了。
        let old = r#"[{"label":"http://eis.ffcs.cn",
                       "reference":"keychain://catfish-teaching:http://eis.ffcs.cn",
                       "createdAt":"2026-08-17T09:37:44.889575+00:00"}]"#;
        let parsed: Vec<TeachingCredential> = serde_json::from_str(old).unwrap();
        assert_eq!(parsed.len(), 1);
        assert!(parsed[0].sites.is_empty());
    }

    #[test]
    fn rows_without_sites_serialize_to_the_same_three_fields() {
        // 没配站点的行不该平白多出一个 "sites": []
        let json = serde_json::to_string(&cred("EIS", "2026-08-01T00:00:00Z")).unwrap();
        assert!(!json.contains("sites"), "空站点不该写进文件: {json}");
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
        // 配了站点的行 —— 字段最多就这四个
        let json =
            serde_json::to_string(&cred_with_sites("EIS", "2026-08-01T00:00:00Z", &["eis.ffcs.cn"]))
                .unwrap();
        let v: serde_json::Value = serde_json::from_str(&json).unwrap();
        // 排序后再比 —— serde_json 默认用 BTreeMap, 键序是字典序不是声明序,
        // 按声明序断言会挂在一个跟本意无关的地方。
        let mut keys: Vec<&str> = v.as_object().unwrap().keys().map(|s| s.as_str()).collect();
        keys.sort_unstable();
        assert_eq!(
            keys,
            vec!["createdAt", "label", "reference", "sites"],
            "索引结构变了 —— 密码只能待在系统凭据库里, 绝不能进这个文件"
        );
    }
}
