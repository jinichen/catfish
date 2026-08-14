//! wiki frontmatter 归一化 —— 受控词表 + authored_by 标记。
//!
//! 2026-08-15 从 wiki_write.rs 切出来。纯搬迁, 逻辑一行未改。
//!
//! ⚠ `include_str!` 的路径是**相对本源文件**的。这个文件必须留在
//! `src/commands/` 下 —— 挪到别的层级 (比如 util/) 那行路径会静默指错,
//! 而 `vocab_json_is_embedded_and_parses` 那条测试是唯一会喊的人。
//!
//! 搬迁时顺手修了一处: `mark_authored_by_employee` 的那段 doc 原来物理上贴在
//! `TYPE_VOCAB_JSON` 上面 (中间没有别的 item), 于是编译器把"员工亲手写的条目"
//! 那段说明挂到了词表常量上。两段各归各位, 不涉及代码。

/// 受控词表 —— 编译期嵌入 edge/contracts/wiki_type_vocab.json。
///
/// 打包后的 .app 里没有 edge/contracts/ 目录, 运行时读不到, 所以用
/// include_str! 编译期嵌进来。同一个文件, Python 侧
/// (catfish_memory_helpers._load_type_vocab) 运行时读它, 两边不会漂。
const TYPE_VOCAB_JSON: &str = include_str!("../../../../contracts/wiki_type_vocab.json");

/// 把 entity_type / concept_type 归一化到受控词表。
///
/// # 为什么 UI 侧也要做 (8/4 鸿波「新增的知识库能不能自动满足 ontology 规则」)
///
/// 之前**只有蒸馏侧归一化**, 员工在界面上手工建的条目不归一 —— 界面上敲
/// 「规则」就存「规则」, 蒸馏出来的同类条目存「rule」。同一个受控词表, 两条
/// 产线两个结果, 于是知识库里 rule/规则、standard/标准 长期并存, 指的是同一
/// 个东西。这跟今天修的"名字解析五套口径"是同一个病, 所以词表直接抽成共享
/// 文件, 不给它分叉的机会。
///
/// 表外的值**不拒绝, 只归一化大小写** —— 词表要能长。P3.5.176 删 enum 的
/// 理由仍然成立: 硬编码 enum 不是更严格, 是让员工场景里的真实类型无处可去。
fn canon_subtype(is_entity: bool, raw: &str) -> String {
    let v = raw.trim();
    if v.is_empty() {
        return String::new();
    }
    let low = v.to_lowercase();
    let Ok(vocab) = serde_json::from_str::<serde_json::Value>(TYPE_VOCAB_JSON) else {
        // 词表坏了不能挡住员工存条目 —— 原样返回
        log::warn!("受控词表解析失败, 类型不归一化");
        return v.to_string();
    };
    let aliases = &vocab["aliases"];
    let canon = aliases
        .get(v)
        .or_else(|| aliases.get(&low))
        .and_then(|x| x.as_str())
        .unwrap_or(&low)
        .to_string();
    if canon != low {
        log::info!("类型归一化: {v} → {canon}");
    }
    let key = if is_entity { "entity_types" } else { "concept_types" };
    let known = vocab[key]
        .as_array()
        .map(|a| a.iter().any(|x| x.as_str() == Some(canon.as_str())))
        .unwrap_or(false);
    if !known {
        // 不拒绝, 但要有人知道 —— 词表长不长得靠这条日志
        log::info!("类型「{canon}」在受控词表外 (不拒绝, 但请确认是不是新类型)");
    }
    canon
}

/// 把 frontmatter 里的 entity_type / concept_type 行换成归一化后的值。
pub(crate) fn normalize_type_line(content: &str, is_entity: bool) -> String {
    let key = if is_entity { "entity_type:" } else { "concept_type:" };
    content
        .lines()
        .map(|line| {
            let t = line.trim_start();
            if let Some(rest) = t.strip_prefix(key) {
                let canon = canon_subtype(is_entity, rest.trim().trim_matches('"').trim_matches('\''));
                if !canon.is_empty() {
                    let indent = &line[..line.len() - t.len()];
                    return format!("{indent}{key} {canon}");
                }
            }
            line.to_string()
        })
        .collect::<Vec<_>>()
        .join("\n")
        + if content.ends_with('\n') { "\n" } else { "" }
}

/// 8/4 (鸿波 "有的数据还是需要人修正的"): 给员工亲手写/改的条目打上
/// `authored_by: employee`。
///
/// # 为什么需要
///
/// 实测鸿波机器 220 条 wiki, **219 条是 LLM 生成的**, frontmatter 里没有任何
/// 字段记录"这条是谁写的" —— 没有 author / reviewed / verified。
///
/// 后果不是"信息缺失": catfish-memory 的 P19 LLM merge 读文件时根本不看来源,
/// 员工在知识体系 TAB 里手工改的内容, 下一次蒸馏会被原样喂给 LLM 重写。
/// 你改掉一条错的断言, 几天后它可能又变回去, 而且不会收到任何提示。
///
/// 有了这个标记, 插件侧 (_is_employee_authored) 就会:
///   · P19 LLM merge 直接跳过, 连送都不送
///   · 写盘时正文原样保留, 新蒸馏内容只进「蒸馏补充」附录等员工确认
///
/// 机器可以提出, 但改不了人已经定下的东西。
pub(crate) fn mark_authored_by_employee(content: &str) -> String {
    let trimmed = content.trim_start();
    if !trimmed.starts_with("---") {
        // 没 frontmatter 就不硬加 —— 读侧 (wiki_read.rs:81) 要求 --- 开头, 我们
        // 在这里补一个 frontmatter 反而会改变文件语义。原样返回。
        return content.to_string();
    }
    let after = &trimmed[3..];
    let Some(end) = after.find("\n---") else {
        return content.to_string();
    };
    let fm = &after[..end];
    if fm.lines().any(|l| l.trim().starts_with("authored_by:")) {
        // 已经有了 —— 覆盖成 employee (员工又改了一次也还是员工的)
        let new_fm: String = fm
            .lines()
            .map(|l| {
                if l.trim().starts_with("authored_by:") {
                    "authored_by: employee".to_string()
                } else {
                    l.to_string()
                }
            })
            .collect::<Vec<_>>()
            .join("\n");
        return format!("---{}\n---{}", new_fm, &after[end + 4..]);
    }
    format!(
        "---{}\nauthored_by: employee\n---{}",
        fm.trim_end(),
        &after[end + 4..]
    )
}

