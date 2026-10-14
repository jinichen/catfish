//! 「接入码」导入中央内部 CA (10/14 鸿波「现在的证书导入方式是不是比较麻烦」)。
//!
//! 原来: 管理员上服务器找 certs/ca.pem → 用微信 / U 盘发给每个员工 → 员工「选择文件」导入。
//! 现在: 管理员只发「门户地址 + 接入码」一句话; Companion 自己去 `<门户>/catfish-ca.pem`
//! 取证书, **算出的指纹跟接入码对上才保存**。
//!
//! 为什么取证书时可以不校验 TLS: 这时本来就还不认识这张 CA (要的就是它)。安全性来自
//! 接入码 —— 它是管理员从服务器上看到、走另一条路 (群消息 / 口头) 发来的, 冒充中央的人
//! 造不出指纹相同的证书。对不上一律不保存。
//!
//! 接入码 = SHA-256(CA 证书 DER) 前 8 字节, 大写十六进制, 4 位一组: `3F9A-1C2B-77D0-9E41`。
//! 同一算法在三处: certgen.py (setup 打印 / certs/pairing-code.txt)、中央门户「用户」页、这里。
use sha2::{Digest, Sha256};

const CA_PATH: &str = "/catfish-ca.pem";
const MAX_PEM_BYTES: usize = 64 * 1024;

/// 员工手抄的接入码: 去掉横线空格, 转大写, 把容易抄混的 O→0、I/L→1。必须是 16 位十六进制。
pub(crate) fn normalize_code(input: &str) -> Result<String, String> {
    let code: String = input
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .map(|c| match c.to_ascii_uppercase() {
            'O' => '0',
            'I' | 'L' => '1',
            other => other,
        })
        .collect();
    if code.len() != 16 || !code.chars().all(|c| c.is_ascii_hexdigit()) {
        return Err("接入码格式不对: 应该是 16 位, 形如 3F9A-1C2B-77D0-9E41".into());
    }
    Ok(code)
}

/// 取 PEM 里第一张证书 (只要 CERTIFICATE 块, 带私钥的一律拒), 返回 (这一块 PEM, DER)。
pub(crate) fn first_certificate(pem: &str) -> Result<(String, Vec<u8>), String> {
    if pem.contains("PRIVATE KEY") {
        return Err("服务器返回的内容带私钥, 拒收".into());
    }
    const BEGIN: &str = "-----BEGIN CERTIFICATE-----";
    const END: &str = "-----END CERTIFICATE-----";
    let start = pem.find(BEGIN).ok_or("服务器没有返回证书 (catfish-ca.pem 不是 PEM 证书)")?;
    let end = pem[start..].find(END).ok_or("证书不完整")? + start + END.len();
    let block = &pem[start..end];
    let body: String = block[BEGIN.len()..block.len() - END.len()]
        .chars()
        .filter(|c| !c.is_whitespace())
        .collect();
    use base64::Engine;
    let der = base64::engine::general_purpose::STANDARD
        .decode(body)
        .map_err(|e| format!("证书内容解不开: {e}"))?;
    Ok((format!("{block}\n"), der))
}

pub(crate) fn pairing_code(der: &[u8]) -> String {
    let digest = Sha256::digest(der);
    digest[..8].iter().map(|b| format!("{b:02X}")).collect()
}

pub(crate) fn pretty(code: &str) -> String {
    code.as_bytes().chunks(4).map(|c| std::str::from_utf8(c).unwrap_or("")).collect::<Vec<_>>().join("-")
}

