/** macOS 缺「完全磁盘访问权限」的提示。
 *
 * 9/21 从 EmailTab.tsx 搬出来 —— 那个文件在 799 行, 改滚动就会把它推过仓库
 * 的 800 行红线。挑这一块搬是因为它是**纯静态展示**: 一个 boolean 进来,
 * 一段固定文案出去, 跟邮件列表的状态机没有任何耦合, 搬动风险最低。
 *
 * ── 下面两条是原地的经验, 跟着一起搬, 别再踩一遍 ──
 *
 * **独占一行, 不能塞进 header。** 8/8 第一版做成 header 概要后面的一个
 * "⚠ 少账号?" chip, 当场把标题挤成竖排的"邮"↵"件"。这个坑文件里就记着
 * (P3.5.204.f): 左栏 340px 硬编码, header 那一行塞 邮件 + 概要 + 新建 +
 * 收信中… 已经是临界的, 上次为了腾 28px 才把 📧 图标删掉 —— 我转手加了
 * 5 个字回去。
 *
 * **独占一行还有个好处: 说得下人话。** chip 只能塞四五个字, 员工看不懂
 * 要做什么。这里能把"去哪点、点完要重启"完整写出来。
 */
export default function FullDiskAccessHint() {
  return (
    <div
      style={{
        fontSize: 11,
        lineHeight: 1.5,
        color: "var(--catfish-hint-amber-text)",
        background: "var(--catfish-hint-amber-bg)",
        border: "1px solid var(--catfish-hint-amber-border)",
        borderRadius: "var(--radius-sm)",
        padding: "6px 8px",
        marginBottom: 6,
      }}
    >
      ⚠ 有邮箱账号读不到 —— 缺「完全磁盘访问权限」。
      <br />
      系统设置 → 隐私与安全性 → 完全磁盘访问权限 → 打开「鲶鱼 Companion」→ 重启。
      <br />
      <span style={{ opacity: 0.8 }}>
        注: 每次重装 Companion 都要重授一次 (app 还没做代码签名, 系统当成新程序)。
      </span>
    </div>
  );
}
