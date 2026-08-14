//! 向量缓存的身份记账 —— 中央换了向量模型, 员工机的缓存要能自己发现 (8/14).
//!
//! # 在防什么
//!
//! 两套缓存 (`advisor_embed_cache` / `wiki_embed`) 里存的是 f32 BLOB。它们只有
//! 在**跟当前模型产出的向量来自同一个向量空间**时才有意义。而"当前是哪个模型"
//! 由中央决定 (roles.yaml 的 embedding 角色 → 控制台那条"向量"类型的模型),
//! 员工机完全被动。
//!
//! 改这件事之前的状态:
//!
//!   · advisor 有个 `_meta` 表, 但**只存一个 embed_dim 数字**, 而且那个数字来自
//!     员工机 yaml 里写死的 1024 —— 不是实际产出的维度。
//!   · wiki **一张 _meta 都没有**, 全靠 `vector_from_blob` 的长度校验兜着:
//!     维度变了 → 逐条读不出来 → 当 cache miss 重算。同维度换模型则完全无感。
//!
//! 同维度换模型是最坏的那种: 新旧向量长度一样、读得出来、算得出 cosine,
//! 只是**结果全是垃圾**, 没有任何一处会报错, 表现只是"语义搜索越来越不准"。
//!
//! # 判据来自观测, 不来自配置
//!
//! `embedding::observed_identity()` 是**实际产出**的 dim + 模型标识
//! (remote 取响应的 model 字段, local 取模型文件路径)。配置不参与。
//!
//! # 时机
//!
//! 观测值要等第一次成功 embed 之后才有。所以对账不能放在"打开数据库"那一刻,
//! 得放在"已经拿到一个新向量、准备写库"之前。调用方按这个顺序:
//!
//!   1. ensure_table(conn)
//!   2. 先 embed 一条 (通常就是查询本身)
//!   3. reconcile(conn, 清表闭包)   ← 不符就在这里清掉
//!   4. 再补索引 / 写新向量 / 读缓存
//!
//! # 一个有意的保守选择
//!
//! 老库里已有向量、`_meta` 里却没有记录时 (wiki 全是这种), **不清缓存**,
//! 只把当前观测写进去。因为"没有记录"不是"记录不同" —— 没有证据说明那些
//! 向量来自别的模型, 而清一次是几百个请求。从这一次起有账可查, 往后就准了。
//!
//! 维度对不上的那部分仍然会被 `vector_from_blob` 逐条拒读, 所以不会算出错的分。

use std::sync::RwLock;

use rusqlite::Connection;

// ─── 观测到的向量身份 ──────────────────────────────────────
//
// 维度和"是哪个模型"都是**中央那个模型的属性**, 却一直写在员工机的
// ~/.catfish/embedding.yaml 里 (embed_dim: 1024)。后果按严重程度排:
//
//   1. RemoteProvider 原来拿 yaml 的 dim 去**否决响应** —— 中央换成 768 维的
//      模型, 员工端每次都把拿回来的向量丢掉, 只留一行 warn。不是"用错维度",
//      是**整个向量功能静默失效**。
//   2. 缓存失效只比维度。同样 1024 维的两个不同模型换过去, 一点都察觉不到:
//      新旧向量长度相同、来自不同的向量空间, cosine 全是垃圾。
//   3. 员工得手动跟着中央改 yaml, 而他根本不知道中央换了。
//
// 现在维度**量出来**: remote 用响应里向量的实际长度, local 用 ONNX 输出
// tensor 的 shape。模型身份也一并记下来 (remote 从响应的 model 字段)。
//
// 这份观测在第一次成功 embed 之后才有值。缓存的失效判断因此挪到"拿到向量
// 之后、写库之前" —— 见 services/embed_cache_meta.rs。

/// 一次成功 embed 观测到的"这批向量是谁产的".
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EmbedIdentity {
    /// 向量维度 —— 实际数出来的, 不是配置里写的.
    pub dim: usize,
    /// 模型标识. remote 取响应里的 model 字段, local 取模型文件路径.
    /// 拿不到就是 None —— 那就**不参与**缓存失效判断 (宁可不重建, 也不要
    /// 因为上游偶尔不返 model 就把几百条向量全部重算)。
    pub model: Option<String>,
}

static OBSERVED: RwLock<Option<EmbedIdentity>> = RwLock::new(None);

/// 记一次观测. 跟上一次不同就 warn —— 那意味着中央换了模型/维度.
pub fn record_identity(id: EmbedIdentity) {
    let mut w = match OBSERVED.write() {
        Ok(w) => w,
        Err(e) => {
            log::warn!("[embedding] 观测身份锁中毒: {e}");
            return;
        }
    };
    match w.as_ref() {
        Some(prev) if *prev == id => {}
        Some(prev) => {
            log::warn!(
                "[embedding] 向量身份变了: dim {}→{}, model {:?}→{:?} —— \
                 缓存会在下一次写入前重建",
                prev.dim, id.dim, prev.model, id.model
            );
            *w = Some(id);
        }
        None => {
            log::info!(
                "[embedding] 观测到向量身份: dim={} model={:?} (量出来的, 不是配置)",
                id.dim, id.model
            );
            *w = Some(id);
        }
    }
}

