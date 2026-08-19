//! 取密码通道的**纯协议层** —— 一行请求进, 一个响应 JSON 出。
//!
//! # 为什么跟 socket.rs 分开
//!
//! 跟 index.rs 分出来是同一个理由 (见那个文件头): 这里一行 tauri、一行 keyring、
//! 一行 `std::os::unix` 都没有, 所以在没有 GTK 的 CI / 沙箱里也编得动、跑得了
//! (`companion-app/pure-tests/`)。
//!
//! 而 socket.rs 是 macOS-only 的 (`std::os::unix::net`), 它的测试在 Linux 上一次
//! 都跑不到。判据最容易错的恰恰是这一层 —— **哪些 service 放行**。让它跟着一个
//! 跑不了的文件走, 等于没测。
//!
//! 密码怎么读是 caller 传进来的 (`lookup`), 这里不知道有钥匙串这回事。

use serde_json::{json, Value};

// 只服务这个命名空间 —— 从 index.rs 拿, **不另抄一份**。
//
// 抄一份的后果: 存的时候拼 `catfish-teaching:x`, 取的时候白名单认的是另一个串,
// 表现成"存进去了但取不出来", 而且两边各自看都正常。8/17 那个 bug 就是这形状。
use super::index::SERVICE_PREFIX;

// JSON-RPC error code。前四个跟 tool-bridge server.py 对齐, 后三个是本通道自己的
// (-32000..-32099 是 JSON-RPC 留给实现自定义的区间)。
pub(crate) const PARSE_ERROR: i64 = -32700;
pub(crate) const INVALID_REQUEST: i64 = -32600;
pub(crate) const METHOD_NOT_FOUND: i64 = -32601;
pub(crate) const INVALID_PARAMS: i64 = -32602;
/// 钥匙串里没这一条。跟"读失败"分开 —— 员工的动作不一样 (这个是"去存一次")。
pub(crate) const NOT_FOUND: i64 = -32001;
/// service 不在白名单。
pub(crate) const FORBIDDEN: i64 = -32002;
/// 找得到但取值出错。
pub(crate) const READ_FAILED: i64 = -32003;

/// service 名的长度上限。正常是 `catfish-teaching:` + 一个 hostname / 短名字。
const MAX_SERVICE_LEN: usize = 200;

fn err(id: Value, code: i64, message: &str) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}})
}

