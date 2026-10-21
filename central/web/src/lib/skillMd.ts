/** SKILL.md 拆成头部字段 + 正文 (10/22)。
 *
 * 详情页原来把整份 SKILL.md (含 `---` YAML 头) 原样塞进 <pre>, 管理员看到的是
 * `name: / kind: procedural / deprecated: false / triggers:` 这种开发格式。这里只认
 * SKILL.md 实际用到的那点 YAML: `key: value` (可带引号) 和 `key:` 下面 `- 项` 的列表。
 * 认不出来的行跳过, 不报错 —— 原文仍在页面底部「原始文件」里能看。
 */

export type SkillMeta = Record<string, string | string[]>;

export interface ParsedSkillMd {
  meta: SkillMeta;
  body: string;
}

function unquote(v: string): string {
  const t = v.trim();
  if (t.length >= 2 && ((t[0] === '"' && t.endsWith('"')) || (t[0] === "'" && t.endsWith("'")))) {
    return t.slice(1, -1);
  }
  return t;
}

export function parseSkillMd(md: string): ParsedSkillMd {
  const text = md.replace(/^﻿/, "");
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(text);
  if (!m) return { meta: {}, body: text };
  const meta: SkillMeta = {};
  let listKey: string | null = null;
  for (const raw of m[1].split(/\r?\n/)) {
    const item = /^\s+-\s+(.*)$/.exec(raw);
    if (item && listKey) {
      const cur = meta[listKey];
      const list = Array.isArray(cur) ? cur : [];
      list.push(unquote(item[1]));
      meta[listKey] = list;
      continue;
    }
    const kv = /^([A-Za-z_][\w-]*):\s*(.*)$/.exec(raw);
    if (!kv) continue;
    const [, key, value] = kv;
    if (value.trim() === "") {
      listKey = key;
      meta[key] = [];
    } else {
      listKey = null;
      const v = value.trim();
      // 行内列表 [a, b]
      meta[key] = v.startsWith("[") && v.endsWith("]")
        ? v.slice(1, -1).split(",").map(unquote).filter(Boolean)
        : unquote(v);
    }
  }
  return { meta, body: text.slice(m[0].length) };
}

export function metaText(meta: SkillMeta, key: string): string | undefined {
  const v = meta[key];
  return typeof v === "string" && v ? v : undefined;
}

export function metaList(meta: SkillMeta, key: string): string[] {
  const v = meta[key];
  return Array.isArray(v) ? v : [];
}

/** 命名空间给人看的名字。 */
export const NAMESPACE_LABEL: Record<string, string> = {
  personal: "个人",
  department: "部门",
  external: "外部",
  shared: "全公司",
};

export function namespaceLabel(ns: string): string {
  return NAMESPACE_LABEL[ns] ?? ns;
}