/// 当前观测到的身份. **一次成功 embed 都还没发生过时返 None** ——
/// 那时无从判断缓存新旧, 调用方应当保持现状而不是乱清。
pub fn observed_identity() -> Option<EmbedIdentity> {
    OBSERVED.read().ok().and_then(|g| g.clone())
}

const KEY_DIM: &str = "embed_dim";
const KEY_MODEL: &str = "embed_model";

/// 建 `_meta` 表. 幂等.
pub fn ensure_table(conn: &Connection) -> Result<(), String> {
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )",
        [],
    )
    .map(|_| ())
    .map_err(|e| format!("CREATE _meta 失败: {e}"))
}

fn get(conn: &Connection, key: &str) -> Option<String> {
    conn.query_row("SELECT value FROM _meta WHERE key = ?1", [key], |r| {
        r.get::<_, String>(0)
    })
    .ok()
}

fn put(conn: &Connection, key: &str, value: &str) -> Result<(), String> {
    conn.execute(
        "INSERT INTO _meta (key, value) VALUES (?1, ?2)
         ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [key, value],
    )
    .map(|_| ())
    .map_err(|e| format!("写 _meta 失败: {e}"))
}

/// `_meta` 里记着的身份. 没记过返 None.
pub fn stored(conn: &Connection) -> Option<EmbedIdentity> {
    let dim = get(conn, KEY_DIM)?.parse::<usize>().ok()?;
    Some(EmbedIdentity {
        dim,
        model: get(conn, KEY_MODEL),
    })
}

/// 读缓存时按哪个维度解 BLOB.
///
/// 优先用**观测到的** (这次真跑出来的), 其次用 `_meta` 里的 (上次跑出来的)。
/// 两个都没有 = 这个进程还没成功 embed 过、库也是新的 → 返 None,
/// 调用方应当把缓存全部当 miss (没有别的安全选择: 拿一个猜的维度去解 BLOB,
/// 解出来的是能算出分数的垃圾)。
pub fn expected_dim(conn: &Connection) -> Option<usize> {
    observed_identity()
        .map(|id| id.dim)
        .or_else(|| stored(conn).map(|id| id.dim))
}

/// 拿这次观测到的身份跟库里的账对一下.
///
/// 不一致 → 调 `clear` 把缓存表清掉/重建, 然后把新身份写进 `_meta`。
/// 还没有观测值 (一次都没成功 embed 过) → 什么都不做。
///
/// 返回 true 表示"清过了"。
pub fn reconcile<F>(conn: &Connection, clear: F) -> Result<bool, String>
where
    F: FnOnce(&Connection) -> Result<(), String>,
{
    reconcile_with(conn, observed_identity(), clear)
}

/// `reconcile` 的**纯**版本: 观测值当参数传, 不读全局.
///
/// 拆出来是为了能测 —— 全局 OBSERVED 是进程级的, 测试并发跑会互相踩,
/// 而这段判断 (什么时候该清、什么时候宁可不清) 正是这次最容易写错的地方。
pub fn reconcile_with<F>(
    conn: &Connection,
    now: Option<EmbedIdentity>,
    clear: F,
) -> Result<bool, String>
where
    F: FnOnce(&Connection) -> Result<(), String>,
{
    let now = match now {
        Some(id) => id,
        // 一次都还没成功 embed —— 无从判断, 保持现状。乱清比不清坏得多。
        None => return Ok(false),
    };
    let before = stored(conn);

    let must_clear = match &before {
        None => false, // 老库没记过账: 见文件头"一个有意的保守选择"
        Some(prev) => {
            // 维度不同 → 一定要清。
            // 模型不同 → 也要清, 但**两边都拿得到名字**才算数:
            //   上游偶尔不返 model 字段就把几百条向量全部重算, 代价远大于收益。
            prev.dim != now.dim
                || match (&prev.model, &now.model) {
                    (Some(a), Some(b)) => a != b,
                    _ => false,
                }
        }
    };

    if must_clear {
        let prev = before.as_ref().expect("must_clear 只在有旧账时为 true");
        log::warn!(
            "[embed_cache] 向量来源变了 (dim {}→{}, model {:?}→{:?}) —— 清缓存重建。\
             这是**应该发生**的: 不同模型产的向量不在同一个空间, 混着算出来的相似度\
             没有意义, 而且不会报错。",
            prev.dim,
            now.dim,
            prev.model,
            now.model
        );
        clear(conn)?;
    }

    put(conn, KEY_DIM, &now.dim.to_string())?;
    if let Some(m) = &now.model {
        put(conn, KEY_MODEL, m)?;
    }
    Ok(must_clear)
}


#[cfg(test)]
mod tests {
    //! 这段判断"什么时候该清缓存"是这次改动里最容易写错的地方:
    //! 清早了 = 几百个请求白跑; 清漏了 = 相似度全是垃圾而且不报错。
    //!
    //! 全部走 reconcile_with (纯版本), 不碰进程级的 OBSERVED —— 那个东西
    //! 并发跑会互相踩, 而这些判断本身跟全局状态无关。

