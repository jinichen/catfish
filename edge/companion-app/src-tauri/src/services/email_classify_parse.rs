//! 邮件分诊 LLM 输出的解析 (8/21) —— 纯函数, 只依赖 serde_json。
//!
//! # 为什么单独一个文件
//!
//! 1. **可就地验证**: 这个文件不 use crate:: 任何东西, 沙箱/本机可以用一个
//!    只依赖 serde_json 的迷你 crate `include!` 同一份源码跑测试, 不用编译
//!    整个 tauri crate (Linux 上要 webkit2gtk 全家桶)。
//! 2. **8/15 事故现场**: 这条链 (批量 LLM 评级) 就是当天烧 2470 万 token 的
//!    地方。解析器的宽严直接决定重试风暴会不会复发 —— 它值得被钉死。
//!
//! # 协议 (跟 email_llm.rs 的 prompt 一体两面, 改一处必改另一处)
//!
//! LLM 返回 JSON 数组, 每封一个对象:
//!
//! ```text
//! [{"u":"急","a":"办","d":"2026-09-10"}, {"u":"低","a":"知"}]
//!
//! u: 急/中/低         —— 紧急度, 走老 Urgency 链路 (cache 形状不动)
//! a: 知/回/办          —— 这封要员工干什么: 知悉即可 / 要回信 / 要办事
//! d: YYYY-MM-DD, 可缺  —— 截止日, 只有 a="办" 或 "回" 才有意义
//! ```
//!
//! # 兼容老格式 (故意的, 不是懒)
//!
//! 老 prompt 返 `["急","中","低"]`。模型退化 / 换了个不听话的模型时很可能
//! 还这么返。解析器兼容纯字符串数组: urgency 照收, action 记 None —— badge
//! 少一个, 但**紧急度链路不断**。判据宽一格的方向是安全的: 宁可 action 缺,
//! 不可 urgency 断 (通知/桌宠都靠它)。
//!
//! # deadline 只做格式校验
//!
//! `d` 必须严格 YYYY-MM-DD 才收, 其它一律丢弃 (记 None)。不做"日期是否真在
//! 邮件里"的判断 —— 那是模型的活; 但格式烂的直接丢, 免得 "9月10日" / "下周五"
//! 这种进了 UI 又要一层解析。

use serde_json::Value;

/// 一封邮件的分诊结果。urgency 是老链路的字符串标签 (急/中/低);
/// action / deadline 是 8/21 新增, 可缺。
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Classification {
    pub(crate) urgency_label: String,
    /// "知" / "回" / "办", None = 模型没给 (老格式) 或给了认不出的值
    pub(crate) action: Option<String>,
    /// YYYY-MM-DD, 只在格式严格合法时为 Some
    pub(crate) deadline: Option<String>,
}

/// 严格 YYYY-MM-DD。只查形状: 4位数字-2位数字-2位数字, 月 01-12, 日 01-31。
/// 不查闰年/大小月 —— 这里挡的是"9月10日前"这种自由文本, 不是万年历。
fn valid_deadline(s: &str) -> bool {
    let b = s.as_bytes();
    if b.len() != 10 || b[4] != b'-' || b[7] != b'-' {
        return false;
    }
    if !b.iter().enumerate().all(|(i, c)| i == 4 || i == 7 || c.is_ascii_digit()) {
        return false;
    }
    let month: u32 = s[5..7].parse().unwrap_or(0);
    let day: u32 = s[8..10].parse().unwrap_or(0);
    (1..=12).contains(&month) && (1..=31).contains(&day)
}

fn norm_action(s: &str) -> Option<String> {
    let t = s.trim();
    // 模型可能返 "知悉"/"要回"/"要办" 全称 —— 按首字归一
    if t.starts_with('知') {
        Some("知".to_string())
    } else if t.starts_with('回') || t.starts_with("要回") {
        Some("回".to_string())
    } else if t.starts_with('办') || t.starts_with("要办") {
        Some("办".to_string())
    } else {
        None
    }
}

