/**
 * 小鲶回答下面的依据 (11/18): 让小鲶改纪要、会中问小鲶、群聊 @小鲶 共用。
 *   · 本机的 (知识库 / 文件 / 邮件) 一行 —— sourcesLine
 *   · 上网查到的 (tool-bridge web_lookup) 列标题 + 网站, 点开用系统浏览器看原文
 */
import type { CSSProperties, ReactNode } from "react";
import { Globe } from "@phosphor-icons/react";
import { Ico } from "./icons";

export type AskSource = { kind: string; title: string; where: string };

const isWeb = (s: AskSource) => s.kind === "网页" && /^https?:\/\//.test(s.where);

/** 「依据 (你电脑上的): 知识库「…」· 文件「…」」(前 5 条, 不含网页) */
export function sourcesLine(src: AskSource[] | undefined): string {
  const local = (src ?? []).filter((s) => s.kind !== "网页");
  if (!local.length) return "";
  return "依据 (你电脑上的): " + local.slice(0, 5).map((s) => `${s.kind === "本机文件" ? "文件" : s.kind}「${s.title}」`).join(" · ");
}

function hostOf(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
}

async function openLink(url: string) {
  try {
    const { open } = await import("@tauri-apps/plugin-shell");
    await open(url);
  } catch { /* 不在 Tauri 里 / 打不开就算了 */ }
}

export function AskSources({ sources, style, localIcon = "" }: { sources?: AskSource[]; style: CSSProperties; localIcon?: ReactNode }) {
  const web = (sources ?? []).filter(isWeb);
  const line = sourcesLine(sources);
  return (
    <>
      {web.length > 0 && (
        <div style={style}>
          <Ico as={Globe} />网上查到的:{" "}
          {web.map((s, i) => (
            <span key={s.where}>
              {i > 0 && " · "}
              <a href={s.where} title={s.where} onClick={(e) => { e.preventDefault(); void openLink(s.where); }}
                style={{ color: "var(--catfish-cyan-bright)" }}>《{s.title}》</a>
              <span> ({hostOf(s.where)})</span>
            </span>
          ))}
        </div>
      )}
      {line && <div style={style}>{localIcon}{line}</div>}
    </>
  );
}
