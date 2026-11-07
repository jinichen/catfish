//! 本单位的邮箱域 —— 钓鱼识别判「本单位发出的邮件」用 (10/31; 11/07 改成三层)。
//!
//! 钓鱼复审原来给模型的只有主题 / 发件人 / 规则结果, 模型分不出「本单位群发的通知」
//! 和「外面的营销群发」: 信息安全中心 <ff_nic@chinatelecom.cn> 群发给 96 人的
//! 「网信安专项 / 安全预警」被判成营销 (鸿波 10/31 截图)。本模块给出本单位的域,
//! 复审时标上「本单位 / 外部」, 规则 PHISH-001/002/003 (显示名冒充 / 形近域 / 子域冒充) 也靠它。
//!
//! 11/07 鸿波: 同一封安全预警又被标成「钓鱼嫌疑」—— 那一轮本单位域是空的。两个原因:
//!
//!   1. 只靠每台电脑去问邮件客户端要账号: Mail 没开时走 EMLX 兜底, 账号目录是编号, 拿不到地址;
//!      macOS 不让读 Foxmail 目录时整条 `accounts` 命令崩掉 (这两处在 catfish-email 里也修了)。
//!   2. 换个单位装、一个单位好几个域名 (集团 / 下属单位 / 合作方)、员工还配了 Gmail —— 都只能靠员工自己的账号猜。
//!
//! 现在本单位域 = 三层合起来:
//!   - 中央「公司设置 → 本单位邮箱域名」(管理员填一次, 全员下发; GET /api/org/policy org_mail_domains) —— 主依据
//!   - 员工自己的工作邮箱 (邮件客户端里配的账号 + 登录鲶鱼的邮箱), **公共邮箱不算** (gmail / qq / 163 …,
//!     谁都能注册, 算进来等于冒充「信息安全中心」的 Gmail 也放过)
//!   - 上次成功取到的 (存 ~/.catfish/own_mail_domains.json): 这次哪一层取不到就用上次的, 不会变空
//!
//! 一小时重算一次。

use std::collections::BTreeSet;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use crate::services::catfish_paths;

const REFRESH_EVERY: Duration = Duration::from_secs(3600);
const POLICY_PATH: &str = "/api/org/policy";

/// 公共邮箱: 谁都能注册, 不能当「本单位」。跟中央 org_policy_router.py PUBLIC_MAIL 同一份。
pub(crate) const PUBLIC_MAIL: &[&str] = &[
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com", "yahoo.com.cn",
    "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "mail.com", "yandex.com",
    "qq.com", "foxmail.com", "163.com", "126.com", "yeah.net", "sina.com", "sina.cn", "sohu.com", "aliyun.com",
    "139.com", "189.cn", "wo.cn", "tom.com", "21cn.com", "263.net",
];

pub(crate) fn is_public_mail(domain: &str) -> bool {
    PUBLIC_MAIL.contains(&domain)
}

/// `a@b.cn` / `名字 <a@b.cn>` → `b.cn`。
pub(crate) fn domain_of(addr: &str) -> Option<String> {
    let at = addr.rfind('@')?;
    let d = addr[at + 1..].trim().trim_end_matches('>').trim().to_lowercase();
    (d.contains('.') && !d.contains(' ')).then_some(d)
}

/// 发件人域是不是本单位的域 (或其子域)。`evilchinatelecom.cn` 不算 `chinatelecom.cn`。
pub(crate) fn is_own_sender(sender: &str, own: &[String]) -> bool {
    let Some(d) = domain_of(sender) else { return false };
    own.iter().any(|o| d == *o || d.ends_with(&format!(".{o}")))
}

#[derive(Debug, Default, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub(crate) struct Known {
    /// 中央下发的本单位邮箱域名 (管理员填的)
    #[serde(default)]
    pub org: Vec<String>,
    /// 员工自己的工作邮箱域 (不含公共邮箱)
    #[serde(default)]
    pub personal: Vec<String>,
}

/// 三层合起来: 这次取到的优先, 取不到 (None / 空) 用上次的。org 取到空列表 = 管理员没填 / 清空了, 就是空。
pub(crate) fn merge(org_now: Option<Vec<String>>, personal_now: Vec<String>, last: &Known) -> (Known, Vec<String>) {
    let known = Known {
        org: org_now.unwrap_or_else(|| last.org.clone()),
        personal: if personal_now.is_empty() { last.personal.clone() } else { personal_now },
    };
    let all: BTreeSet<String> = known.org.iter().chain(known.personal.iter()).cloned().collect();
    (known, all.into_iter().collect())
}

/// 一串地址 → 工作邮箱域 (去重, 公共邮箱不算)。
pub(crate) fn work_domains<'a>(addrs: impl IntoIterator<Item = &'a str>) -> Vec<String> {
    let set: BTreeSet<String> = addrs.into_iter().filter_map(domain_of).filter(|d| !is_public_mail(d)).collect();
    set.into_iter().collect()
}

fn known_path() -> Option<PathBuf> {
    Some(catfish_paths::catfish_home()?.join("own_mail_domains.json"))
}

