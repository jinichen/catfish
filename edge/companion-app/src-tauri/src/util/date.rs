//! 日期小工具 —— 不引 chrono (太重), std time + 手算。
//!
//! 2026-08-15 从 commands/wiki_write.rs 切出来。它跟 wiki 没有任何关系,
//! 躺在那儿只是历史。
//!
//! 搬过来时补了测试: 原来一条都没有。手写的儒略日换算在 wiki_write.rs 里是
//! 私有函数, 错了只影响 frontmatter 的 created 字段; 放进 util 之后谁都能用,
//! 再没测试就不合适了。期望值是拿 Python `datetime.date` 独立算的, 不是
//! 抄这段代码自己的输出 —— 拿被测代码当答案的检查, 等于给 bug 背书。

/// 简单 today YYYY-MM-DD format — 不引 chrono dep (太重), 用 std time + hand calc.
pub(crate) fn chrono_today() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Unix epoch (1970-01-01) → year/month/day
    let days_since_epoch = (secs / 86400) as i64;
    let (y, m, d) = days_to_ymd(days_since_epoch);
    format!("{y:04}-{m:02}-{d:02}")
}

/// days since epoch → (year, month, day), civil_from_days (Howard Hinnant algorithm).
fn days_to_ymd(z: i64) -> (i64, u32, u32) {
    let z = z + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}

#[cfg(test)]
mod date_tests {
    use super::*;

    // 期望值来自 Python datetime.date, 不是本文件的输出。
    #[test]
    fn epoch_and_simple_days() {
        assert_eq!(days_to_ymd(0), (1970, 1, 1));
        assert_eq!(days_to_ymd(1), (1970, 1, 2));
        assert_eq!(days_to_ymd(730), (1972, 1, 1));
        assert_eq!(days_to_ymd(18993), (2022, 1, 1));
        assert_eq!(days_to_ymd(19236), (2022, 9, 1));
    }

    #[test]
    fn leap_days() {
        // 2000 是闰年 (被 400 整除), 1900 不是 —— 百年规则的两侧都要落在测试里
        assert_eq!(days_to_ymd(11016), (2000, 2, 29));
        assert_eq!(days_to_ymd(11017), (2000, 3, 1));
        assert_eq!(days_to_ymd(19782), (2024, 2, 29));
    }

    #[test]
    fn before_epoch() {
        // secs/86400 在 1970 前是负数; era 那行的 else 分支只有这里会走到
        assert_eq!(days_to_ymd(-1), (1969, 12, 31));
    }

    #[test]
    fn today_is_zero_padded() {
        let s = chrono_today();
        assert_eq!(s.len(), 10, "{s}");
        assert_eq!(s.as_bytes()[4], b'-');
        assert_eq!(s.as_bytes()[7], b'-');
        assert!(s.starts_with("20"), "{s}");
    }
}
