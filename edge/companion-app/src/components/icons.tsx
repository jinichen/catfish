/**
 * 11/24 unslop: 界面里的 emoji 图标统一换成 Phosphor 线性图标 (rail / 会话栏早就用它)。
 *
 * 规矩: 一套图标 (Phosphor), 一个尺寸 (默认 16), 一个笔画 (regular), 颜色跟着所在文字走 (currentColor)。
 * 不给图标单独上色; 要区分类别用文字或小圆点。
 *
 * - <Ico as={X} />          : 行内图标, 跟文字基线对齐、跟文字留 4px
 * - <FileKindIcon name=... />: 按扩展名选文件图标 (替原来的 fileEmoji)
 */
import type { CSSProperties } from "react";
import type { Icon } from "@phosphor-icons/react";
import {
  File,
  FileArchive,
  FileCode,
  FileDoc,
  FileImage,
  FilePdf,
  FilePpt,
  FileText,
  FileXls,
  Folder,
  Globe,
} from "@phosphor-icons/react";

export const ICON_SIZE = 16;

const inline: CSSProperties = { display: "inline-block", verticalAlign: "-0.15em", flex: "none" };

/** 行内图标: 默认 16px、regular、颜色 = 当前文字色。gap = 跟后面文字的间距 (默认 4) */
export function Ico({ as: C, size = ICON_SIZE, gap = 4, style, title }: {
  as: Icon;
  size?: number;
  gap?: number;
  style?: CSSProperties;
  title?: string;
}) {
  return (
    <C
      size={size}
      weight="regular"
      aria-hidden={title ? undefined : true}
      aria-label={title}
      style={{ ...inline, marginRight: gap, ...style }}
    />
  );
}

const BY_EXT: Record<string, Icon> = {
  doc: FileDoc, docx: FileDoc,
  pdf: FilePdf,
  ppt: FilePpt, pptx: FilePpt,
  xls: FileXls, xlsx: FileXls, xlsm: FileXls, csv: FileXls, tsv: FileXls,
  json: FileCode, yaml: FileCode, yml: FileCode,
  txt: FileText, md: FileText, markdown: FileText, log: FileText,
  png: FileImage, jpg: FileImage, jpeg: FileImage, gif: FileImage, svg: FileImage, webp: FileImage, heic: FileImage,
  zip: FileArchive, tar: FileArchive, gz: FileArchive, "7z": FileArchive, rar: FileArchive,
  html: Globe, htm: Globe,
};

export function fileKindIcon(name: string): Icon {
  const base = name.split(/[\\/]/).pop() ?? name;
  const dot = base.lastIndexOf(".");
  if (dot <= 0) return name.endsWith("/") ? Folder : File;
  return BY_EXT[base.slice(dot + 1).toLowerCase()] ?? File;
}

/** 按文件名出图标 (替 fileEmoji) */
export function FileKindIcon({ name, size = ICON_SIZE, gap = 4 }: { name: string; size?: number; gap?: number }) {
  return <Ico as={fileKindIcon(name)} size={size} gap={gap} />;
}
