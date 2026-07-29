//! 面向**中央服务**的 reqwest client 构造器 (P3.5.80 · 7/28 鸿波达华现场).
//!
//! ── 为什么需要这个 ────────────────────────────────────────────────
//!
//! 中央端启用 HTTPS 后用的是**自签证书** (setup.sh 现场生成, SAN 含服务器 IP).
//! 浏览器点一次「继续前往」就记住了; reqwest 不吃这一套 —— TLS 握手直接失败.
//!
//! 现场表现: 仪表盘顶部「中央门户」显示"连不上", 但同一个地址浏览器打得开、
//! curl -k 也通. 因为门户探活 (WebPortalLink pingWeb) 和服务器连通性检测
//! (useServerReachable) 都走 fetchViaProxy → Rust reqwest.
//!
//! 更糟的是 http_proxy.rs::build_client 里有一句注释写着
//!     "5/19 BL-COMPANION-AUTH: ... 不 verify 证书严"
//! 但**代码里根本没有对应的那一行** —— builder 只设了 timeout. 注释描述了一个
//! 不存在的行为, 于是没人怀疑过 TLS.
//!
//! ── 为什么不直接 danger_accept_invalid_certs(true) ────────────────
//!
//! 那会**全局**关掉证书校验, 连带 weather.rs 打 wttr.in、邮件、钓鱼扫描这些
//! 走公网的请求一起裸奔, 任何中间人都能冒充. 为了内网一台自签服务器把整个
//! 客户端的 TLS 信任模型废掉, 不划算.
//!
//! 本模块的做法: **只额外信任 IT 给的那一张证书**, 其它一切照常严格校验.
//!   1. 读 ~/.catfish/server-ca.pem (IT 把 setup.sh 生成的 certs/cert.pem
//!      推过去, 改个名即可) → reqwest add_root_certificate()
//!   2. 没有这个文件就退回默认行为 (完全严格), 不静默放宽
//!   3. 逃生开关 CATFISH_ALLOW_SELF_SIGNED=1 —— 只给排障用, 会打 warn 日志
//!
//! 加载的是**额外**根证书, 不是替换: 系统信任库照旧生效, 公网站点不受影响.
//!
//! ── 用在哪 ────────────────────────────────────────────────────────
//!
//! 只给"连公司中央服务"的调用点用 (门户探活 / gateway / identity / 配置同步).
//! 连公网的 (weather / 钓鱼库 / 邮件 provider) **不要**用这个 —— 它们没理由
//! 信任客户内网的自签 CA.

use std::path::PathBuf;
use std::sync::OnceLock;
use std::time::Duration;

/// IT 推给员工机器的中央服务证书. 没有就走默认严格校验.
///
/// 为什么放 ~/.catfish/ 而不是要求装进系统钥匙串: 装钥匙串要管理员权限 /
/// MDM 下发, 放一个文件不用. 两条路都支持 —— 系统钥匙串装了的话, native-tls
/// 本来就认, 这里再加一次也不冲突.
const CERT_FILE: &str = "server-ca.pem";

fn cert_path() -> Option<PathBuf> {
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .ok()?;
    Some(PathBuf::from(home).join(".catfish").join(CERT_FILE))
}

/// 读证书文件 → reqwest::Certificate. 进程内只读一次.
///
/// 读失败 (文件不存在 / 格式不对) 都返 None 走默认严格校验, 但**格式不对要
/// 打 error**: 文件明明在那儿却没生效, 不说的话现场会以为"证书推了还是不通"
/// 而去查网络.
fn extra_root_cert() -> Option<&'static reqwest::Certificate> {
    static CERT: OnceLock<Option<reqwest::Certificate>> = OnceLock::new();
    CERT.get_or_init(|| {
        let path = cert_path()?;
        if !path.exists() {
            log::debug!(
                "未找到 {} · 中央服务若用自签证书会连不上 (走系统信任库 / 或放这个文件)",
                path.display()
            );
            return None;
        }
        let pem = match std::fs::read(&path) {
            Ok(b) => b,
            Err(e) => {
                log::error!("读 {} 失败: {e} · 自签证书不会被信任", path.display());
                return None;
            }
        };
        match reqwest::Certificate::from_pem(&pem) {
            Ok(c) => {
                log::info!("已加载中央服务证书 {} · 该证书签发的 HTTPS 会被信任", path.display());
                Some(c)
            }
            Err(e) => {
                log::error!(
                    "{} 不是合法 PEM 证书: {e} · 请确认推的是 setup.sh 生成的 certs/cert.pem",
                    path.display()
                );
                None
            }
        }
    })
    .as_ref()
}

/// 开关取值的解析 —— 纯函数, 不碰环境变量.
///
/// 为什么拆出来 (P3.5.80 · 7/28): 原来读 env 和判断写在一个函数里, 测试只能
/// 靠 set_var/remove_var 来驱动. 但 cargo test 默认**多线程并发**跑, 而
/// std::env 是进程全局的 —— 一个测试 remove_var 会把另一个测试刚 set 的值抹掉,
/// 于是 allow_self_signed_reads_env 随机失败. 加 mutex 能压住, 但那是拿锁
/// 掩盖设计问题: 判断逻辑本来就不需要知道值从哪来.
fn parse_allow_flag(v: Option<&str>) -> bool {
    matches!(v, Some(s) if s == "1" || s.eq_ignore_ascii_case("true"))
}

