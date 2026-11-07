/**
 * 对话右侧成果区 (11/07) —— 哪些文件能在右栏看、一段对话里出现过哪些、这一轮新生成了哪些。
 *
 * 扩展名分类跟 Rust 端 src-tauri/src/commands/artifacts.rs 的 kind_of 一致 (两边各有单测钉住)。
 */
import type { ChatMessage } from "../types/chat";
import { extractFilePaths } from "./path_detect";

export type ArtifactKind = "html" | "pdf" | "image" | "markdown" | "table" | "text" | "office";

const KINDS: Record<string, ArtifactKind> = {
  html: "html", htm: "html",
  pdf: "pdf",
  png: "image", jpg: "image", jpeg: "image", gif: "image", webp: "image", svg: "image", bmp: "image",
  md: "markdown", markdown: "markdown",
  csv: "table", tsv: "table",
  txt: "text", log: "text", json: "text", yaml: "text", yml: "text", xml: "text", toml: "text", ini: "text",
  sql: "text", sh: "text", py: "text", js: "text", ts: "text", tsx: "text", jsx: "text", css: "text", rs: "text", go: "text",
  docx: "office", xlsx: "office", xlsm: "office", xls: "office", pptx: "office",
};

export function extOf(path: string): string {
  const m = path.match(/\.([A-Za-z0-9]+)$/);
  return m ? m[1].toLowerCase() : "";
}

export function artifactKind(path: string): ArtifactKind | null {
  return KINDS[extOf(path)] ?? null;
}

const KIND_LABEL: Record<ArtifactKind, string> = {
  html: "网页", pdf: "PDF", image: "图片", markdown: "文档", table: "表格", text: "文本", office: "Office",
};
const OFFICE_LABEL: Record<string, string> = { docx: "Word", xlsx: "Excel", xlsm: "Excel", xls: "Excel", pptx: "PPT" };

/** 右栏标题下那行的类型名: Office 文件说具体是 Word / Excel / PPT */
export function artifactLabel(kind: ArtifactKind, ext: string): string {
  return (kind === "office" && OFFICE_LABEL[ext]) || KIND_LABEL[kind];
}

function resultText(result: unknown): string {
  if (result == null) return "";
  return typeof result === "string" ? result : JSON.stringify(result);
}

/** 一条消息里提到的、右栏能显示的文件 (回答正文 + 已完成的工具结果)。
 *  toolsOnly: 只看已完成的工具结果 —— 回答还在一个字一个字出来时, 正文里的路径可能只写了一半。 */
export function messageArtifactPaths(msg: ChatMessage, toolsOnly = false): string[] {
  if (msg.role !== "assistant" && msg.role !== "tool") return [];
  const texts = toolsOnly ? [] : [msg.role === "assistant" ? msg.content : resultText(msg.content)];
  for (const call of msg.tool_calls ?? []) {
    if (call.status === "done") texts.push(resultText(call.result));
  }
  return texts.flatMap((t) => extractFilePaths(t)).filter((p) => artifactKind(p) !== null);
}

/** 整段对话出现过的成果, 最新的在前、去重。 */
export function sessionArtifactPaths(messages: ChatMessage[], toolsOnly = false): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (let i = messages.length - 1; i >= 0; i--) {
    const paths = messageArtifactPaths(messages[i], toolsOnly);
    for (let j = paths.length - 1; j >= 0; j--) {
      if (!seen.has(paths[j])) {
        seen.add(paths[j]);
        out.push(paths[j]);
      }
    }
  }
  return out;
}

/**
 * 这一轮 (最后一条用户消息之后) 提到的成果, 最后提到的在前, 以及这一轮开始的时间。
 * 右栏自动打开时再拿文件修改时间比一下 —— 只开这一轮里真写过的文件, 小鲶只是读了 / 引用了的不弹。
 * toolsOnly: 回答进行中用, 只看已完成的工具结果 (见 messageArtifactPaths)。
 */
export function lastRoundArtifacts(messages: ChatMessage[], toolsOnly = false): { paths: string[]; since: number } | null {
  let lastUser = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "user") {
      lastUser = i;
      break;
    }
  }
  if (lastUser < 0) return null;
  const since = Date.parse(messages[lastUser].ts);
  if (!Number.isFinite(since)) return null;
  const paths = sessionArtifactPaths(messages.slice(lastUser + 1), toolsOnly);
  return paths.length ? { paths, since } : null;
}
