import { defaultUrlTransform } from "react-markdown";

/** Preserve internal wiki references on anchors; retain normal URL filtering elsewhere. */
export function wikiMarkdownUrl(url: string, key: string): string {
  if (key === "href" && url.startsWith("catfish-wikilink://")) return url;
  return defaultUrlTransform(url);
}
