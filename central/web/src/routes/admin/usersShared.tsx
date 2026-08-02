/** /admin/users 列表页和表单页都要的两小块 (8/1 拆文件时抽出).
 *
 * 单开一个文件是因为 UsersPage 和 UserForms 互相 import 会成环
 * (列表页要渲染编辑弹窗, 而弹窗的标题栏又要 RoleBadge)。
 */

import type { CSSProperties } from "react";

import { Badge } from "../../components/DataTable";
import type { Role } from "../../lib/admin";

export function roleLabel(role: Role): string {
  return { sysadmin: "系统超级管理员", admin: "管理员", manager: "部门经理", employee: "普通员工" }[role];
}

/** 角色徽章。
 *
 * 8/1: 原来是这个页面私有的一份实现 (硬编码 #7c3aed / var(--accent) /
 * var(--status-warn) / var(--text-muted), 全填充白字) —— 也就是
 * DataTable 文件头说的"6 份徽章"里的一份。换成共享 Badge。
 *
 * ## 四个角色, 四档, 但只用两个色相
 *
 *   sysadmin   accent 实心    全公司最高权限, 五个页面 require 它,
 *                             而"谁是 sysadmin"基本只能在这一页看出来
 *   admin      accent 描边    同族, 低一档
 *   manager    neutral 描边   能进后台 (navConfig 有两条 require MANAGER),
 *                             跟 employee 必须分得开
 *   employee   不给徽章       一行里少一个色块; 它是默认值, 不需要标记
 *
 * **不给 manager 用橙色**: 右边"状态"列的「已锁」「待改密」就是橙的,
 * 同一行两个橙徽章表达完全不同的东西, 比不分色更糟。靠填充/描边分档,
 * 色相只留两个, 一列扫下来仍然能一眼找到 sysadmin。
 */
export function RoleBadge({ role }: { role: Role }) {
  if (role === "employee")
    return <span style={{ color: "var(--text-muted)", fontSize: 11 }}>{roleLabel(role)}</span>;
  const tone =
    role === "sysadmin" ? "accent" : role === "admin" ? "accentSoft" : "neutral";
  return (
    <Badge
      tone={tone}
      title={
        role === "sysadmin"
          ? "最高权限, 能改所有配置"
          : role === "admin"
            ? "能管用户和部门, 不能改供应商/模型"
            : "能进后台看本部门的用量"
      }
    >
      {roleLabel(role)}
    </Badge>
  );
}

export const btnSmall: CSSProperties = {
  padding: "2px 8px",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  background: "var(--bg-secondary)",
  cursor: "pointer",
  fontSize: 12,
};

/** 生成一个临时密码。
 *
 * ## 为什么不是 Math.random()
 *
 * 8/1 合并两份实现时发现的: 创建页和重置密码各写了一份, **字符集还不一样**
 * (一份带 !@#$%, 一份不带), 而两份都用 `Math.random()`。
 *
 * `Math.random()` 不是密码学安全的 —— V8 用的是 xorshift128+, 观察到几个
 * 输出之后可以推出内部状态、进而算出后续的值。对"洗牌"这类用途无所谓,
 * 对**一个真的会被用来登录的密码**不行。`crypto.getRandomValues` 是同样
 * 一行的事, 没有理由不用。
 *
 * ## 为什么这样取模
 *
 * `byte % chars.length` 会有取模偏差 (字符集 55 个, 256 除不尽, 前 36 个
 * 字符出现概率略高)。这里改成**丢弃落在尾巴上的字节**重取, 分布是均匀的。
 * 拒绝率 36/256 ≈ 14%, 生成 14 位期望循环约 16 次。
 * 偏差在这个量级下不足以被利用, 但既然只多两行, 没必要留一个要解释的弱点。
 *
 * 字符集去掉了形近字 (0 1 i l I o O) —— 这个密码要靠人念或者手抄给同事。
 * 14 位 × 55 个字符 ≈ 81 bit 熵, 比换之前的 12 位 × 59 (≈71 bit) 还高,
 * 所以去掉符号不是降级。后端也只校验长度 ≥ 8, 没有复杂度规则。
 */
export function genTempPassword(length = 14): string {
  const chars = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const max = Math.floor(256 / chars.length) * chars.length; // 均匀取模的上界
  const out: string[] = [];
  const buf = new Uint8Array(1);
  while (out.length < length) {
    crypto.getRandomValues(buf);
    if (buf[0] >= max) continue; // 落在尾巴上就重取
    out.push(chars[buf[0] % chars.length]);
  }
  return out.join("");
}

/** 把文字放进剪贴板。返回是否成功。
 *
 * ⚠ `navigator.clipboard` **只在安全上下文里存在** (https / localhost)。
 * 而这个产品是私有化部署, 客户内网经常是 `http://10.x.x.x` 直连 ——
 * 那里 `navigator.clipboard` 是 undefined, 直接调用会抛 TypeError。
 * 所以退回老的 execCommand, 并且失败时如实返回 false 让调用方说人话,
 * 而不是显示"已复制"却什么都没发生。
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 落到下面的兜底
  }
  const prev = document.activeElement as HTMLElement | null;
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    // readonly: 触屏上不弹软键盘, 而且 iOS Safari 只有只读时 select() 才可靠。
    ta.setAttribute("readonly", "");
    // 显式定位到左上角 1×1, 不依赖静态位置 —— 靠默认位置的话它会落在
    // 文档末尾, 某些浏览器为了 select() 会去滚动。
    Object.assign(ta.style, {
      position: "fixed",
      top: "0",
      left: "0",
      width: "1px",
      height: "1px",
      opacity: "0",
    });
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    // 焦点还回去。不还的话它落在 <body> 上, 对话框的焦点约束就破了,
    // 键盘用户下一次 Tab 要从整页顶部重来。
    prev?.focus?.();
    return ok;
  } catch {
    prev?.focus?.();
    return false;
  }
}