/// 逃生开关: 完全跳过证书校验.
///
/// 只在排障 / 客户死活不肯推证书时用. 每次建 client 都打 warn —— 这种降级
/// 必须一直可见, 不能装作没事.
fn allow_self_signed() -> bool {
    parse_allow_flag(
        std::env::var("CATFISH_ALLOW_SELF_SIGNED")
            .ok()
            .as_deref(),
    )
}

/// 要不要走系统/环境里配的 HTTP 代理.
///
/// 默认 **不走** —— 见 `trust_central` 里的说明. 极少数情况下中央服务真的
/// 只能通过代理到达, 那时显式 `CATFISH_USE_SYSTEM_PROXY=1`.
fn use_system_proxy() -> bool {
    parse_allow_flag(std::env::var("CATFISH_USE_SYSTEM_PROXY").ok().as_deref())
}

/// 给 builder 挂上中央服务该有的配置 (信任 + 绕代理).
///
/// 供各调用点在自己的 builder 上链式调用, 这样超时 / UA 等其它配置各自保留.
///
/// ── 为什么要 no_proxy (P3.5.80 · 7/28 鸿波达华现场, 查了一整轮才定位) ──
///
/// reqwest 默认**会读环境变量和 macOS 系统代理设置**. 员工机器上装了
/// Clash / Surge / 公司 VPN 客户端时, 系统代理指向 127.0.0.1:7890 之类;
/// 中央服务在**内网**, 本来直连就到, 却被塞进代理隧道. 代理没起 / 不转发
/// 内网段时, reqwest 报出来是:
///
/// ```text
/// error sending request for url (https://192.168.31.199/)
///   ← client error (Connect)
///   ← tunnel error: failed to create underlying connection
///   ← tcp connect error
///   ← Connection refused (os error 61)
/// ```
///
/// 关键词是 `tunnel error` —— 那是在向代理发 CONNECT, 不是在连服务器.
///
/// 这个坑极难认: 浏览器有"本地网络绕过代理"的规则所以打得开, 面板却连不上,
/// 看起来像"检测有问题". 而且 `gateway.rs` 和 `health.rs` **早就写了**
/// `.no_proxy()` (注释原话: "localhost 永远不走代理, 避免员工设了 HTTPS_PROXY
/// 把 127.0.0.1 也劫了"), 唯独 `http_proxy.rs` 这条漏了 —— 于是别的检测都好,
/// 只有走它的门户探活红着, 不对称本身就是线索.
///
/// 中央服务按定义在客户内网, 走代理没有任何意义, 所以这里一律绕过.
pub fn trust_central(mut b: reqwest::ClientBuilder) -> reqwest::ClientBuilder {
    if let Some(cert) = extra_root_cert() {
        b = b.add_root_certificate(cert.clone());
    }
    if allow_self_signed() {
        log::warn!(
            "CATFISH_ALLOW_SELF_SIGNED=1 · 已关闭 TLS 证书校验 (仅排障用) · \
             生产请改为把中央证书放到 ~/.catfish/{CERT_FILE}"
        );
        b = b.danger_accept_invalid_certs(true);
    }
    if use_system_proxy() {
        log::warn!("CATFISH_USE_SYSTEM_PROXY=1 · 连中央服务会走系统代理 (默认是绕过的)");
    } else {
        b = b.no_proxy();
    }
    b
}

/// 同上, blocking 版 (embedding / role_config 用的是 blocking client).
pub fn trust_central_blocking(
    mut b: reqwest::blocking::ClientBuilder,
) -> reqwest::blocking::ClientBuilder {
    if let Some(cert) = extra_root_cert() {
        b = b.add_root_certificate(cert.clone());
    }
    if allow_self_signed() {
        log::warn!("CATFISH_ALLOW_SELF_SIGNED=1 · 已关闭 TLS 证书校验 (仅排障用)");
        b = b.danger_accept_invalid_certs(true);
    }
    if use_system_proxy() {
        log::warn!("CATFISH_USE_SYSTEM_PROXY=1 · 连中央服务会走系统代理 (默认是绕过的)");
    } else {
        b = b.no_proxy();
    }
    b
}

