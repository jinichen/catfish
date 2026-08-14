//! OIDC 配置的来源与合并 —— env / ~/.catfish/companion.yaml / 中央下发。
//!
//! 2026-08-15 从 oauth.rs 切出来 (1043 行超限)。纯搬迁, 逻辑一行未改。
//!
//! `OidcConfig` 保持 pub —— lib.rs 和 commands/auth.rs 按老路径调
//! `oauth::OidcConfig::load()`, 所以 oauth.rs 里把它重新导出给那条路径用。
//!
//! ⚠ 这里只放**配置来源**。client_secret 之类的东西不在这条路径上:
//! 走的是 PKCE public client, 没有 secret 要存。

use anyhow::{anyhow, Context, Result};
use serde::Deserialize;

pub(crate) const DEFAULT_SCOPE: &str = "openid email profile";

/// 从 env 读 OIDC 配置.
#[derive(Debug, Clone)]
pub struct OidcConfig {
    pub issuer: String,
    pub client_id: String,
    /// gateway 用 audience 验 JWT, Companion 端不用 (id_token 我们不验签).
    /// 但保留字段, Phase 2 加 Companion 端 id_token 验签时用.
    #[allow(dead_code)]
    pub audience: String,
    pub scope: String,
}

/// ~/.catfish/companion.yaml 文件 schema (oidc 段子集).
#[derive(Debug, Deserialize)]
struct CompanionYamlFile {
    oidc: Option<CompanionYamlOidc>,
}

#[derive(Debug, Deserialize)]
struct CompanionYamlOidc {
    issuer: String,
    client_id: Option<String>,
    audience: Option<String>,
    scope: Option<String>,
}

impl OidcConfig {
    /// 加载 OIDC 配置. 优先级 (踩过坑 2026-04-29 鸿波):
    ///
    ///   1. ~/.catfish/companion.yaml 的 oidc 段 — **生产路径**, 员工友好
    ///   2. CATFISH_OIDC_* env — **dev / 测试路径**, terminal 启动用
    ///   3. 都没 → 自动生成默认 yaml + 返默认配置 (catfish-identity 本地)
    ///
    /// 为啥不只用 env: macOS .app 通过 LaunchServices 启动**不继承 terminal env**,
    /// 员工装完 .app 没人帮他 launchctl setenv. yaml 文件路径稳定, 装/部署友好.
    pub fn load() -> Result<Self> {
        // 1. yaml 文件
        if let Some(cfg) = Self::from_yaml_file()? {
            log::info!("OIDC 配置来自 ~/.catfish/companion.yaml");
            return Ok(cfg);
        }
        // 2. env 兜底 (dev / 测试)
        if std::env::var("CATFISH_OIDC_ISSUER").is_ok() {
            log::info!("OIDC 配置来自 env (生产应该用 ~/.catfish/companion.yaml)");
            return Self::from_env();
        }
        // 3. 都没: 自动生成默认 yaml, 配 catfish-identity 本地. 员工后续按需改.
        log::warn!(
            "OIDC 配置不存在, 自动生成默认 ~/.catfish/companion.yaml \
             (指向本地 catfish-identity:8998). 客户用别的 SSO 改这个文件即可."
        );
        Self::ensure_default_yaml()?;
        Self::from_yaml_file()?
            .ok_or_else(|| anyhow!("自动生成默认 yaml 后仍读不到, 内部 bug"))
    }

    pub fn from_env() -> Result<Self> {
        let issuer = std::env::var("CATFISH_OIDC_ISSUER")
            .map_err(|_| anyhow!("CATFISH_OIDC_ISSUER 没设"))?;
        let client_id = std::env::var("CATFISH_OIDC_CLIENT_ID")
            .unwrap_or_else(|_| "catfish-companion".into());
        let audience = std::env::var("CATFISH_OIDC_AUDIENCE")
            .unwrap_or_else(|_| client_id.clone());
        let scope = std::env::var("CATFISH_OIDC_SCOPE")
            .unwrap_or_else(|_| DEFAULT_SCOPE.into());
        Ok(Self {
            issuer: issuer.trim_end_matches('/').to_string(),
            client_id,
            audience,
            scope,
        })
    }

    fn yaml_path() -> Result<std::path::PathBuf> {
        let home = crate::util::paths::home_env()
            .or_else(|_| std::env::var("USERPROFILE"))
            .map_err(|_| anyhow!("找不到 HOME 环境变量"))?;
        Ok(std::path::PathBuf::from(home)
            .join(".catfish")
            .join("companion.yaml"))
    }

    fn from_yaml_file() -> Result<Option<Self>> {
        let path = Self::yaml_path()?;
        if !path.exists() {
            return Ok(None);
        }
        let content = std::fs::read_to_string(&path)
            .with_context(|| format!("读 {} 失败", path.display()))?;
        let parsed: CompanionYamlFile = serde_yaml::from_str(&content)
            .with_context(|| format!("解析 {} YAML 失败", path.display()))?;
        let oidc = match parsed.oidc {
            Some(o) => o,
            None => return Ok(None),  // 文件存在但没 oidc 段, fallback env
        };
        let client_id = oidc.client_id.unwrap_or_else(|| "catfish-companion".into());
        Ok(Some(Self {
            issuer: oidc.issuer.trim_end_matches('/').to_string(),
            audience: oidc.audience.unwrap_or_else(|| client_id.clone()),
            scope: oidc.scope.unwrap_or_else(|| DEFAULT_SCOPE.into()),
            client_id,
        }))
    }

