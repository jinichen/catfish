//! 9/24: 条目正文里是否记着"会变的进度" —— 工作台据此列「进度待核对」任务。
//!
//! 从 wiki_read.rs 拆出 (那个文件到 800 行线了)。词表跟前端
//! tabs/Wiki/wikiRelationshipTasks.ts 的 STATUS_MARKERS 是同一份, 改一边要改另一边。

/// 正文里出现这些词, 说明这条记着会变的进度 (见 wiki_read::WikiFileInfo::tracks_status)。
const STATUS_MARKERS: &[&str] = &[
    "当前状态", "状态（", "状态(", "进度（", "进度(", "**进度", "办理中", "等出证", "待出证", "等审核", "待审核",
];

pub(crate) fn body_tracks_status(body: &str) -> bool {
    STATUS_MARKERS.iter().any(|marker| body.contains(marker))
}

#[cfg(test)]
mod status_marker_tests {
    use super::body_tracks_status;

    #[test]
    fn detects_progress_paragraphs_only() {
        assert!(body_tracks_status("## 当前状态（截至 2026-09-22）\n现场审核进行中"));
        assert!(body_tracks_status("## 状态（2026-09-11 员工确认）\nISO20000 未出证"));
        assert!(body_tracks_status("**进度（2026-09-07）**：擎标中标"));
        // 组织架构里的"经营推进中心"不是进度 (所以词表里不放"推进中": 它是"推进中心"的子串)
        assert!(!body_tracks_status("下辖产品管理中心、经营推进中心、市场规划中心"));
        assert!(!body_tracks_status("证书编号 X，有效期至 2029-01-27。"));
    }
}
