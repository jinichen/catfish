//! Wiki 本体关系目标校验。

use std::path::Path;

use super::wiki_read::{build_file_info, collect_all_wiki_md};

/// 判断关系目标是否是唯一且 active 的本体节点。
pub fn ontology_target_is_active(home: &Path, name: &str) -> bool {
    let needle = name.trim().to_lowercase();
    if needle.is_empty() {
        return false;
    }
    let matches = collect_all_wiki_md(home)
        .into_iter()
        .filter_map(|path| build_file_info(home, &path))
        .filter(|info| matches!(info.kind.as_str(), "entity" | "concept"))
        .filter(|info| {
            let status = info.ontology_status.as_deref().unwrap_or("active").to_lowercase();
            status == "active"
                && (info.title.to_lowercase() == needle
                    || info.slug.to_lowercase() == needle
                    || info
                        .aliases
                        .iter()
                        .any(|alias| alias.trim().to_lowercase() == needle))
        })
        .count();
    matches == 1
}