    /// 第一次启动 (没 yaml 也没 env) 时生成默认; 已有 yaml 但缺 oidc 段时 *补*
    /// 而不是覆盖 (BL-COMPANION-YAML-MERGE 5/18 鸿波踩坑: 覆写 email 段时把
    /// oidc 段冲掉, 自动补又被 path.exists 早返跳过 → 登录永挂).
    fn ensure_default_yaml() -> Result<()> {
        let path = Self::yaml_path()?;
        if path.exists() {
            // 文件存在但可能缺 oidc 段. 追加默认 oidc, 不动其他段.
            return Self::append_default_oidc_if_missing(&path);
        }
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let content = r#"# Catfish Companion 配置文件
# 路径: ~/.catfish/companion.yaml
# 自动生成于第一次启动 Companion 时, 客户按需修改.
#
# 改完重启 Companion 即可生效. 员工不需要敲 launchctl setenv 这种命令.

oidc:
  # SSO 端点 (改这里). 三种典型场景:
  #   - 本机 demo:        http://127.0.0.1:8998
  #   - catfish-identity 中央:  http://10.10.40.50:8998
  #   - 企业 SSO (Azure AD/Okta/飞书): IT 给的 issuer URL
  issuer: http://127.0.0.1:8998

  # OIDC client 标识. catfish-identity 用默认就行, 企业 SSO 改成 IT 给的 GUID/App ID.
  client_id: catfish-companion

  # JWT audience claim — 必须跟 IdP 签 token 时填的 aud 一致, 否则 aud mismatch 401.
  # BL-WIN9.2 (5/8): catfish-identity 默认 audience = client_id, 所以这里也跟着填
  # 'catfish-companion' (而不是历史默认的 'test'). 之前默认 'test' 是早期 demo 残留,
  # 跟 catfish-identity 实际签的对不上, 改这条是修隐性 bug.
  audience: catfish-companion

  # OAuth scope. 决定 IdP 返哪些 claim. OIDC 标准三件套, 几乎不需要改.
  #   openid:  标识这是 OIDC 流 (必须)
  #   email:   拿员工邮箱 (catfish 用来识别员工)
  #   profile: 拿员工姓名 / 头像
  scope: openid email profile

# BL-WIN9 / DEPLOY1 (5/8): 网关地址配置 — 客户网关装中央服务器时改这里.
# 优先级: yaml > env (CATFISH_GATEWAY_HOST/PORT) > default 127.0.0.1:8999.
#
# 单机部署 (开发 / demo): 网关跟 Companion 同机, 注释掉这段, 走 default.
# 集中部署 (生产): 取消下面注释, 改 gateway_url 指你网关服务器.
# endpoints:
#   gateway_url: http://10.10.40.50:8999       # 客户网关地址
#   chrome_debug_url: http://127.0.0.1:9222    # Chrome 一般还在本机, 不动

# audit:
#   max_log_size_mb: 100
"#;
        std::fs::write(&path, content)
            .with_context(|| format!("写默认 yaml 到 {} 失败", path.display()))?;
        // 权限 0600 (含敏感配置时安全, 现在也提前打)
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
        }
        log::info!("已生成默认 ~/.catfish/companion.yaml, 客户按需修改 oidc.issuer");
        Ok(())
    }

    /// BL-COMPANION-YAML-MERGE (5/18): 文件存在但缺 oidc 段时追加默认.
    /// 不动现有 email / endpoints / agent / tts 等段.
    fn append_default_oidc_if_missing(path: &std::path::Path) -> Result<()> {
        let content = std::fs::read_to_string(path)
            .with_context(|| format!("读 {} 失败", path.display()))?;
        // 解 yaml 看顶层是否有 oidc key. 解失败 / 空文件都当 *缺*, append.
        let has_oidc = serde_yaml::from_str::<serde_yaml::Value>(&content)
            .ok()
            .and_then(|v| v.as_mapping().cloned())
            .map(|m| m.contains_key(serde_yaml::Value::String("oidc".into())))
            .unwrap_or(false);
        if has_oidc {
            return Ok(());
        }
        log::warn!(
            "{} 已存在但缺 oidc 段, 追加默认 oidc (指本地 catfish-identity:8998). \
             不动现有其他段.",
            path.display()
        );
        // 防末尾没换行直接接 → "}oidc:" 拼起来挂
        let separator = if content.ends_with('\n') { "" } else { "\n" };
        let appended = format!(
            "{content}{separator}\n# 5/18 自动补的 oidc 段 (登录必需). 客户用别的 SSO 改 issuer.\noidc:\n  issuer: http://127.0.0.1:8998\n  client_id: catfish-companion\n  audience: catfish-companion\n  scope: openid email profile\n"
        );
        std::fs::write(path, appended)
            .with_context(|| format!("追加 oidc 段到 {} 失败", path.display()))?;
        Ok(())
    }
}
