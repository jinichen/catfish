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

// ⚠ keyring v3 的平台后端靠 Cargo.toml 的 [target.*] feature 开。没开时它
//   `pub use mock as default` —— set_password 返 Ok(()) 而密码只进一个进程内
//   的假存储, 退出就没。8/18 实撞: 界面一直显示"已保存", 教学时
//   `security find-generic-password` 永远找不到, 查了三轮才落到这儿。
//
//   这条 compile_error! 让"新平台忘了开 feature"变成**编译失败**, 而不是又一次
//   静默的假保存。加平台时: Cargo.toml 加一段 [target.'cfg(target_os = "...")']
//   带对应 feature, 再把这里放行。
//   ⚠ Linux 是**故意放行**的, 见下面 entry_for 那条 cfg。8/18 第一版把 Linux 也
//     写进 compile_error, 结果是 CI 的 `cargo test --lib --no-run` (ubuntu-latest,
//     标着"必过") 直接编不过 —— 那一步跟平台无关、不给逃生口, 正好被这条挡死。
//     产品不发 Linux, 但 CI 在 Linux 上编。
#[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
compile_error!(
    "keyring 只在 macOS / Windows 配了平台后端 (见 Cargo.toml)。\n     当前目标没配 —— 直接编过去的话 keyring 会用 mock store, \n     密码存了等于没存, 而且界面还显示成功。先去 Cargo.toml 加 [target.*] feature。"
);

mod index;
/// 取值通道的判据 (纯逻辑, 没有 unix / keyring 依赖 → CI 沙箱里跑得了)。
mod socket_proto;
/// 取值通道的接线。macOS only —— 它解的是钥匙串按二进制授权这个 macOS 特有的
/// 问题, Windows 凭据管理器同用户下本来就都读得到, 没有要绕的东西。
#[cfg(target_os = "macos")]
pub mod socket;

pub use index::TeachingCredential;
use index::{
    normalize_label, normalize_site, normalize_sites, read_index, reference_for, service_name,
    site_owner, upsert, write_index,
};
// 只有真去开凭据库的那个 entry_for 用得到 —— Linux 那版是个直接返错的桩,
// 不 cfg 的话在 ubuntu CI 上会多一条 unused import 警告。
#[cfg(any(target_os = "macos", target_os = "windows"))]
use index::ACCOUNT;
use keyring::Entry;
/// 打开凭据库条目。service / user 两个位置的用法必须跟 secret_resolver 一致,
/// 写进去读不出来就白存了 (两边都对过: macOS 查 service 不带 -a, Windows 查
/// service="catfish" + user=target)。
///
/// (8/18 拆文件时这段注释掉了前两行, 只剩最后半句挂在这儿 —— 8/19 从 2dd0e80 补回。)
#[cfg(any(target_os = "macos", target_os = "windows"))]
fn entry_for(target: &str) -> Result<Entry, String> {
    #[cfg(target_os = "windows")]
    let entry = Entry::new("catfish", target);
    #[cfg(not(target_os = "windows"))]
    let entry = Entry::new(target, ACCOUNT);
    entry.map_err(|e| format!("无法打开本机凭据库: {e}"))
}

/// Linux: **编得过, 但一律拒绝**。
///
/// 产品不发 Linux (resources/ 里只有 mac-aarch64 / mac-x64 / windows 三个包),
/// 而 CI 在 ubuntu 上编。这时 keyring 没开任何平台 feature —— 它会
/// `pub use mock as default`, `set_password` 返 Ok(()) 而密码只进一个进程内的假
/// 存储。8/18 就是这条静默路径让"界面显示已保存, 教学时永远找不到"查了三轮。
///
/// 所以这里不给它机会: Linux 上根本不去碰 keyring, 每次调用都是一句说得清的错。
/// 真要支持 Linux, 去 Cargo.toml 加 `sync-secret-service` feature 并把这个 cfg
/// 收窄 —— 别让它悄悄掉回 mock。
#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn entry_for(_target: &str) -> Result<Entry, String> {
    Err("本平台没有配置系统凭据库后端 —— 教学密码只在 macOS / Windows 可用".to_string())
}

