//! `hermes_jwt_sync.rs` 的测试 (JWT / config.yaml 同步)。
//!
//! 8/8 拆出来: 主文件加了 api_mode 的 4 条测试之后到了 837 行, 越过军规的
//! 800 行红线。非测试部分只有 583 行 —— 撑爆它的是测试, 所以拆测试而不是
//! 拆逻辑, 免得为了行数把内聚的东西切散。
//!
//! 用 `#[path]` 引进去而不是放 `tests/`, 因为这些测试要摸 `replace_model_field`
//! 这类私有函数 —— 集成测试目录看不见它们。模块仍挂在 `hermes_jwt_sync` 下,
//! 所以文件里的 `super::` 跟拆之前指的是同一个地方, 一行都不用改。

    use super::*;

    #[test]
    fn replace_env_existing() {
        let input = "OTHER=1\nOPENAI_API_KEY=old_jwt\nMORE=2\n";
        let out = replace_or_append_env_line(input, "OPENAI_API_KEY", "new_jwt");
        assert!(out.contains("OPENAI_API_KEY=new_jwt"));
        assert!(!out.contains("old_jwt"));
        assert!(out.contains("OTHER=1"));
        assert!(out.contains("MORE=2"));
    }

    #[test]
    fn replace_env_append_when_absent() {
        let input = "OTHER=1\n";
        let out = replace_or_append_env_line(input, "OPENAI_API_KEY", "new_jwt");
        assert!(out.contains("OTHER=1"));
        assert!(out.ends_with("OPENAI_API_KEY=new_jwt\n"));
    }

    #[test]
    fn replace_yaml_api_key_existing() {
        let input = "model:\n  name: catfish-auto\n  api_key: old_jwt\n  base_url: http://127.0.0.1:8999/v1\n";
        let out = replace_model_api_key(input, "new_jwt");
        assert!(out.contains("api_key: new_jwt"));
        assert!(!out.contains("old_jwt"));
        assert!(out.contains("name: catfish-auto"));
        assert!(out.contains("base_url:"));
    }

    #[test]
    fn replace_yaml_api_key_append_when_absent() {
        let input = "model:\n  name: catfish-auto\n  base_url: http://127.0.0.1:8999/v1\n";
        let out = replace_model_api_key(input, "new_jwt");
        assert!(out.contains("api_key: new_jwt"));
        assert!(out.contains("name: catfish-auto"));
    }

    #[test]
    fn replace_yaml_preserves_other_top_sections() {
        let input = "browser:\n  cdp_url: ws://x\nmodel:\n  api_key: old\nplugins:\n  enabled:\n  - foo\n";
        let out = replace_model_api_key(input, "new");
        assert!(out.contains("api_key: new"));
        assert!(out.contains("browser:"));
        assert!(out.contains("plugins:"));
        assert!(out.contains("cdp_url: ws://x"));
    }

    // ── api_mode 钉死 (8/8) ────────────────────────────────────────────
    //
    // 见 ensure_model_api_mode 的注释: hermes v0.20 会让 provider 声明的
    // transport (openai-api → codex_responses) 决定协议, 于是打到我们网关的
    // 请求变成 /v1/responses —— 网关没这条路由, 404。

    /// bootstrap 刚写完的 config 里没有 api_mode —— 这是实际发生的形态。
    #[test]
    fn 缺_api_mode_时插进去() {
        let input = "model:\n  api_key: jwt\n  base_url: http://127.0.0.1:8999/v1\n  provider: openai-api\n  default: catfish-auto\nweb:\n  backend: tavily\n";
        let out = ensure_model_api_mode(input);
        assert!(out.contains("api_mode: chat_completions"), "没插进去: {out}");
        // 插的位置必须还在 model 段里 (缩进两格), 不能掉到 web 段下面
        let model_block: String = out
            .lines()
            .skip_while(|l| !l.starts_with("model:"))
            .skip(1)
            .take_while(|l| l.starts_with(' '))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(model_block.contains("api_mode: chat_completions"), "插错段了: {out}");
        // 其余字段一个都不能丢
        for keep in ["api_key: jwt", "base_url: http://127.0.0.1:8999/v1",
                     "provider: openai-api", "default: catfish-auto", "backend: tavily"] {
            assert!(out.contains(keep), "丢了 {keep}: {out}");
        }
    }

    /// 已经有了就只是覆写, 不能插出第二行。
    #[test]
    fn 已有_api_mode_时幂等() {
        let input = "model:\n  api_key: jwt\n  api_mode: chat_completions\n  provider: openai-api\n";
        let out = ensure_model_api_mode(input);
        assert_eq!(out.matches("api_mode:").count(), 1, "重复插了: {out}");
        // 再跑一次仍然一样 —— 这个函数每次 JWT 同步都会跑
        assert_eq!(ensure_model_api_mode(&out), out);
    }

    /// 被人改成 codex_responses 了也要掰回来 —— 那正是坏掉的那个值。
    #[test]
    fn 覆写掉_codex_responses() {
        let input = "model:\n  api_key: jwt\n  api_mode: codex_responses\n";
        let out = ensure_model_api_mode(input);
        assert!(out.contains("api_mode: chat_completions"));
        assert!(!out.contains("codex_responses"), "没掰回来: {out}");
    }

    /// 跟 api_key 替换串起来跑一遍 —— sync_config_yaml 里就是这个顺序。
    #[test]
    fn 跟_api_key_替换串起来不打架() {
        let input = "model:\n  api_key: old\n  provider: openai-api\n";
        let out = ensure_model_api_mode(&replace_model_api_key(input, "newjwt"));
        assert!(out.contains("api_key: newjwt"));
        assert!(out.contains("api_mode: chat_completions"));
        assert!(out.contains("provider: openai-api"));
    }

    // ─── P49 · API_SERVER_HOST → 0.0.0.0 ──────────────────────────────

    /// 本机 .env 现状就是这一行 (9/10 查证第 483 行), 必须判"要改"。
    #[test]
    fn api_server_host_127_要改() {
        let env = "API_SERVER_KEY=k\nAPI_SERVER_ENABLED=true\nAPI_SERVER_HOST=127.0.0.1\nAPI_SERVER_PORT=8642\n";
        assert!(api_server_host_needs_update(env));
    }

    /// 幂等的判据: 已经是规范形态就不碰文件 (mtime 不动)。
    #[test]
    fn api_server_host_已是_0000_不改() {
        let env = "API_SERVER_HOST=0.0.0.0\n";
        assert!(!api_server_host_needs_update(env));
    }

    /// 没这行 (hermes 老版本 / 手动删过) 也要补。
    #[test]
    fn api_server_host_缺行_要改() {
        assert!(api_server_host_needs_update("API_SERVER_KEY=k\n"));
        assert!(api_server_host_needs_update(""));
    }

    /// 带空格 / 引号的变体算"还得改" —— 交给 replace_or_append 统一写成规范形态,
    /// 不在判据里做宽松匹配 (宽了会把 `0.0.0.0 # 注释` 这种半吊子也当已改)。
    #[test]
    fn api_server_host_变体_要改() {
        assert!(api_server_host_needs_update("API_SERVER_HOST = 0.0.0.0\n"));
        assert!(api_server_host_needs_update("API_SERVER_HOST=\"0.0.0.0\"\n"));
        assert!(api_server_host_needs_update("api_server_host=0.0.0.0\n"));
    }

    /// 跟 replace_or_append_env_line 串起来: 127.0.0.1 → 0.0.0.0, 邻居行一个不动。
    /// 这条钉的是"改对了那一行、没伤到 KEY/PORT"—— KEY 被改掉等于门锁没了。
    #[test]
    fn api_server_host_替换_不伤邻居() {
        let env = "API_SERVER_KEY=secret\nAPI_SERVER_HOST=127.0.0.1\nAPI_SERVER_PORT=8642\n";
        let out = replace_or_append_env_line(env, "API_SERVER_HOST", "0.0.0.0");
        assert!(out.contains("API_SERVER_HOST=0.0.0.0"));
        assert!(!out.contains("127.0.0.1"));
        assert!(out.contains("API_SERVER_KEY=secret"), "KEY 行被动了: {out}");
        assert!(out.contains("API_SERVER_PORT=8642"), "PORT 行被动了: {out}");
        assert!(!api_server_host_needs_update(&out), "改完再判应该是'不用改'");
    }
