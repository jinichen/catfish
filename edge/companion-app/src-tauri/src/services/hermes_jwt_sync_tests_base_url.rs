//! `hermes_jwt_sync.rs` 的测试 (服务器地址同步)。
//!
//! 8/8 拆出来: 主文件加了 api_mode 的 4 条测试之后到了 837 行, 越过军规的
//! 800 行红线。非测试部分只有 583 行 —— 撑爆它的是测试, 所以拆测试而不是
//! 拆逻辑, 免得为了行数把内聚的东西切散。
//!
//! 用 `#[path]` 引进去而不是放 `tests/`, 因为这些测试要摸 `replace_model_field`
//! 这类私有函数 —— 集成测试目录看不见它们。模块仍挂在 `hermes_jwt_sync` 下,
//! 所以文件里的 `super::` 跟拆之前指的是同一个地方, 一行都不用改。

    use super::{replace_model_field, replace_or_append_env_line, rewrite_base_urls};
    use serde_json::json;

    #[test]
    fn env_openai_base_url_replaced_not_appended() {
        // 当晚真实 .env: CATFISH_GATEWAY_URL 已是新 IP, OPENAI_BASE_URL 还是
        // localhost. 两个都在同一个文件里, 只改前者 → SDK 读后者 → 连不上.
        let input = "CATFISH_GATEWAY_URL=http://10.0.0.5:8999\n\
                     OPENAI_BASE_URL=http://localhost:8999/v1\n\
                     API_SERVER_KEY=secret\n";
        let out = replace_or_append_env_line(input, "OPENAI_BASE_URL", "http://10.0.0.5:8999/v1");
        assert!(out.contains("OPENAI_BASE_URL=http://10.0.0.5:8999/v1"));
        assert!(!out.contains("localhost"), "旧地址必须被替换掉, 不能两行并存");
        // 别的 key 不能被动到
        assert!(out.contains("API_SERVER_KEY=secret"));
        assert_eq!(out.matches("OPENAI_BASE_URL=").count(), 1, "不能追加出第二行");
    }

    #[test]
    fn env_openai_base_url_appended_when_absent() {
        // hermes 装好但没配过 provider 的机器: .env 里根本没这个 key.
        let input = "API_SERVER_PORT=8642\n";
        let out = replace_or_append_env_line(input, "OPENAI_BASE_URL", "http://10.0.0.5:8999/v1");
        assert!(out.contains("API_SERVER_PORT=8642"));
        assert!(out.contains("OPENAI_BASE_URL=http://10.0.0.5:8999/v1"));
    }

    #[test]
    fn config_yaml_base_url_replaced_keeps_other_fields() {
        let input = "model:\n  api_key: jwt123\n  base_url: http://localhost:8999/v1\n  \
                     provider: openai-api\nweb:\n  backend: tavily\n";
        let out = replace_model_field(input, "base_url", "http://10.0.0.5:8999/v1");
        assert!(out.contains("base_url: http://10.0.0.5:8999/v1"));
        assert!(!out.contains("localhost"));
        // 同段其它字段 + 别的顶层段都不能被破坏
        assert!(out.contains("api_key: jwt123"));
        assert!(out.contains("provider: openai-api"));
        assert!(out.contains("backend: tavily"));
    }

    #[test]
    fn config_yaml_base_url_inserted_when_model_section_lacks_it() {
        let input = "model:\n  api_key: jwt123\n  provider: openai-api\nweb:\n  backend: tavily\n";
        let out = replace_model_field(input, "base_url", "http://10.0.0.5:8999/v1");
        assert!(out.contains("base_url: http://10.0.0.5:8999/v1"));
        assert!(out.contains("backend: tavily"), "不能插到别的段里去");
        // 必须插在 model 段内 (base_url 出现在 web: 之前)
        let i_base = out.find("base_url").unwrap();
        let i_web = out.find("web:").unwrap();
        assert!(i_base < i_web, "base_url 被插到了 model 段之外");
    }

    #[test]
    fn config_yaml_api_key_still_works_after_refactor() {
        // replace_model_api_key 改成了 replace_model_field 的包装, 别退化.
        let input = "model:\n  api_key: old\n  base_url: http://x/v1\n";
        let out = super::replace_model_api_key(input, "new");
        assert!(out.contains("api_key: new"));
        assert!(out.contains("base_url: http://x/v1"), "不该动到 base_url");
    }

    #[test]
    fn auth_json_rewrites_only_our_gateway() {
        // auth.json 里可能同时有员工自配的第三方 provider —— 那些不能动.
        let mut v = json!({
            "credential_pool": {
                "openai-api": [
                    {"base_url": "http://localhost:8999/v1", "key": "a"},
                    {"base_url": "https://api.openai.com/v1", "key": "b"}
                ],
                "azure": [{"base_url": "https://x.openai.azure.com", "key": "c"}]
            }
        });
        let n = rewrite_base_urls(&mut v, "http://10.0.0.5:8999/v1");
        assert_eq!(n, 1, "只该改指向我们 gateway(:8999) 的那一条");
        let pool = &v["credential_pool"]["openai-api"];
        assert_eq!(pool[0]["base_url"], "http://10.0.0.5:8999/v1");
        assert_eq!(pool[1]["base_url"], "https://api.openai.com/v1", "第三方 provider 被误改");
        assert_eq!(
            v["credential_pool"]["azure"][0]["base_url"],
            "https://x.openai.azure.com",
            "azure provider 被误改"
        );
    }

    #[test]
    fn auth_json_survives_structure_change() {
        // 写死 credential_pool.openai-api[0] 的话, hermes 换结构就静默失效.
        // 递归实现必须在任意嵌套下都找得到.
        let mut v = json!({
            "v2": {"providers": {"list": [{"nested": {"base_url": "http://1.2.3.4:8999/v1"}}]}}
        });
        let n = rewrite_base_urls(&mut v, "http://10.0.0.5:8999/v1");
        assert_eq!(n, 1);
        assert_eq!(
            v["v2"]["providers"]["list"][0]["nested"]["base_url"],
            "http://10.0.0.5:8999/v1"
        );
    }

    #[test]
    fn auth_json_no_match_is_zero_not_error() {
        let mut v = json!({"credential_pool": {"azure": [{"base_url": "https://x.azure.com"}]}});
        assert_eq!(rewrite_base_urls(&mut v, "http://10.0.0.5:8999/v1"), 0);
    }

    #[test]
    fn renewal_env_keys_written_and_idempotent() {
        // 7/28 实测: 续期该触发时日志两条 "secret 没配", 一次没续成 ——
        // 因为这两个键从来没人写. 这条测试盯住"确实写了 + 重复写不叠加".
        let input = "OPENAI_API_KEY=jwt\nAPI_SERVER_PORT=8642\n";
        let once = replace_or_append_env_line(
            &replace_or_append_env_line(input, "CATFISH_IDENTITY_URL", "http://10.0.0.5:8998"),
            "CATFISH_HERMES_CLIENT_SECRET",
            super::HERMES_CLI_SECRET,
        );
        assert!(once.contains("CATFISH_IDENTITY_URL=http://10.0.0.5:8998"));
        assert!(once.contains(&format!(
            "CATFISH_HERMES_CLIENT_SECRET={}",
            super::HERMES_CLI_SECRET
        )));
        assert!(once.contains("API_SERVER_PORT=8642"), "别的 key 被动了");

        // 换服务器后重写: 必须是替换而不是追加, 否则 dotenv 取哪一行看实现,
        // 正是"看着改对了其实没生效"这类故障的温床.
        let twice = replace_or_append_env_line(
            &once,
            "CATFISH_IDENTITY_URL",
            "http://10.0.0.9:8998",
        );
        assert_eq!(twice.matches("CATFISH_IDENTITY_URL=").count(), 1);
        assert!(twice.contains("CATFISH_IDENTITY_URL=http://10.0.0.9:8998"));
        assert!(!twice.contains("10.0.0.5"), "旧 identity 地址残留");
    }

    #[test]
    fn idempotent_second_run_is_noop() {
        // 面板连点两次保存, 结果必须一致.
        let input = "model:\n  base_url: http://localhost:8999/v1\n";
        let once = replace_model_field(input, "base_url", "http://10.0.0.5:8999/v1");
        let twice = replace_model_field(&once, "base_url", "http://10.0.0.5:8999/v1");
        assert_eq!(once, twice);
    }
