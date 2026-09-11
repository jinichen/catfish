// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import ReactMarkdown from "react-markdown";
import { wikiMarkdownUrl } from "./wikiMarkdownUrl";

afterEach(cleanup);

it("preserves a generated knowledge link through Markdown rendering and clicking", () => {
  const open = vi.fn();
  const name = "ITSS智能运维一级";
  render(
    <ReactMarkdown urlTransform={wikiMarkdownUrl} components={{
      a: ({ href, children }) => (
        <button onClick={() => open(decodeURIComponent(href!.slice("catfish-wikilink://".length)))}>
          {children}
        </button>
      ),
    }}>
      {`注意：不是 [${name}](catfish-wikilink://${encodeURIComponent(name)})。`}
    </ReactMarkdown>,
  );
  fireEvent.click(screen.getByRole("button", { name }));
  expect(open).toHaveBeenCalledWith(name);
});

it("keeps unsafe URLs blocked and preserves ordinary HTTPS links", () => {
  expect(wikiMarkdownUrl("javascript:alert(1)", "href")).toBe("");
  expect(wikiMarkdownUrl("catfish-wikilink://test", "src")).toBe("");
  expect(wikiMarkdownUrl("https://example.com", "href")).toBe("https://example.com");
});