/// 请求行 → 响应。`lookup(service)` 返 `Ok(None)` 表示"库里没这条"。
///
/// # 红线
///
/// 密码只出现在成功那一支的 `result.password` 里。**任何 error 分支都不许把它
/// 拼进 message** —— 对端 (companion_secrets.py) 会把 message 原样给员工看,
/// 那句话会进工具返回值 → SSE → 模型上下文 → hermes state.db, 撤销不了。
pub(crate) fn respond<F>(line: &str, lookup: F) -> Value
where
    F: FnOnce(&str) -> Result<Option<String>, String>,
{
    let req: Value = match serde_json::from_str(line) {
        Ok(v) => v,
        // ⚠ 不回显原文。请求里目前只有 service 名, 但这条通道上不养这个习惯 ——
        //   反方向那条 (tool_bridge_rpc.rs:117) 回显原文, 照抄过来就是泄漏。
        Err(_) => return err(Value::Null, PARSE_ERROR, "请求不是合法 JSON"),
    };
    let id = req.get("id").cloned().unwrap_or(Value::Null);

    if req.get("jsonrpc").and_then(|v| v.as_str()) != Some("2.0") {
        return err(id, INVALID_REQUEST, "需要 jsonrpc=2.0");
    }
    if req.get("method").and_then(|v| v.as_str()) != Some("secret/get") {
        return err(id, METHOD_NOT_FOUND, "只支持 secret/get");
    }

    let service = match req
        .get("params")
        .and_then(|p| p.get("service"))
        .and_then(|s| s.as_str())
    {
        Some(s) if !s.is_empty() => s,
        _ => return err(id, INVALID_PARAMS, "params.service 必填"),
    };

    // ── 白名单。见 socket.rs 文件头: 防的是我们自己乱用这条通道, 不是防黑客。──
    //
    // 判据是 starts_with, 不是 contains —— `evil:catfish-teaching:x` 这种
    // 含前缀但不以它开头的必须拒掉。也不 trim、不忽略大小写: 合法的 service 名
    // 是我们自己 (index.rs::service_name) 拼出来的, 一个字节都不会差, 放宽只会
    // 让白名单变松。
    if !service.starts_with(SERVICE_PREFIX) {
        return err(id, FORBIDDEN, "这条通道只给教学凭据 (catfish-teaching:*) 用");
    }
    // 控制字符会把日志和后面的 JSON 搞乱, 而合法的 service 名里不可能有。
    if service.len() > MAX_SERVICE_LEN || service.chars().any(char::is_control) {
        return err(id, INVALID_PARAMS, "service 名过长或含不可用字符");
    }

    match lookup(service) {
        Ok(Some(pwd)) if !pwd.is_empty() => {
            json!({"jsonrpc": "2.0", "id": id, "result": {"password": pwd}})
        }
        // 空密码当"没有"。回一个空串, 对端会拿它去 page.fill, 登录失败得莫名其妙。
        Ok(_) => err(id, NOT_FOUND, "本机钥匙串里没有这一条 —— 在 Companion 里存一次"),
        Err(e) => err(id, READ_FAILED, &format!("读本机凭据库失败: {e}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PROBE: &str = "hunter2-DO-NOT-LEAK";

    fn line(service: &str) -> String {
        json!({"jsonrpc":"2.0","id":1,"method":"secret/get","params":{"service":service}})
            .to_string()
    }

    fn code_of(v: &Value) -> i64 {
        v["error"]["code"].as_i64().unwrap_or(0)
    }

    /// lookup 被叫到了没有 —— 白名单那几条测的就是"**没**叫到"。
    fn spy() -> (std::rc::Rc<std::cell::Cell<bool>>, impl FnOnce(&str) -> Result<Option<String>, String>)
    {
        let hit = std::rc::Rc::new(std::cell::Cell::new(false));
        let h = hit.clone();
        (hit, move |_: &str| {
            h.set(true);
            Ok(Some(PROBE.to_string()))
        })
    }

    #[test]
    fn 正常一次() {
        let v = respond(&line("catfish-teaching:neis.ffcs.cn"), |s| {
            assert_eq!(s, "catfish-teaching:neis.ffcs.cn");
            Ok(Some(PROBE.into()))
        });
        assert_eq!(v["result"]["password"], json!(PROBE));
        assert!(v.get("error").is_none());
    }

    #[test]
    fn 坏请求各报各的_不是笼统一个错() {
        // ⚠ 第一版这里只断言"有 error", 太宽 —— "把 method 校验整个删掉"和
        //   "空 service 放行"两个变异都跑绿了 (它们最后落到别的分支上, 照样报错)。
        //   判据必须钉到**具体是哪个错**, 否则等于只测了"没 panic"。
        for (l, want) in [
            ("", PARSE_ERROR),
            ("不是 JSON", PARSE_ERROR),
            ("{坏的", PARSE_ERROR),
            ("[1,2,3]", INVALID_REQUEST),
            ("null", INVALID_REQUEST),
            (r#"{"jsonrpc":"1.0","id":1,"method":"secret/get"}"#, INVALID_REQUEST),
            (r#"{"id":1,"method":"secret/get"}"#, INVALID_REQUEST),
            // ★ 带上合法 params —— 否则会先掉进 INVALID_PARAMS, 测不到 method 那道
            (
                r#"{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"service":"catfish-teaching:x"}}"#,
                METHOD_NOT_FOUND,
            ),
            (
                r#"{"jsonrpc":"2.0","id":1,"method":"secret/set","params":{"service":"catfish-teaching:x"}}"#,
                METHOD_NOT_FOUND,
            ),
            (r#"{"jsonrpc":"2.0","id":1,"method":"secret/get"}"#, INVALID_PARAMS),
            (r#"{"jsonrpc":"2.0","id":1,"method":"secret/get","params":{}}"#, INVALID_PARAMS),
            // ★ 空串走的是 INVALID_PARAMS(必填), 不是 FORBIDDEN —— 分开钉住,
            //   不然"空串也放行"这个变异会被 FORBIDDEN 悄悄接住
            (
                r#"{"jsonrpc":"2.0","id":1,"method":"secret/get","params":{"service":""}}"#,
                INVALID_PARAMS,
            ),
            (
                r#"{"jsonrpc":"2.0","id":1,"method":"secret/get","params":{"service":42}}"#,
                INVALID_PARAMS,
            ),
            (
                r#"{"jsonrpc":"2.0","id":1,"method":"secret/get","params":"字符串"}"#,
                INVALID_PARAMS,
            ),
        ] {
            let v = respond(l, |_| panic!("坏请求不该走到 lookup: {l}"));
            assert_eq!(code_of(&v), want, "错码不对: {l}");
            assert!(v.get("result").is_none(), "报错时不该有 result: {l}");
        }
    }

    #[test]
    fn 前缀不对一律拒绝_而且根本不去查() {
        // ★ 关键是**没查**。查了再拒等于这条通道能被拿来探测钥匙串里有什么。
        for bad in [
            "eis_password",               // 4/28 手工那条, 走它自己的老路
            "com.apple.something",        // 系统的
            "login",                      // 钥匙串本身
            "evil:catfish-teaching:neis", // 含前缀但不在开头
            "CATFISH-TEACHING:neis",      // 大小写不放宽
            " catfish-teaching:neis",     // 不 trim
            "catfish-teaching",           // 差一个冒号 —— 不是这个命名空间
        ] {
            let (hit, f) = spy();
            assert_eq!(code_of(&respond(&line(bad), f)), FORBIDDEN, "该拒: {bad}");
            assert!(!hit.get(), "拒掉的请求不该去查钥匙串: {bad}");
        }
    }

    #[test]
    fn 前缀对的才放行() {
        let (hit, f) = spy();
        let v = respond(&line("catfish-teaching:neis.ffcs.cn"), f);
        assert!(hit.get());
        assert_eq!(v["result"]["password"], json!(PROBE));
    }

    #[test]
    fn 控制字符和超长名字挡掉_也不去查() {
        for bad in [
            "catfish-teaching:a\nb".to_string(),
            "catfish-teaching:a\0b".to_string(),
            format!("catfish-teaching:{}", "x".repeat(MAX_SERVICE_LEN)),
        ] {
            let (hit, f) = spy();
            assert_eq!(code_of(&respond(&line(&bad), f)), INVALID_PARAMS, "该拒: {bad:?}");
            assert!(!hit.get());
        }
    }

    #[test]
    fn 没这条和读失败要分开() {
        // 员工的动作不一样: 前者"去存一次", 后者"钥匙串出问题了"。
        // 合成一个的话前端只能给一句含糊的话, 8/18 就是这么卡住的。
        assert_eq!(code_of(&respond(&line("catfish-teaching:x"), |_| Ok(None))), NOT_FOUND);
        assert_eq!(
            code_of(&respond(&line("catfish-teaching:x"), |_| Err("坏了".into()))),
            READ_FAILED
        );
    }

    #[test]
    fn 空密码当没有() {
        // 回空串, 对端会拿它去 page.fill, 登录失败得莫名其妙
        let v = respond(&line("catfish-teaching:x"), |_| Ok(Some(String::new())));
        assert_eq!(code_of(&v), NOT_FOUND);
        assert!(v.get("result").is_none());
    }

    #[test]
    fn id_原样带回() {
        // 客户端按 id 对号。丢了 id 就没法确认这是不是自己那条的回答。
        for id in [json!(1), json!("abc"), json!(null)] {
            let l = json!({"jsonrpc":"2.0","id":id,"method":"secret/get",
                           "params":{"service":"nope"}})
            .to_string();
            assert_eq!(respond(&l, |_| Ok(None))["id"], id);
        }
    }

    #[test]
    fn 密码只出现在_result_password_这一处() {
        // 挡的是"顺手再抄一份到 summary / echo / 日志字段里更好排查"那种改法。
        // 抄出去的那一份会跟着响应走完整条链, 收不回来。
        let v = respond(&line("catfish-teaching:x"), |_| Ok(Some(PROBE.into())));
        assert_eq!(v["result"]["password"], json!(PROBE));
        assert_eq!(v.to_string().matches(PROBE).count(), 1, "响应里出现了第二份密码");
        assert!(v.get("error").is_none());
    }

    #[test]
    fn 报错时绝不带_result() {
        // 对端只看 error 在不在就决定要不要往下走。两个都有 = 行为未定义。
        for v in [
            respond(&line("catfish-teaching:x"), |_| Ok(None)),
            respond(&line("catfish-teaching:x"), |_| Err("坏了".into())),
            respond(&line("eis_password"), |_| Ok(Some(PROBE.into()))),
            respond("{坏的", |_| Ok(Some(PROBE.into()))),
        ] {
            assert!(v.get("error").is_some());
            assert!(v.get("result").is_none());
            assert!(!v.to_string().contains(PROBE), "报错的响应里出现了密码");
        }
    }

    #[test]
    fn 跟_index_拼出来的_service_对得上() {
        // 存的时候拼的是 service_name().1, 取的时候白名单认的必须是同一个串。
        // 对不上 = "存进去了但取不出来", 两边各自看都正常 —— 8/17 那个 bug 的形状。
        let (_, target) = super::super::index::service_name("neis.ffcs.cn").unwrap();
        let (hit, f) = spy();
        assert_ne!(code_of(&respond(&line(&target), f)), FORBIDDEN, "白名单挡住了自己人: {target}");
        assert!(hit.get());
    }
}
