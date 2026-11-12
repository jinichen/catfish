// @vitest-environment jsdom
/** 11/12: 知识库本来就是空的 (新员工) 时, 关系整理只拉一次列表, 不无限重拉。 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useWikiStore } from "../../store/wiki";
import WikiOrganizer from "./WikiOrganizer";

vi.mock("./wikiRelationshipTasks", async (orig) => ({
  ...(await orig<typeof import("./wikiRelationshipTasks")>()),
  wikiMigrateLegacyRelations: vi.fn().mockResolvedValue({ scanned_files: 0, converted_relations: 0, changed_files: 0 }),
}));

afterEach(cleanup);

describe("WikiOrganizer 空知识库", () => {
  it("列表一直是空的: 只拉一次", async () => {
    let calls = 0;
    const loadFiles = vi.fn(async () => {
      calls += 1;
      useWikiStore.setState({ filesLoading: true });
      await Promise.resolve();
      useWikiStore.setState({ filesLoading: false, files: [] });
    });
    useWikiStore.setState({ files: [], filesLoading: false, loadFiles });
    render(<WikiOrganizer mode="organize" onModeChange={() => undefined} taskId={null} onSelectTask={() => undefined} />);
    await new Promise((r) => setTimeout(r, 50));
    expect(calls).toBe(1);
  });
});
