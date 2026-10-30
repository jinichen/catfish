//! 员工自己的邮箱域 —— 判「本单位发出的邮件」用 (10/31)。
//!
//! 钓鱼复审原来给模型的只有主题 / 发件人 / 规则结果, 模型分不出「本单位群发的通知」
//! 和「外面的营销群发」: 信息安全中心 <ff_nic@chinatelecom.cn> 群发给 96 人的
//! 「网信安专项 / 安全预警」被判成营销 (鸿波 10/31 截图)。本模块给出员工自己账号的域,
//! 复审时标上「本单位 / 外部」, 本单位的一律不进营销档。
//!
//! 来源: `catfish-email accounts --json` 里每个账号的 address (员工在邮件客户端里配的),
//! 再加登录员工自己的邮箱域。账号列表拿到过一次就缓存到进程结束; 拿不到不缓存, 下次再试。

use std::collections::BTreeSet;
use std::sync::OnceLock;

use crate::services::catfish_paths;

/// `a@b.cn` / `名字 <a@b.cn>` → `b.cn`。
pub(crate) fn domain_of(addr: &str) -> Option<String> {
    let at = addr.rfind('@')?;
    let d = addr[at + 1..].trim().trim_end_matches('>').trim().to_lowercase();
    (d.contains('.') && !d.contains(' ')).then_some(d)
}

/// 发件人域是不是员工自己的域 (或其子域)。`evilchinatelecom.cn` 不算 `chinatelecom.cn`。
pub(crate) fn is_own_sender(sender: &str, own: &[String]) -> bool {
    let Some(d) = domain_of(sender) else { return false };
    own.iter().any(|o| d == *o || d.ends_with(&format!(".{o}")))
}

pub(crate) fn own_mail_domains() -> Vec<String> {
    static CACHE: OnceLock<Vec<String>> = OnceLock::new();
    if let Some(v) = CACHE.get() {
        return v.clone();
    }
    let mut set = BTreeSet::new();
    let mut from_accounts = false;
    if let Some(bin) = catfish_paths::catfish_email_bin() {
        if let Ok(out) = crate::commands::email::email_command(&bin).args(["accounts", "--json"]).output() {
            if let Ok(list) = serde_json::from_slice::<Vec<serde_json::Value>>(&out.stdout) {
                for acc in list {
                    if let Some(d) = acc.get("address").and_then(|a| a.as_str()).and_then(domain_of) {
                        set.insert(d);
                        from_accounts = true;
                    }
                }
            }
        }
    }
    if let Some(d) = crate::services::oauth::current_user_sub().as_deref().and_then(domain_of) {
        set.insert(d);
    }
    let v: Vec<String> = set.into_iter().collect();
    if from_accounts {
        let _ = CACHE.set(v.clone());
    }
    v
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 取域名() {
        assert_eq!(domain_of("信息安全中心 <ff_nic@chinatelecom.cn>").as_deref(), Some("chinatelecom.cn"));
        assert_eq!(domain_of("a@B.CN").as_deref(), Some("b.cn"));
        assert_eq!(domain_of("没有地址"), None);
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
}
