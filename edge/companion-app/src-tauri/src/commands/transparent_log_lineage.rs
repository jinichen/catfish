//! 数据外发记录 × 换机迁移 (docs/DEVICE-MIGRATION-DESIGN.md §6.2)。
//!
//! 迁移包原样带走 `outbound_log.db`, 新机接着这条链往下写; 导出、导入各在链上记一行
//! (`dest_kind = local`, 不出本机, 内容只有设备号和链头), 审计时看得到「哪天从哪台搬来的」。
//!
//! 中央锚点按人记、现在带设备号。新机校验时只对:
//!   - 不带设备号的老锚点 (升级前报的)
//!   - 自己报的
//!   - 迁来的那台、**迁移点之前**报的 (`device_lineage` 里记着迁移点)
//!
//! 旧机导出后如果还在用, 它之后报的锚点属于另一条链, 不算这条链被动过 —— 单独计数说出来。

use std::collections::BTreeMap;

use rusqlite::Connection;
use serde_json::json;

use super::transparent_log_store::{insert, meta_get, meta_set, Anchor, ChainRow};

const LINEAGE_KEY: &str = "device_lineage";

/// 设备号 → 这台的锚点只认到第几条 (None = 不设上限, 即当前这台)。
pub(crate) type Lineage = BTreeMap<String, Option<i64>>;

pub(crate) fn lineage(conn: &Connection) -> Lineage {
    meta_get(conn, LINEAGE_KEY)
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn set_lineage(conn: &Connection, l: &Lineage) -> Result<(), String> {
    meta_set(conn, LINEAGE_KEY, &serde_json::to_string(l).map_err(|e| e.to_string())?)
}

pub(crate) enum Scope {
    Check,
    OtherDevice,
}

/// 这个锚点该不该拿来对本机这条链。`me = None` (还没生成设备号 / 测试) 时全部照对 (老行为)。
pub(crate) fn anchor_scope(a: &Anchor, me: Option<&str>, lineage: &Lineage) -> Scope {
    let Some(dev) = a.device_id.as_deref() else { return Scope::Check };
    let Some(me) = me else { return Scope::Check };
    if dev == me {
        return Scope::Check;
    }
    match lineage.get(dev) {
        Some(None) => Scope::Check,
        Some(Some(limit)) if a.last_id <= *limit => Scope::Check,
        _ => Scope::OtherDevice,
    }
}

/// (last_id, last_hash) —— 链头, 没有进链的记录时 (0, GENESIS)。
pub(crate) fn chain_tip(conn: &Connection) -> (i64, String) {
    let id: i64 = conn
        .query_row("SELECT COALESCE(MAX(id), 0) FROM outbound_log WHERE row_hash IS NOT NULL", [], |r| r.get(0))
        .unwrap_or(0);
    let hash = conn
        .query_row("SELECT row_hash FROM outbound_log WHERE id = ?1", [id], |r| r.get::<_, String>(0))
        .unwrap_or_else(|_| meta_get(conn, "gc_last_hash").unwrap_or_else(|| super::transparent_log_store::GENESIS.into()));
    (id, hash)
}

fn local_row(purpose: &str, body: &[u8]) -> ChainRow {
    let now = chrono::Utc::now().to_rfc3339();
    ChainRow {
        ts_request: now.clone(),
        ts_response: Some(now),
        method: "LOCAL".into(),
        url: "local://device-migration".into(),
        request_bytes: body.len() as i64,
        response_bytes: 0,
        status: None,
        error: None,
        origin: Some("companion".into()),
        dest_kind: Some("local".into()),
        dest_host: Some("本机".into()),
        purpose: Some(purpose.into()),
        body_sha256: String::new(),
    }
}

/// 旧机: 导出前在链上记一行「导出迁移包」(导出的快照包含这一行)。
pub(crate) fn record_export(conn: &mut Connection, device_id: &str) -> Result<(), String> {
    let body = json!({ "event": "export", "device_id": device_id }).to_string();
    insert(conn, local_row("导出迁移包", body.as_bytes()), Some(body.as_bytes()), None, Some("migration".into()))?;
    Ok(())
}

/// 新机: 导入后接着旧链记一行「设备迁移」, 并登记血缘: 旧机的锚点只认到导出时的链头。
/// `inherited` = 包里那份库原来的血缘 (旧机自己也是从更早的机器迁来的时候, 一路继承下去)。
pub(crate) fn record_import(conn: &mut Connection, from_device: &str, to_device: &str, exported_tip: (i64, &str)) -> Result<(), String> {
    let mut l = lineage(conn);
    // 旧机原来是「不设上限」的当前机 → 现在封顶到导出点; 更早的祖先保持原样
    l.insert(from_device.to_string(), Some(exported_tip.0));
    l.insert(to_device.to_string(), None);
    set_lineage(conn, &l)?;
    let body = json!({
        "event": "import",
        "from_device": from_device,
        "to_device": to_device,
        "exported_last_id": exported_tip.0,
        "exported_last_hash": exported_tip.1,
    })
    .to_string();
    insert(conn, local_row("设备迁移", body.as_bytes()), Some(body.as_bytes()), None, Some("migration".into()))?;
    // 新机还没报过锚点: 清掉旧机的「上次报的时间」, 让新机尽快报第一个
    conn.execute("DELETE FROM outbound_meta WHERE key = 'anchor_last_sent'", []).map_err(|e| e.to_string())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn anchor(id: i64, dev: Option<&str>) -> Anchor {
        Anchor { last_id: id, last_hash: "x".into(), received_at: "2026-10-31T00:00:00+08:00".into(), device_id: dev.map(Into::into) }
    }

    #[test]
    fn 只对自己和迁移点之前的旧机锚点() {
        let mut l = Lineage::new();
        l.insert("old".into(), Some(100));
        l.insert("new".into(), None);
        let me = Some("new");
        assert!(matches!(anchor_scope(&anchor(5, None), me, &l), Scope::Check)); // 老锚点
        assert!(matches!(anchor_scope(&anchor(150, Some("new")), me, &l), Scope::Check));
        assert!(matches!(anchor_scope(&anchor(100, Some("old")), me, &l), Scope::Check));
        assert!(matches!(anchor_scope(&anchor(101, Some("old")), me, &l), Scope::OtherDevice)); // 旧机导出后又写的
        assert!(matches!(anchor_scope(&anchor(3, Some("laptop2")), me, &l), Scope::OtherDevice));
        assert!(matches!(anchor_scope(&anchor(3, Some("laptop2")), None, &l), Scope::Check));
    }
}