/// 按 service 名读密码。`Ok(None)` = 凭据库里没这一条。
///
/// # 这是**唯一**的读出口, 而且不是 Tauri command
///
/// 前端永远不该拿到密码: 它只负责把密码送进来 (`teaching_credential_save`),
/// 不负责取出去。取值只有一个消费者 —— socket.rs 那条给 tool-bridge 的通道。
/// 一旦这里挂上 `#[tauri::command]`, 任何一段前端 JS 就都能把密码要出来放进
/// 聊天里, 之前所有"密码不进 LLM 上下文"的功夫全废。
///
/// ⚠ 错误消息里不许有密码。keyring 的 Error 是状态码 / 系统消息, 本身不含值 ——
///   下面这个 `{e}` 是安全的, 但别往里加别的东西。
pub(crate) fn read_password(target: &str) -> Result<Option<String>, String> {
    match entry_for(target)?.get_password() {
        Ok(p) => Ok(Some(p)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(e.to_string()),
    }
}

/// 存一条教学凭据: 密码进系统凭据库, 标签 / 引用 / 站点进 `~/.catfish` 的索引。
///
/// `sites` 的三种取值意思不一样, 别合并:
///
///   `None`      —— 这次没提站点 (改密码走这条), 原有站点原样留着
///   `Some([..])`—— 用这一串**替换**
///   `Some([])`  —— 明确清空 (员工在 UI 里把站点全删了)
#[tauri::command]
pub fn teaching_credential_save(
    label: String,
    password: String,
    sites: Option<Vec<String>>,
) -> Result<String, String> {
    // 归一化一次, 后面 service / 索引都用它 —— 8/17 之前索引存的是**原样**,
    // 于是"名字本身是引用串"那条会一直留在列表里, 点一下又填回输入框, 再存
    // 就多套一层。存归一化后的名字, 这个循环就断了。
    let (name, target) = service_name(&label)?;
    if password.is_empty() {
        return Err("请填写密码".to_string());
    }

    // ⚠ 站点校验必须在**写凭据库之前**。写完再报错的话密码已经进去了,
    //   员工看到的是"保存失败", 实际却存了一半。
    let existing = read_index();
    let sites = match &sites {
        Some(raw) => {
            let hosts = normalize_sites(raw)?;
            for h in &hosts {
                if let Some(owner) = site_owner(&existing, h, &name) {
                    return Err(format!(
                        "{h} 已经归「{owner}」管了。\n\
                         一个网站只该有一条凭据 —— 要换成这条, 先把「{owner}」\
                         里的这个网站去掉 (或者直接改「{owner}」的密码)。"
                    ));
                }
            }
            hosts
        }
        // 没提就是没提。`Vec::new()` 到了 upsert 会被认成"沿用原有站点"。
        None => Vec::new(),
    };

    // macOS: 先删再建, **不能直接 set**。
    //
    // keyring 的 set_password 底下是 security-framework 的 set_generic_password,
    // 而它是"条目已存在就 item.set_password 原地改"(passwords.rs:275)。原地改
    // **保留旧的 ACL**。
    //
    // 这一点有实际后果: 8/18 那版 (c010210) 往已有条目的 ACL 里塞过
    // `/usr/bin/security` —— 等于对所有程序开放。如果这里原地改, 那条超宽的 ACL
    // 会一直留在鸿波机器上那条 `catfish-teaching:neis.ffcs.cn` 上, 这次改动对它
    // 完全无效, 而列表里看不出任何异样。
    //
    // 删掉重建, ACL 由系统按"创建者"重新生成 → 只有 Companion。改一次密码就自动
    // 收敛, 不用写迁移。
    //
    // 删和建都不触发授权框: 删要求本程序在该条目 ACL 里 (Companion 一直在),
    // 建是全新条目, 定初始 ACL 是免费的。
    #[cfg(target_os = "macos")]
    match entry_for(&target)?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => {}
        // 删不掉的话下面那句会退化成"原地改", 密码是对的, 但 ACL 还是老的那份。
        // 不当失败 —— 员工没办法处理这个, 而且密码确实存进去了。留一行能 grep 的话。
        Err(e) => log::warn!(
            "[teaching-credential] 旧条目删不掉 ({target}): {e} —— \
             这次是原地覆盖, ACL 沿用旧的 (可能还留着 8/18 加的 /usr/bin/security)"
        ),
    }

    entry_for(&target)?
        .set_password(&password)
        .map_err(|e| format!("保存到本机凭据库失败: {e}"))?;

    let reference = reference_for(&target);
    // 索引写失败不能让"密码已经存进去了"这件事变成报错 —— 密码是主线,
    // 索引只是为了后面能列出来。写不进去就记一条日志, 员工仍然拿得到引用串。
    if let Err(e) = write_index(&upsert(
        existing,
        TeachingCredential {
            label: name.clone(),
            reference: reference.clone(),
            created_at: chrono::Utc::now().to_rfc3339(),
            sites,
        },
    )) {
        log::warn!("凭据已存入系统凭据库, 但标签索引没写成: {e}");
    }
    Ok(reference)
}

/// 给一条已有的凭据**再挂一个网站** —— 多入口共用一个密码走这条。
///
/// 典型场景: `eis.ffcs.cn` 存过了, 登录时跳到 `neis.ffcs.cn`, 教学那边报
/// needs_credential。员工不用再输一遍密码, 把新入口挂到老那条上就行。
///
/// **不碰密码**, 所以不需要 password 参数, 也不会有任何密码经过这里。
#[tauri::command]
pub fn teaching_credential_add_site(label: String, site: String) -> Result<Vec<String>, String> {
    let name = normalize_label(&label)?;
    let host = normalize_site(&site)?;
    let mut items = read_index();

    if let Some(owner) = site_owner(&items, &host, &name) {
        return Err(format!("{host} 已经归「{owner}」管了 —— 一个网站只该有一条凭据。"));
    }
    let idx = items
        .iter()
        .position(|i| i.label == name)
        .ok_or_else(|| format!("没有名为「{name}」的凭据 —— 先存一条再挂网站"))?;

    if !items[idx].sites.contains(&host) {
        items[idx].sites.push(host);
    }
    let out = items[idx].sites.clone();
    write_index(&items)?;
    Ok(out)
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
    // read_index 已经过 migrate_index, 里面的 label 都是归一化后的,
    // 所以按 name 比就够 —— 不用再加"也认原样"那条特判。
    //
    // ⚠ 那条特判 8/17 加过又拿掉: 它会让"点老那行删除"同时删掉新旧两行
    //   (两者归一化后同名), 等于点"删垃圾"把好的一起删了。
    //   根子上是列表里不该同时出现两行, migrate_index 解决的就是这个。
    let kept: Vec<TeachingCredential> = read_index()
        .into_iter()
        .filter(|i| i.label != name)
        .collect();
    write_index(&kept)
}