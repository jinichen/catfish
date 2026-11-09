// @vitest-environment jsdom
/** 11/09 鸿波「删除无效」: 页上别处一条老关系不合规, 删另一条也存不了 (提示还在面板最底下)。数据全是虚构的。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const t = vi.hoisted(() => ({ wikiUpdateFile: vi.fn(), wikiCreateEntityOrConcept: vi.fn(), wikiGuardCheck: vi.fn() }));
vi.mock("../../lib/tauri", async (orig) => ({ ...(await orig<typeof import("../../lib/tauri")>()), wikiUpdateFile: t.wikiUpdateFile, wikiCreateEntityOrConcept: t.wikiCreateEntityOrConcept }));
vi.mock("../../lib/tauri_wiki_types", () => ({ wikiGuardCheck: t.wikiGuardCheck }));
vi.mock("../../lib/wikiRelationVocab", () => ({ useRelationVocab: (sel: (s: { custom: [] }) => unknown) => sel({ custom: [] }) }));

import type { WikiFileFull, WikiFileInfo } from "../../lib/tauri_wiki";
import { useWikiStore } from "../../store/wiki";
import WikiRelationsEditor from "./WikiRelationsEditor";

const file = (title: string, subtype: string) => ({ rel_path: `wiki/entities/${title}.md`, title, kind: "entity", subtype, aliases: [], related: [] }) as unknown as WikiFileInfo;
const files = [file("南岭科技", "org"), file("金河达", "org"), file("软件台账", "doc"), file("北江集团", "org")];
const page: WikiFileFull = {
  info: { ...files[0], ontology_status: "active",
          related: [{ name: "金河达", rel: "协作" }, { name: "软件台账", rel: "持有" }] } as unknown as WikiFileInfo,
  content: '---\ntype: entity\ntitle: 南岭科技\nentity_type: org\nrelated: [{name: "金河达", rel: "协作"}, {name: "软件台账", rel: "持有"}]\n---\n\n正文\n',
  frontmatter: "", body: "正文",
};
const oldBad = { name: "软件台账", rel: "持有", why: "组织→文件资料「软件台账」不能用「持有」" };

beforeEach(() => {
  useWikiStore.setState({ files, loadFiles: vi.fn(async () => {}), selectFile: vi.fn(async () => {}) });
  t.wikiUpdateFile.mockResolvedValue({ ok: true });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("WikiRelationsEditor 保存校验", () => {
  it("页上原来就有一条不合规: 删另一条照样能存", async () => {
    t.wikiGuardCheck.mockResolvedValue({ ok: false, bad: [oldBad], missing: [] });
    render(<WikiRelationsEditor selectedFile={page} onLater={null} />);
    fireEvent.click(screen.getByLabelText("删除与「金河达」的关系"));
    await waitFor(() => expect(t.wikiUpdateFile).toHaveBeenCalledTimes(1));
    const written = t.wikiUpdateFile.mock.calls[0][1] as string;
    expect(written).not.toContain("金河达");
    expect(written).toContain("软件台账");
    expect(screen.queryByText(/没保存/)).toBeNull();
  });

  it("这次新加的不合规: 照样拦, 写明原因", async () => {
    t.wikiGuardCheck.mockResolvedValue({ ok: false, bad: [{ name: "北江集团", rel: "持有", why: "组织→组织「北江集团」不能用「持有」" }], missing: [] });
    render(<WikiRelationsEditor selectedFile={page} onLater={null} />);
    fireEvent.click(screen.getByText(/再加一条关系/));
    fireEvent.change(screen.getByLabelText("关系类型"), { target: { value: "持有" } });
    fireEvent.change(screen.getByLabelText("关系目标"), { target: { value: "北江集团" } });
    const add = screen.getAllByRole("button").find((b) => /^加上|^添加|^加这条/.test(b.textContent ?? ""))!;
    fireEvent.click(add);
    expect(await screen.findByText(/没保存.*北江集团/)).toBeTruthy();
    expect(t.wikiUpdateFile).not.toHaveBeenCalled();
  });
});
