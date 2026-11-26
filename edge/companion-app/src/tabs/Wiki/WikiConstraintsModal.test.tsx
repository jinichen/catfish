// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ConstraintReport } from "../../lib/tauri_wiki_types";

const fixes: unknown[] = [];
const selectFile = vi.fn(async () => {});
let report: ConstraintReport;

vi.mock("../../lib/tauri_wiki_types", () => ({
  wikiConstraintsReport: vi.fn(async () => report),
  wikiConstraintsFix: vi.fn(async (items: unknown[]) => { fixes.push(items); return { ok: true, done: items.length, errors: [] }; }),
}));
vi.mock("../../store/wiki", () => ({
  useWikiStore: (sel: (s: { selectFile: typeof selectFile; loadFiles: () => Promise<void> }) => unknown) =>
    sel({ selectFile, loadFiles: async () => {} }),
}));
vi.mock("../../components/ModalShell", () => ({ ModalShell: ({ children }: { children: unknown }) => children }));

import WikiConstraintsModal, { autoFixable, fixLabel } from "./WikiConstraintsModal";

const base = { type: "person", type_label: "人" };

describe("WikiConstraintsModal (11/26 约束检查)", () => {
  beforeEach(() => {
    cleanup();
    fixes.length = 0;
    selectFile.mockClear();
    report = {
      ok: true, total: 4,
      summary: { bad_value: 1, link_prop: 2, missing_required: 1 },
      types: [{ key: "project", label: "项目", entries: 67, missing: { 客户: 67 } }],
      issues: [
        { ...base, rel_path: "wiki/entities/c.md", title: "某证书", type: "cert", type_label: "资质证书", kind: "bad_value",
          key: "level", label: "等级", value: "／认证范围／复审", message: "等级「／认证范围／复审」只能是: 甲级 / 乙级",
          options: ["甲级", "乙级"], fix: { op: "set_prop", key: "level" } },
        { ...base, rel_path: "wiki/entities/z.md", title: "张三", kind: "link_prop", key: "department",
          message: "部门没填, 关系里隶属的是「企发部」", fix: { op: "set_prop", key: "department", value: "企发部" } },
        { ...base, rel_path: "wiki/entities/l.md", title: "李四", kind: "link_prop", key: "department",
          message: "部门没填, 关系里隶属的是「人力部」", fix: { op: "set_prop", key: "department", value: "人力部" } },
        { ...base, rel_path: "wiki/entities/p.md", title: "某项目", type: "project", type_label: "项目", kind: "missing_required",
          message: "必填没填: 客户" },
      ],
    };
  });

  it("按种类分组, 默认先看最要紧的 (值不合规); 选一个选项再改", async () => {
    render(<WikiConstraintsModal onClose={() => {}} />);
    await screen.findByText("某证书");
    expect(screen.queryByText("张三")).toBeNull();
    fireEvent.change(screen.getByLabelText("等级改成"), { target: { value: "甲级" } });
    fireEvent.click(screen.getByRole("button", { name: "改" }));
    await waitFor(() => expect(fixes.length).toBe(1));
    expect(fixes[0]).toEqual([{ op: "set_prop", key: "level", value: "甲级", rel_path: "wiki/entities/c.md" }]);
  });

  it("改法确定的一批改", async () => {
    render(<WikiConstraintsModal onClose={() => {}} />);
    await screen.findByText("某证书");
    fireEvent.click(screen.getByRole("button", { name: /属性没填/ }));
    fireEvent.click(screen.getByRole("button", { name: "这 2 项全部按建议改" }));
    await waitFor(() => expect(fixes.length).toBe(1));
    expect((fixes[0] as { rel_path: string }[]).map((x) => x.rel_path)).toEqual(["wiki/entities/z.md", "wiki/entities/l.md"]);
  });

  it("必填没填: 显示每类缺几条; 点标题打开那一页", async () => {
    const onClose = vi.fn();
    render(<WikiConstraintsModal onClose={onClose} />);
    await screen.findByText("某证书");
    fireEvent.click(screen.getByRole("button", { name: /必填没填/ }));
    expect(screen.getByText("项目（67 条）：客户 缺 67")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "某项目" }));
    expect(selectFile).toHaveBeenCalledWith("wiki/entities/p.md");
    expect(onClose).toHaveBeenCalled();
  });

  it("按钮上的字 / 能不能一批改", () => {
    expect(fixLabel({ op: "set_prop", key: "a", value: "企发部" })).toBe("填成「企发部」");
    expect(fixLabel({ op: "replace_relation", rel: "隶属", old: "A", name: "B" })).toBe("关系改成「B」");
    expect(autoFixable(report.issues[0])).toBe(false);
    expect(autoFixable(report.issues[1])).toBe(true);
  });
});