/// 门户地址 + 接入码 → 取 CA、核对、保存。返回保存路径。
#[tauri::command(rename_all = "camelCase")]
pub async fn pair_server_certificate(web_url: String, code: String) -> Result<String, String> {
    let want = normalize_code(&code)?;
    let base = web_url.trim().trim_end_matches('/');
    if !base.starts_with("https://") {
        return Err("门户地址是 http:// 的不需要接入码 (只有 HTTPS 部署才要装证书)".into());
    }
    let url = format!("{base}{CA_PATH}");
    // 只为取这一张公开证书放宽校验 (见文件头); 不走系统代理, 跟其它连中央的请求一致
    let client = reqwest::Client::builder()
        .danger_accept_invalid_certs(true)
        .no_proxy()
        .connect_timeout(std::time::Duration::from_secs(10))
        .timeout(std::time::Duration::from_secs(20))
        .build()
        .map_err(|e| format!("建 http client 失败: {e}"))?;
    let resp = client.get(&url).send().await.map_err(|e| {
        format!("连不上中央 ({url}): {}", crate::services::components::error_chain(&e))
    })?;
    if resp.status() == reqwest::StatusCode::NOT_FOUND {
        return Err("中央还不支持接入码 (没有 /catfish-ca.pem), 请让 IT 升级中央, 或用「选择文件」导入 ca.pem".into());
    }
    if !resp.status().is_success() {
        return Err(format!("取证书失败: HTTP {}", resp.status()));
    }
    let bytes = resp.bytes().await.map_err(|e| format!("取证书失败: {e}"))?;
    if bytes.len() > MAX_PEM_BYTES {
        return Err("服务器返回的证书太大, 拒收".into());
    }
    let pem = String::from_utf8_lossy(&bytes);
    let (block, der) = first_certificate(&pem)?;
    let got = pairing_code(&der);
    if got != want {
        log::warn!("[pairing] 接入码不符: 输入 {} · 服务器证书 {}", pretty(&want), pretty(&got));
        return Err(format!(
            "接入码对不上, 没有导入。可能是接入码抄错了, 也可能连到的不是公司的中央服务器 ({base})。\
             请跟 IT 核对地址和接入码。"
        ));
    }
    let path = crate::util::http_client::cert_path().ok_or("无法确定用户证书目录")?;
    crate::util::http_client::save_root_cert(&path, &block)?;
    log::info!("[pairing] 接入码核对通过, 已导入中央 CA → {}", path.display());
    Ok(path.display().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    const PEM: &str = include_str!("../util/test_root_ca.txt");

    #[test]
    fn hand_typed_codes_are_normalized() {
        assert_eq!(normalize_code("3f9a-1c2b-77d0-9e41").unwrap(), "3F9A1C2B77D09E41");
        assert_eq!(normalize_code(" 3F9A 1C2B 77DO 9E4l ").unwrap(), "3F9A1C2B77D09E41");
        assert!(normalize_code("3F9A-1C2B").is_err());
        assert!(normalize_code("ZZZZ-1C2B-77D0-9E41").is_err());
    }

    #[test]
    fn fingerprint_matches_openssl_style_sha256_prefix() {
        let (block, der) = first_certificate(PEM).unwrap();
        assert!(block.starts_with("-----BEGIN CERTIFICATE-----"));
        let full: String = Sha256::digest(&der).iter().map(|b| format!("{b:02X}")).collect();
        assert_eq!(pairing_code(&der), full[..16]);
        // 跟 certgen.py / openssl 算出来的对过 (openssl x509 -outform DER | shasum -a 256)
        assert_eq!(pretty(&pairing_code(&der)), "0404-8028-BF1F-2864");
    }

    #[test]
    fn private_keys_and_garbage_are_refused() {
        assert!(first_certificate("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----").is_err());
        assert!(first_certificate("<html>404</html>").is_err());
    }

    /// 真中央 (或 catfish-web 镜像起的 nginx) 端到端。默认跳过:
    ///   CATFISH_TEST_PAIR_URL=https://127.0.0.1:18443 CATFISH_TEST_PAIR_CODE=<certs/pairing-code.txt> \
    ///   cargo test --lib server_pairing -- --ignored --test-threads=1
    #[tokio::test]
    #[ignore]
    async fn e2e_pair_against_real_central() {
        let url = std::env::var("CATFISH_TEST_PAIR_URL").unwrap();
        let code = std::env::var("CATFISH_TEST_PAIR_CODE").unwrap();
        let home = tempfile::tempdir().unwrap();
        std::env::set_var("HOME", home.path());
        let wrong = format!("{}0", &normalize_code(&code).unwrap()[..15]);
        let err = pair_server_certificate(url.clone(), wrong).await.unwrap_err();
        assert!(err.contains("对不上"), "{err}");
        assert!(!home.path().join(".catfish/server-ca.pem").exists(), "对不上不能保存");
        let path = pair_server_certificate(url.clone(), code.to_lowercase()).await.unwrap();
        assert!(std::path::Path::new(&path).starts_with(home.path()));
        // 导入后, 正常 (严格校验) 的客户端就能连中央了
        let client = crate::util::http_client::trust_central(reqwest::Client::builder()).build().unwrap();
        let ok = client.get(format!("{url}/catfish-ca.pem")).send().await;
        assert!(ok.is_ok(), "导入后严格校验应能连通: {:?}", ok.err().map(|e| crate::services::components::error_chain(&e)));
    }

}