#[cfg(test)]
mod authored_by_tests {
    use super::mark_authored_by_employee;

    /// 8/4: 员工在 UI 改的东西必须能被插件侧认出来, 否则下一次蒸馏 LLM 会把它
    /// 重写掉 —— 实测 220 条 wiki 里 219 条是机器写的, 而数据上完全区分不出来。
    #[test]
    fn adds_marker_to_frontmatter() {
        let out = mark_authored_by_employee("---\ntype: entity\ntitle: X\n---\n\n正文\n");
        assert!(out.contains("authored_by: employee"), "{out}");
        assert!(out.contains("title: X") && out.contains("正文"), "{out}");
        // frontmatter 结构没被破坏 (读侧 split_frontmatter 要求 --- 开头 + \n--- 收尾)
        assert!(out.starts_with("---\n"), "{out}");
        assert!(out.contains("\n---"), "{out}");
    }

    #[test]
    fn overwrites_existing_marker() {
        let out = mark_authored_by_employee(
            "---\ntype: entity\nauthored_by: llm\ntitle: X\n---\n\n正文\n",
        );
        assert!(out.contains("authored_by: employee"));
        assert!(!out.contains("authored_by: llm"));
    }

    #[test]
    fn leaves_content_without_frontmatter_alone() {
        // 没 frontmatter 就不硬加 —— 补一个会改变文件语义 (读侧对 --- 开头很敏感,
        // 8/4 查了半天的"显示成拼音"就是这个 fence 缺失造成的)
        let src = "# 标题\n\n纯正文\n";
        assert_eq!(mark_authored_by_employee(src), src);
    }

    #[test]
    fn leaves_half_frontmatter_alone() {
        let src = "---\ntype: entity\n没有收尾\n";
        assert_eq!(mark_authored_by_employee(src), src);
    }
}

#[cfg(test)]
mod type_vocab_tests {
    use super::*;

    // 8/4: 之前只有蒸馏侧归一化, UI 手工建的不归一 —— 界面上敲「规则」存
    // 「规则」, 蒸馏出来的存「rule」。同一个受控词表两条产线两个结果。

    #[test]
    fn chinese_synonym_is_canonicalized() {
        assert_eq!(canon_subtype(false, "规则"), "rule");
        assert_eq!(canon_subtype(false, "流程"), "process");
        assert_eq!(canon_subtype(true, "资质"), "cert");
        assert_eq!(canon_subtype(true, "公司"), "org");
    }

    #[test]
    fn known_english_is_kept() {
        assert_eq!(canon_subtype(true, "cert"), "cert");
    }

    #[test]
    fn unknown_type_is_allowed_not_rejected() {
        // 词表要能长 —— 表外不拒绝, 只小写归一 + 打日志
        assert_eq!(canon_subtype(true, "Spaceship"), "spaceship");
    }

    #[test]
    fn empty_stays_empty() {
        assert_eq!(canon_subtype(true, "   "), "");
    }

    #[test]
    fn rewrites_only_the_type_line() {
        let src = "---\ntype: concept\ntitle: X\nconcept_type: 规则\n---\n\n正文。\n";
        let out = normalize_type_line(src, false);
        assert!(out.contains("concept_type: rule"), "{out}");
        assert!(out.contains("title: X") && out.contains("正文。"), "{out}");
        assert!(out.ends_with('\n'), "尾部换行被吃了");
    }

    #[test]
    fn already_canonical_is_unchanged() {
        let src = "---\ntype: entity\ntitle: X\nentity_type: cert\n---\n\n正文。\n";
        assert_eq!(normalize_type_line(src, true), src);
    }

    #[test]
    fn entity_and_concept_use_different_vocab() {
        // concept_type 行在 is_entity=true 时不该被动
        let src = "---\nconcept_type: 规则\n---\n\n正文。\n";
        assert_eq!(normalize_type_line(src, true), src);
    }

    #[test]
    fn vocab_json_is_embedded_and_parses() {
        // include_str! 路径写错的话这条会红 —— 打包后 .app 里没有
        // edge/contracts/ 目录, 只能靠编译期嵌入
        let v: serde_json::Value = serde_json::from_str(TYPE_VOCAB_JSON).unwrap();
        assert_eq!(v["aliases"]["规则"], "rule");
        assert!(v["entity_types"].as_array().unwrap().len() >= 5);
    }
}
