//! wiki_read.rs 的测试 (9/29 拆出: 那边加了 Windows 路径分隔符的用例后过了 800 行)。
//! 用 `#[path]` 挂成 wiki_read 的子模块, `use super::*` 照旧能看到私有函数。

/// Windows 上 strip_prefix 出来是反斜杠, 9/29 之前整个 wiki 在 Windows 上都不可见
#[test]
fn build_file_info_windows_path_separator() {
    let tmp = tempfile::TempDir::new().unwrap();
    let home = tmp.path();
    let dir = home.join("wiki").join("entities");
    std::fs::create_dir_all(&dir).unwrap();
    let file = dir.join("合同专用章.md");
    std::fs::write(&file, "---\ntitle: 合同专用章\ntype: entity\n---\n正文足够长足够长足够长足够长足够长足够长足够长足够长足够长").unwrap();
    let info = super::build_file_info(home, &file).expect("wiki/entities 下的文件必须被认出来");
    assert_eq!(info.rel_path, "wiki/entities/合同专用章.md");
    assert_eq!(info.kind, "entity");
    assert!(!info.rel_path.contains('\\'));
}

use super::*;

/// 知识体系 TAB 的可见性契约 —— 用例表跟 tool-bridge 共用同一份文件:
///   edge/contracts/wiki_visibility_cases.json
///
/// 8/3 起因: tool-bridge 新加了 catfish_wiki_list, 在 Python 里复刻了这里的
/// build_file_info + is_tombstone, 好让鲶鱼能查证「TAB 现在有什么」。
///
/// 两份实现讲同一件事就必然会漂, 而漂的后果比没有那个工具更坏 —— 工具说有、
/// TAB 说没有, 鲶鱼会拿着一个不可信的真相源继续推理, 正是 8/3 绕一整轮的
/// 形状。所以判断表只留一份, 两边各自读它断言, 谁漂谁红。
///
/// 这一侧是**基准**: 它就是 TAB 的真实行为。Python 侧对齐它, 不是反过来。
#[test]
fn visibility_contract_matches_shared_cases() {
    let contract_path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../contracts/wiki_visibility_cases.json");
    let raw = fs::read_to_string(&contract_path).unwrap_or_else(|e| {
        panic!(
            "读不到契约表 {contract_path:?}: {e}\n                 它是 Rust / Python 两边共用的唯一判断依据, 不能只删一边。"
        )
    });
    let doc: serde_json::Value = serde_json::from_str(&raw).expect("契约表不是合法 JSON");
    let cases = doc["cases"].as_array().expect("契约表缺 cases 数组");

    // 把用例铺成一棵真实目录树, 走跟线上完全相同的代码路径
    let tmp = tempfile::tempdir().expect("建临时目录失败");
    let home = tmp.path();
    for c in cases {
        let rel = c["path"].as_str().expect("case 缺 path");
        let content = c["content"].as_str().expect("case 缺 content");
        let abs = home.join(rel);
        fs::create_dir_all(abs.parent().unwrap()).expect("建子目录失败");
        fs::write(&abs, content).expect("写用例文件失败");
    }

    for c in cases {
        let name = c["name"].as_str().unwrap_or("<无名用例>");
        let rel = c["path"].as_str().unwrap();
        let expect = &c["expect"];
        let info = build_file_info(home, &home.join(rel));

        if !expect["visible"].as_bool().unwrap_or(false) {
            assert!(
                info.is_none(),
                "{name}\n  这个文件不该出现在 TAB 里, 但 build_file_info 收下了。\n  路径: {rel}"
            );
            continue;
        }
        let info = info.unwrap_or_else(|| {
            panic!("{name}\n  这个文件该出现在 TAB 里, 但 build_file_info 返了 None。\n  路径: {rel}")
        });
        assert_eq!(
            info.title,
            expect["title"].as_str().unwrap(),
            "{name}\n  title 不对"
        );
        assert_eq!(
            info.kind,
            expect["kind"].as_str().unwrap(),
            "{name}\n  kind 不对"
        );
    }
}

fn rr(name: &str) -> RelatedRef { RelatedRef { name: name.to_string(), rel: None, source: Some("body".into()) } }
fn rr_frontmatter(name: &str) -> RelatedRef { RelatedRef { name: name.to_string(), rel: None, source: Some("frontmatter".into()) } }
fn rr_with(name: &str, rel: &str) -> RelatedRef { RelatedRef { name: name.to_string(), rel: Some(rel.to_string()), source: Some("frontmatter".into()) } }

