/** AuditPage 数据对账 — 抽自 AuditPage.tsx (5/20 拆分).
 *
 * 各维度加和跟总请求对不上时报警, 对得上时静默。这是个诊断模块: 正常情况下
 * 这一页永远看不到它。
 *
 * ## 8/1 修的那个假警报
 *
 * 后端给 by_model / by_department 的是 `LIMIT 20`, by_user 是 `LIMIT 50`。
 * 而这里拿**截断后的列表**去跟**没截断的** request_count 比 —— 所以公司
 * 一旦有超过 50 个活跃员工, 这张告警就永久挂在页面上, 而且给出的
 * "典型原因"（老数据缺 OIDC claim）是错的。
 *
 * 更糟的是它挂上之后就不再是信号了: 真出现口径不一致的时候, 它跟昨天、
 * 前天长得一模一样, 没人会多看一眼。**一个永远亮着的告警灯等于没有告警灯**
 * —— 这比它一开始就不存在更糟。
 *
 * ## 截断了就完全不查吗
 *
 * 不。截断只能解释"加和**小于**总数", 解释不了加和**大于**总数 ——
 * 那是真的重复计数, 而且截断只会让它更难出现, 所以一旦出现更值得看。
 * 所以截断的维度仍然查一个方向。
 *
 * 另外"恰好等于 LIMIT"是分不出"公司正好这么多"还是"被截了"的 ——
 * 这里按被截了算 (保守), 代价是恰好 20 个模型的公司少一层对账。
 * 这个取舍在界面上写出来了, 不然它就成了另一个静默行为。
 */

import { AUDIT_TOP_N, type GlobalAudit } from "../../lib/me";

interface Dim {
  label: string;
  sum: number;
  /** 行数达到了后端的 LIMIT —— 加和小于总数是设计如此, 不是异常。 */
  truncated: boolean;
}

function DataReconciliation({ audit }: { audit: GlobalAudit }) {
  const dims: Dim[] = [
    {
      label: "按模型",
      sum: audit.by_model.reduce((s, m) => s + m.count, 0),
      truncated: audit.by_model.length >= AUDIT_TOP_N.model,
    },
    {
      label: "按部门",
      sum: audit.by_department.reduce((s, d) => s + d.count, 0),
      truncated: audit.by_department.length >= AUDIT_TOP_N.department,
    },
    {
      label: "按员工",
      sum: audit.by_user.reduce((s, u) => s + u.count, 0),
      truncated: audit.by_user.length >= AUDIT_TOP_N.user,
    },
  ];

  const bad = dims.filter((d) =>
    // 没截断的: 加和必须**等于**总数。
    // 截断了的: 只有加和**超过**总数才是问题 —— 少了是截断的正常后果。
    d.truncated ? d.sum > audit.request_count : d.sum !== audit.request_count,
  );
  if (bad.length === 0) return null;

  // ⚠ 少算和多算是两回事, 原因完全不同, 不能共用一句解释。
  //
  //   少算 (加和 < 总数): 请求进了 (未分组) 桶 —— 老数据缺 OIDC claim。
  //   多算 (加和 > 总数): 同一条请求被数了两次 —— 分组 SQL 的 JOIN 或
  //                       GROUP BY 出问题, 跟 claim 一点关系没有。
  //
  // 第一版这里对两种都写"典型原因: 缺 claim 进了未分组桶", 管理员照着
  // 那个方向查多算的情况会一无所获。而且差值写死成 `总数 - 加和`,
  // 多算时会显示成"差 -1,234"。
  const under = bad.filter((d) => d.sum < audit.request_count);
  const over = bad.filter((d) => d.sum > audit.request_count);

  // 有几维因为截断只查了一半。写出来是因为下面那条原因只解释得了没截断的
  // 那几维 —— 不说清楚的话, 管理员会拿错误的原因去查。
  //
  // 这一段**只在已经报警时**出现。全部三维都截断、而且没查出问题时整块
  // 不显示: "前 N 名"在每张表的表尾都已经写了 (truncatedNote), 再来一条
  // 常驻提示就是重复, 而常驻的提示很快会被当成背景。
  const partial = dims.filter((d) => d.truncated);

  return (
    <div
      style={{
        border: "1px solid var(--status-warn)",
        borderRadius: "var(--radius-md)",
        padding: "6px 10px",
        fontSize: 12,
        lineHeight: 1.5,
      }}
    >
      <strong>⚠️ 数据对账不平</strong>：总请求{" "}
      {audit.request_count.toLocaleString()}，但{" "}
      {bad
        .map((d) => {
          const diff = d.sum - audit.request_count;
          return `${d.label}加和 = ${d.sum.toLocaleString()}（${
            diff > 0 ? "多" : "少"
          } ${Math.abs(diff).toLocaleString()}）`;
        })
        .join("，")}
      。
      <div style={{ color: "var(--text-muted)", fontSize: 11, marginTop: 2 }}>
        {under.length > 0 && (
          <div>
            少的那几项（{under.map((d) => d.label).join("、")}）通常是：老的审计
            数据没拿到 user_email / department（dev_token，或者 OIDC 少了对应的
            claim），这些请求进了「(未分组)」桶。
          </div>
        )}
        {over.length > 0 && (
          <div>
            多的那几项（{over.map((d) => d.label).join("、")}）不是漏归组造成的
            —— 加和比总数还大只能是同一条请求被数了两次，查那条分组 SQL 的
            GROUP BY 和 JOIN。
          </div>
        )}
        {partial.length > 0 && (
          <div>
            {partial.map((d) => d.label).join("、")}
            已达后端返回上限，这几项只检查了"有没有多算"，没检查"有没有漏"。
          </div>
        )}
      </div>
    </div>
  );
}
export default DataReconciliation;