fn load_known() -> Known {
    known_path()
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save_known(k: &Known) {
    let Some(p) = known_path() else { return };
    if let Ok(s) = serde_json::to_string(k) {
        let tmp = p.with_extension("json.tmp");
        if std::fs::write(&tmp, s).is_ok() {
            let _ = std::fs::rename(&tmp, &p);
        }
    }
}

/// 员工自己配的邮箱账号地址 (catfish-email accounts --json)。拿不到 = 空。
fn account_addresses() -> Vec<String> {
    let Some(bin) = catfish_paths::catfish_email_bin() else { return Vec::new() };
    let Ok(out) = crate::commands::email::email_command(&bin).args(["accounts", "--json"]).output() else {
        return Vec::new();
    };
    serde_json::from_slice::<Vec<serde_json::Value>>(&out.stdout)
        .unwrap_or_default()
        .iter()
        .filter_map(|a| a.get("address").and_then(|x| x.as_str()).map(str::to_string))
        .collect()
}

/// 中央「公司设置 → 本单位邮箱域名」。没登录 / 连不上 = None (用上次的); 没填 / 老中央 = Some([])。
async fn org_domains() -> Option<Vec<String>> {
    let token = crate::services::oauth::ensure_fresh_access_token().await?;
    let url = format!("{}{}", crate::services::endpoints::endpoints().gateway_base().trim_end_matches('/'), POLICY_PATH);
    let client = crate::util::http_client::trust_central(reqwest::Client::builder().timeout(Duration::from_secs(10)))
        .build()
        .ok()?;
    let resp = client.get(&url).bearer_auth(token).send().await.ok()?;
    if resp.status().as_u16() == 404 {
        return Some(Vec::new()); // 老中央还没有公司设置
    }
    if !resp.status().is_success() {
        return None;
    }
    let v: serde_json::Value = resp.json().await.ok()?;
    Some(policy_domains(&v))
}

/// GET /api/org/policy 的回包 → 本单位邮箱域名 (中央还不认这一项的老版本 = 空)。
pub(crate) fn policy_domains(v: &serde_json::Value) -> Vec<String> {
    let Some(serde_json::Value::Array(a)) = v.get("org_mail_domains") else { return Vec::new() };
    a.iter()
        .filter_map(|x| x.as_str())
        .map(|d| d.trim().trim_start_matches('@').to_lowercase())
        .filter(|d| d.contains('.') && !is_public_mail(d))
        .collect()
}

static CACHE: Mutex<Option<(Instant, Vec<String>)>> = Mutex::new(None);

/// 本单位的邮箱域 (一小时重算一次)。
pub(crate) async fn own_mail_domains() -> Vec<String> {
    if let Ok(c) = CACHE.lock() {
        if let Some((at, v)) = c.as_ref() {
            if at.elapsed() < REFRESH_EVERY {
                return v.clone();
            }
        }
    }
    let org = org_domains().await;
    let mut addrs = tokio::task::spawn_blocking(account_addresses).await.unwrap_or_default();
    if let Some(sub) = crate::services::oauth::current_user_sub() {
        addrs.push(sub);
    }
    let personal = work_domains(addrs.iter().map(String::as_str));
    let last = load_known();
    let (known, all) = merge(org, personal, &last);
    if known != last {
        save_known(&known);
    }
    if !all.is_empty() {
        if let Ok(mut c) = CACHE.lock() {
            *c = Some((Instant::now(), all.clone()));
        }
    }
    log::info!("[phishing] 本单位邮箱域: {:?} (中央下发 {:?})", all, known.org);
    all
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 取域名() {
        assert_eq!(domain_of("信息安全中心 <ff_nic@chinatelecom.cn>").as_deref(), Some("chinatelecom.cn"));
        assert_eq!(domain_of("a@B.CN").as_deref(), Some("b.cn"));
        assert_eq!(domain_of("没有地址"), None);
        assert_eq!(domain_of("36984457-CE3B-4239-92E7-3374CE655974"), None);
    }

    #[test]
    fn 本单位判定_含子域_不含形近域() {
        let own = vec!["chinatelecom.cn".to_string()];
        assert!(is_own_sender("信息安全中心 <ff_nic@chinatelecom.cn>", &own));
        assert!(is_own_sender("x@fj.chinatelecom.cn", &own));
        assert!(!is_own_sender("x@evilchinatelecom.cn", &own));
        assert!(!is_own_sender("x@chinatelecom.cn.attacker.com", &own));
        assert!(!is_own_sender("x@gmail.com", &own));
    }

    #[test]
    fn 公共邮箱不算本单位() {
        let d = work_domains(["ffchenhb@chinatelecom.cn", "josonchen1234@gmail.com", "jini.chen@icloud.com", "x@QQ.com"]);
        assert_eq!(d, vec!["chinatelecom.cn".to_string()]);
    }

    #[test]
    fn 中央回包_只认域名_公共邮箱剔掉_老中央没有这项() {
        let v = serde_json::json!({"room_retention_days": 90, "org_mail_domains": ["ChinaTelecom.cn", "@ffcs.cn", "qq.com", "x"]});
        assert_eq!(policy_domains(&v), vec!["chinatelecom.cn".to_string(), "ffcs.cn".to_string()]);
        assert!(policy_domains(&serde_json::json!({"room_retention_days": 90})).is_empty());
    }

    #[test]
    fn 三层合起来_取不到的用上次的() {
        let last = Known { org: vec!["ffcs.cn".into()], personal: vec!["chinatelecom.cn".into()] };
        // 这次都取不到: 全用上次的 (不会变空)
        let (k, all) = merge(None, vec![], &last);
        assert_eq!(k, last);
        assert_eq!(all, vec!["chinatelecom.cn".to_string(), "ffcs.cn".to_string()]);
        // 中央下发了新的: 以中央为准, 再并上员工自己的
        let (k, all) = merge(Some(vec!["ctyun.cn".into()]), vec!["chinatelecom.cn".into()], &last);
        assert_eq!(k.org, vec!["ctyun.cn".to_string()]);
        assert_eq!(all, vec!["chinatelecom.cn".to_string(), "ctyun.cn".to_string()]);
        // 管理员清空了: 中央那层就是空的
        let (k, _) = merge(Some(vec![]), vec!["abc.com".into()], &last);
        assert!(k.org.is_empty());
        assert_eq!(k.personal, vec!["abc.com".to_string()]);
    }
}