    use super::*;

    fn db() -> Connection {
        let c = Connection::open_in_memory().unwrap();
        ensure_table(&c).unwrap();
        c
    }

    fn id(dim: usize, model: Option<&str>) -> EmbedIdentity {
        EmbedIdentity { dim, model: model.map(str::to_string) }
    }

    /// clear 闭包被调了没
    fn run(conn: &Connection, now: Option<EmbedIdentity>) -> (bool, bool) {
        let called = std::cell::Cell::new(false);
        let cleared = reconcile_with(conn, now, |_| {
            called.set(true);
            Ok(())
        })
        .unwrap();
        (cleared, called.get())
    }

    #[test]
    fn 还没观测到任何东西时_什么都不做() {
        // ★★ 第一次 embed 之前无从判断。乱清比不清坏得多 —— 清一次是几百个请求。
        let c = db();
        put(&c, KEY_DIM, "1024").unwrap();
        assert_eq!(run(&c, None), (false, false));
        assert_eq!(stored(&c).unwrap().dim, 1024, "不该动库里的账");
    }

    #[test]
    fn 老库没记过账时_只补账不清缓存() {
        // ★★★ wiki 那套全是这种 (它原来一张 _meta 都没有)。
        // "没有记录"不是"记录不同" —— 没有证据说明那些向量来自别的模型。
        let c = db();
        assert_eq!(run(&c, Some(id(1024, Some("bge-m3")))), (false, false));
        assert_eq!(stored(&c), Some(id(1024, Some("bge-m3"))), "账要补上");
    }

    #[test]
    fn 维度变了_必须清() {
        // ★★★ 768 维的向量跟 1024 维的没法比, 而且长度不同还会被逐条拒读,
        // 留着只是占空间 + 每次都 miss。
        let c = db();
        run(&c, Some(id(1024, Some("bge-m3"))));
        assert_eq!(run(&c, Some(id(768, Some("bge-m3")))), (true, true));
        assert_eq!(stored(&c).unwrap().dim, 768);
    }

    #[test]
    fn 同维度换模型_也必须清() {
        // ★★★ 这条是这次改动的**核心**。
        // 老代码只比维度, 这种情况一点都察觉不到: 新旧向量长度一样、读得出来、
        // 算得出 cosine, 只是结果全是垃圾, 没有一处报错。
        let c = db();
        run(&c, Some(id(1024, Some("bge-m3"))));
        assert_eq!(run(&c, Some(id(1024, Some("customer-x-embed")))), (true, true));
        assert_eq!(stored(&c).unwrap().model.as_deref(), Some("customer-x-embed"));
    }

    #[test]
    fn 模型名一样_不清() {
        let c = db();
        run(&c, Some(id(1024, Some("bge-m3"))));
        assert_eq!(run(&c, Some(id(1024, Some("bge-m3")))), (false, false));
    }

    #[test]
    fn 上游这次没返模型名_不清() {
        // ★★ 有意的保守: 上游偶尔不返 model 字段就把几百条向量全部重算,
        // 代价远大于收益。缺证据 ≠ 有反证。
        let c = db();
        run(&c, Some(id(1024, Some("bge-m3"))));
        assert_eq!(run(&c, Some(id(1024, None))), (false, false));
        assert_eq!(
            stored(&c).unwrap().model.as_deref(),
            Some("bge-m3"),
            "也不该把已知的模型名擦掉"
        );
    }

    #[test]
    fn 旧账没有模型名_新的有_不清() {
        // 从"只记了维度"的老库升上来: 补记模型名, 但不能凭这个就判定换过模型。
        let c = db();
        put(&c, KEY_DIM, "1024").unwrap();
        assert_eq!(run(&c, Some(id(1024, Some("bge-m3")))), (false, false));
        assert_eq!(stored(&c).unwrap().model.as_deref(), Some("bge-m3"));
    }

    #[test]
    fn 维度不同就清_哪怕模型名都拿不到() {
        // 维度是硬证据, 不需要模型名佐证。
        let c = db();
        run(&c, Some(id(1024, None)));
        assert_eq!(run(&c, Some(id(768, None))), (true, true));
    }

    #[test]
    fn expected_dim_没账没观测时返_none() {
        // ★★ 调用方据此把缓存全当 miss。拿一个猜的维度去解 BLOB,
        // 解出来的是**能算出分数的垃圾** —— 比 miss 坏得多。
        let c = db();
        assert_eq!(expected_dim(&c), None);
        put(&c, KEY_DIM, "1024").unwrap();
        assert_eq!(expected_dim(&c), Some(1024), "有账就用账上的");
    }

    #[test]
    fn clear_失败要往上抛_不能吞() {
        // 清失败却把新账写进去 = 库里记着新模型、数据还是旧模型, 最坏的一种。
        let c = db();
        run(&c, Some(id(1024, Some("a"))));
        let r = reconcile_with(&c, Some(id(768, Some("b"))), |_| Err("清不动".into()));
        assert!(r.is_err(), "clear 报错必须往上抛");
        assert_eq!(stored(&c).unwrap().dim, 1024, "账不能改 —— 数据还没清掉");
    }
}