/// 从 LLM 输出解析分诊数组。容错: markdown fence / 前后多余文字都扒。
///
/// 兼容两种元素形状:
///   对象 {"u","a","d"} —— 新协议
///   纯字符串 "急"       —— 老协议 (action/deadline 记 None)
pub(crate) fn parse_classifications(s: &str) -> Result<Vec<Classification>, String> {
    let trimmed = s.trim();
    let start = trimmed
        .find('[')
        .ok_or_else(|| format!("没 JSON array: {trimmed:?}"))?;
    let end = trimmed
        .rfind(']')
        .ok_or_else(|| format!("没 ] 闭合: {trimmed:?}"))?;
    if end <= start {
        return Err(format!("] 在 [ 前: {trimmed:?}"));
    }
    let arr: Vec<Value> = serde_json::from_str(&trimmed[start..=end])
        .map_err(|e| format!("JSON 解析失败 ({e})"))?;

    Ok(arr
        .into_iter()
        .map(|v| match v {
            Value::String(label) => Classification {
                urgency_label: label,
                action: None,
                deadline: None,
            },
            Value::Object(o) => {
                let urgency_label = o
                    .get("u")
                    .and_then(|x| x.as_str())
                    .unwrap_or("中")
                    .to_string();
                let action = o.get("a").and_then(|x| x.as_str()).and_then(norm_action);
                let deadline = o
                    .get("d")
                    .and_then(|x| x.as_str())
                    .filter(|d| valid_deadline(d))
                    .map(|d| d.to_string());
                // deadline 只对 回/办 有意义 —— "知悉"带 deadline 是模型没想清楚,
                // 丢日期保动作 (知悉不产生待办, 挂个日期只会让员工困惑)。
                // 第一版这里写的是 `action.is_some()` —— 但"知"也是 Some,
                // 测试当场抓红。判据要贴着"产生待办的动作", 不是"有动作"。
                let deadline = match action.as_deref() {
                    Some("回") | Some("办") => deadline,
                    _ => None,
                };
                Classification { urgency_label, action, deadline }
            }
            other => Classification {
                urgency_label: format!("{other}"),
                action: None,
                deadline: None,
            },
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn c(u: &str, a: Option<&str>, d: Option<&str>) -> Classification {
        Classification {
            urgency_label: u.to_string(),
            action: a.map(str::to_string),
            deadline: d.map(str::to_string),
        }
    }

    #[test]
    fn new_protocol_full() {
        let r = parse_classifications(
            r#"[{"u":"急","a":"办","d":"2026-09-10"},{"u":"低","a":"知"}]"#,
        )
        .unwrap();
        assert_eq!(r, vec![c("急", Some("办"), Some("2026-09-10")), c("低", Some("知"), None)]);
    }

    #[test]
    fn old_protocol_strings_still_work() {
        // 模型退化返老格式 → urgency 不断, action 空。这是故意的兼容, 别"修掉"。
        let r = parse_classifications(r#"["急","中","低"]"#).unwrap();
        assert_eq!(r.len(), 3);
        assert_eq!(r[0].urgency_label, "急");
        assert!(r.iter().all(|x| x.action.is_none()));
    }

    #[test]
    fn markdown_fence_and_prefix_text() {
        let r = parse_classifications("评级如下:\n```json\n[{\"u\":\"中\",\"a\":\"回\"}]\n```\n完")
            .unwrap();
        assert_eq!(r, vec![c("中", Some("回"), None)]);
    }

    #[test]
    fn bad_deadline_dropped_not_propagated() {
        // "9月10日" / "下周五" / 年月日残缺 —— 全丢, 不让自由文本流进 UI
        for bad in ["9月10日", "2026-9-1", "2026-13-01", "2026-09-32", "next friday", ""] {
            let s = format!(r#"[{{"u":"急","a":"办","d":"{bad}"}}]"#);
            let r = parse_classifications(&s).unwrap();
            assert_eq!(r[0].deadline, None, "坏日期 {bad:?} 竟然被收了");
            assert_eq!(r[0].action.as_deref(), Some("办"), "丢日期不该连动作一起丢");
        }
    }

    #[test]
    fn deadline_without_action_dropped() {
        // a="知" 还带 d → 日期丢弃 (知悉不产生待办, 挂个日期只会困惑)
        let r = parse_classifications(r#"[{"u":"中","a":"知","d":"2026-09-10"}]"#).unwrap();
        assert_eq!(r[0].deadline, None);
    }

    #[test]
    fn action_full_names_normalized() {
        let r = parse_classifications(
            r#"[{"u":"中","a":"知悉"},{"u":"中","a":"要回"},{"u":"急","a":"要办","d":"2026-09-10"}]"#,
        )
        .unwrap();
        assert_eq!(r[0].action.as_deref(), Some("知"));
        assert_eq!(r[1].action.as_deref(), Some("回"));
        assert_eq!(r[2].action.as_deref(), Some("办"));
        assert_eq!(r[2].deadline.as_deref(), Some("2026-09-10"));
    }

    #[test]
    fn unknown_action_is_none_not_guess() {
        // 认不出的 action 记 None, 不猜 —— badge 空白好过贴错标签
        let r = parse_classifications(r#"[{"u":"中","a":"whatever"}]"#).unwrap();
        assert_eq!(r[0].action, None);
    }

    #[test]
    fn no_array_errors() {
        assert!(parse_classifications("急 中 低").is_err());
        assert!(parse_classifications("").is_err());
    }
}