/// P3.5.42.13: 鸿波 entity 实景 body 形态. P3.5.132 #5: 升级返 Vec<RelatedRef>.
#[test]
fn extract_body_wikilinks_finds_inline_links() {
    let body = "## 通信网络安全服务能力风险评估一级\n\n\
                类型: [[信息安全与安防类]]\n\n\
                发证中心: 中国通信企业协会\n\
                关联体系: [[企业资质知识体系]]\n";
    let out = extract_body_wikilinks(body);
    assert_eq!(out, vec![rr("信息安全与安防类"), rr("企业资质知识体系")]);
}

#[test]
fn extract_body_wikilinks_handles_alias() {
    let body = "看 [[信息安全与安防类|安防类]] 跟 [[陈鸿波]]";
    let out = extract_body_wikilinks(body);
    assert_eq!(out, vec![rr("信息安全与安防类"), rr("陈鸿波")]);
}

#[test]
fn extract_body_wikilinks_skips_too_long_or_newline() {
    let long = "a".repeat(120);
    let body = format!("[[{long}]] [[normal]] [[multi\nline]]");
    let out = extract_body_wikilinks(&body);
    assert_eq!(out, vec![rr("normal")]);
}

#[test]
fn extract_body_wikilinks_empty_body() {
    assert_eq!(extract_body_wikilinks(""), Vec::<RelatedRef>::new());
    assert_eq!(
        extract_body_wikilinks("no wikilinks here"),
        Vec::<RelatedRef>::new()
    );
}

#[test]
fn merge_related_dedupes_body_against_frontmatter() {
    let body = "看 [[陈鸿波]] 跟 [[信息安全与安防类]]";
    let out = merge_related_with_body(vec![rr_frontmatter("陈鸿波")], body);
    assert_eq!(out, vec![rr_frontmatter("陈鸿波"), rr("信息安全与安防类")]);
}

#[test]
fn merge_related_preserves_frontmatter_first() {
    let body = "[[A]] [[B]]";
    let out = merge_related_with_body(vec![rr_frontmatter("X"), rr_frontmatter("Y")], body);
    assert_eq!(out, vec![rr_frontmatter("X"), rr_frontmatter("Y"), rr("A"), rr("B")]);
}

/// P3.5.132 #5: dedupe by name 真 — frontmatter 真 typed rel 优先, body wikilink
/// 同名跳, rel 不被 body None 覆盖.
#[test]
fn merge_related_keeps_typed_rel_when_body_has_same_name() {
    let body = "[[陈鸿波]] [[新人]]";
    let out = merge_related_with_body(vec![rr_with("陈鸿波", "同事")], body);
    assert_eq!(out, vec![rr_with("陈鸿波", "同事"), rr("新人")]);
}

// ─── P3.5.132 #5 dual-shape parser tests ──────────────────────────

#[test]
fn parse_related_old_bare_strings() {
    let fm = "related: [陈鸿波, FFCS]";
    let out = parse_related(fm);
    assert_eq!(out, vec![rr_frontmatter("陈鸿波"), rr_frontmatter("FFCS")]);
}

#[test]
fn parse_related_old_wikilink_form() {
    let fm = "related: [\"[[陈鸿波]]\", \"[[FFCS]]\"]";
    let out = parse_related(fm);
    assert_eq!(out, vec![rr_frontmatter("陈鸿波"), rr_frontmatter("FFCS")]);
}

#[test]
fn parse_related_new_inline_map() {
    let fm = "related: [{name: 陈鸿波, rel: 同事}, {name: FFCS, rel: 部门}]";
    let out = parse_related(fm);
    assert_eq!(out, vec![rr_with("陈鸿波", "同事"), rr_with("FFCS", "部门")]);
}

#[test]
fn parse_related_mixed_old_and_new() {
    let fm = "related: [{name: 陈鸿波, rel: 同事}, FFCS, 项目X]";
    let out = parse_related(fm);
    assert_eq!(
        out,
        vec![rr_with("陈鸿波", "同事"), rr_frontmatter("FFCS"), rr_frontmatter("项目X")]
    );
}

#[test]
fn parse_related_inline_map_with_quoted_values() {
    let fm = "related: [{name: \"陈鸿波\", rel: \"同事\"}]";
    let out = parse_related(fm);
    assert_eq!(out, vec![rr_with("陈鸿波", "同事")]);
}

#[test]
fn parse_related_empty_or_missing() {
    assert_eq!(parse_related(""), Vec::<RelatedRef>::new());
    assert_eq!(parse_related("title: x\ntags: []"), Vec::<RelatedRef>::new());
    assert_eq!(parse_related("related: []"), Vec::<RelatedRef>::new());
}

#[test]
fn split_top_level_respects_braces() {
    // 验证 brace-aware splitter — 内部 `,` 不切
    assert_eq!(
        split_top_level("a, {b, c}, d"),
        vec!["a".to_string(), " {b, c}".to_string(), " d".to_string()]
    );
}