/// 连中央服务的默认 client. 没有特殊需求的调用点直接用这个.
pub fn central_client(timeout: Duration) -> Result<reqwest::Client, String> {
    trust_central(reqwest::Client::builder().timeout(timeout))
        .build()
        .map_err(|e| format!("建 http client 失败: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 不设环境变量 = 关. 这是安全默认值, 被改成默认开就是灾难.
    #[test]
    fn allow_flag_defaults_off() {
        assert!(!parse_allow_flag(None));
    }

    /// 只有明确的 "1" / "true" 才算开. 其余一律关 —— 包括空串、"yes"、"on"
    /// 这类看着像开的写法. 宁可员工发现"开关没生效"来问, 也不要因为一个手滑的
    /// 值静默关掉全部证书校验.
    #[test]
    fn allow_flag_only_accepts_explicit_true() {
        for on in ["1", "true", "TRUE", "True"] {
            assert!(parse_allow_flag(Some(on)), "{on} 应该算开");
        }
        for off in ["0", "", "yes", "on", "false", "1 ", " 1", "2", "no"] {
            assert!(!parse_allow_flag(Some(off)), "{off:?} 不该算开");
        }
    }

    #[test]
    fn cert_path_points_into_dot_catfish() {
        // CI / 沙箱里可能没有 HOME —— 那种情况下 cert_path() 返 None 是正确行为,
        // 不该让测试炸. 用 expect 会把"环境没 HOME"误报成"代码有 bug".
        let Some(p) = cert_path() else {
            eprintln!("跳过: 环境没有 HOME/USERPROFILE");
            return;
        };
        let s = p.to_string_lossy();
        assert!(
            s.ends_with(&format!(".catfish/{CERT_FILE}"))
                || s.ends_with(&format!(".catfish\\{CERT_FILE}")),
            "证书路径不对: {s}"
        );
    }

    /// 真连一次中央服务, 验证 ~/.catfish/server-ca.pem 到底有没有让 reqwest
    /// 接受那张自签证书 (P3.5.80 · 7/28).
    ///
    /// 为什么要有这条: 之前验证这件事的唯一办法是"重建整个 Companion → 装 →
    /// 打开 → 看仪表盘变不变绿", 一轮十几分钟, 而且失败了还分不清是
    ///   (a) 代码没生效  (b) 证书没放对  (c) 方案本身不成立
    /// 三者中的哪一个. 这条测试把这三者一次分开.
    ///
    /// 默认 `#[ignore]` —— 它依赖一台真实可达的服务器, 不该进常规 CI.
    ///
    /// 跑法 (⚠ 带 --lib —— 不带的话 `--ignored` 会连 doctest 里标 ignore 的
    /// 代码块一起编译执行, 7/28 踩过):
    /// ```text
    /// CATFISH_TEST_URL=https://192.168.31.199/ cargo test --lib -- --ignored --nocapture
    /// ```
    #[tokio::test]
    #[ignore]
    async fn real_request_to_central_server() {
        let Ok(url) = std::env::var("CATFISH_TEST_URL") else {
            // 不 panic —— 裸跑 `cargo test -- --ignored` 时没配 URL 是正常情况,
            // 红一条会让人误以为代码坏了 (7/28 就误会过一次). 打提示跳过.
            eprintln!("跳过: 未设 CATFISH_TEST_URL (例: CATFISH_TEST_URL=https://192.168.31.199/)");
            return;
        };

        // 先把"证书文件到底有没有被读进来"打出来 —— 分开 (b) 和 (c).
        match cert_path() {
            Some(p) if p.exists() => println!("证书文件: {} (存在)", p.display()),
            Some(p) => println!("证书文件: {} ⚠ 不存在", p.display()),
            None => println!("⚠ 拿不到 HOME, 无法定位证书"),
        }
        println!(
            "加载结果: {}",
            if extra_root_cert().is_some() {
                "✓ 已作为根证书加入信任"
            } else {
                "✗ 没加载到 (文件缺失 / 不是合法 PEM)"
            }
        );

        let client = central_client(Duration::from_secs(10)).expect("建 client");
        match client.get(&url).send().await {
            Ok(r) => println!("✓ 连通 · HTTP {}", r.status()),
            Err(e) => {
                // 打完整 source 链 —— reqwest 的 Display 只有最外层, 看不出真因.
                let mut chain = vec![e.to_string()];
                let mut src = std::error::Error::source(&e);
                while let Some(s) = src {
                    chain.push(s.to_string());
                    src = s.source();
                }
                panic!("✗ 连不上 {url}\n   {}", chain.join("\n   ← "));
            }
        }
    }

    /// 代理开关的默认值必须是"绕过".
    ///
    /// 这条是 7/28 那次误诊的直接产物: 门户探活被 Clash 劫走, 表现成
    /// "连不上", 查了好几轮才定位. 默认值一旦被改成"走代理", 同样的坑会
    /// 原样重现, 所以钉一条测试守住.
    #[test]
    fn system_proxy_off_by_default() {
        assert!(!parse_allow_flag(None), "默认必须绕过代理");
        assert!(parse_allow_flag(Some("1")));
    }

    #[test]
    fn builder_still_builds_without_cert_file() {
        // 没有证书文件时必须照常能建 client (退回默认严格校验),
        // 不能因为缺文件就把整个 http 层搞挂.
        let c = central_client(Duration::from_secs(5));
        assert!(c.is_ok(), "缺证书文件时 client 建不出来: {:?}", c.err());
    }
}
