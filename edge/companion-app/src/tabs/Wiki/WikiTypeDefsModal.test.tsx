// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TypeDefChange, TypeDefsView } from "../../lib/tauri_wiki_types";

const changes: TypeDefChange[] = [];
const VIEW: TypeDefsView = {
  ok: true,
  subtypes: [{ key: "system", count: 9 }],
  types: [{
    key: "cert", label: "资质证书", origin: "builtin",
    status: { from: "valid_until", warn_days: 180 },
    properties: [
      { key: "cert_no", label: "证书编号", type: "text", origin: "builtin", hidden: false },
      { key: "valid_until", label: "有效期至", type: "date", origin: "builtin", hidden: false },
      { key: "custom_1", label: "投标用途", type: "text", origin: "local", hidden: false },
    ],
    actions: [{ id: "type.cert.expiry-reminder", label: "到期提醒", instruction: "建提醒", origin: "builtin", hidden: false }],
  }],
};

vi.mock("../../lib/tauri_wiki_types", () => ({
  wikiTypeDefs: vi.fn(async () => VIEW),
  wikiTypeDefsApply: vi.fn(async (c: TypeDefChange) => { changes.push(c); return VIEW; }),
}));

import WikiTypeDefsModal from "./WikiTypeDefsModal";

describe("WikiTypeDefsModal (11/07 类型定义页)", () => {
  beforeEach(() => {
    cleanup();
    changes.length = 0;
  });

  it("内置的只能隐藏, 自己加的能删; 提醒天数显示", async () => {
    render(<WikiTypeDefsModal onClose={() => {}} />);
    await screen.findByLabelText("提前天数");
    expect(screen.getAllByRole("button", { name: "隐藏" }).length).toBe(3);   // 2 个内置属性 + 1 个内置行动
    expect(screen.getAllByRole("button", { name: "删除" }).length).toBe(1);
    expect((screen.getByLabelText("提前天数") as HTMLInputElement).value).toBe("180");
  });

  it("加属性 / 隐藏 / 加行动 (插入属性名) 各提交一处改动", async () => {
    render(<WikiTypeDefsModal onClose={() => {}} />);
    await screen.findByLabelText("提前天数");
    fireEvent.change(screen.getByLabelText("新属性名"), { target: { value: "复审机构" } });
    fireEvent.click(screen.getByRole("button", { name: "加属性" }));
    await waitFor(() => expect(changes[0]).toEqual({ op: "add_property", type: "cert", property: { label: "复审机构", type: "text", hint: "" } }));
    fireEvent.click(screen.getAllByRole("button", { name: "隐藏" })[0]);
    await waitFor(() => expect(changes[1]).toEqual({ op: "hide_property", type: "cert", key: "cert_no" }));
    fireEvent.change(screen.getByLabelText("行动名"), { target: { value: "续期准备" } });
    fireEvent.change(screen.getByLabelText("要做的事"), { target: { value: "按 " } });
    fireEvent.click(screen.getByRole("button", { name: "有效期至" }));
    fireEvent.click(screen.getByRole("button", { name: "加行动" }));
    await waitFor(() => expect(changes[2]).toMatchObject({ op: "add_action", type: "cert", action: { label: "续期准备", instruction: "按 {有效期至}" } }));
  });

  it("新建类型从知识库已有分类里选", async () => {
    render(<WikiTypeDefsModal onClose={() => {}} />);
    await screen.findByLabelText("提前天数");
    fireEvent.change(screen.getByLabelText("知识库里的分类"), { target: { value: "system" } });
    fireEvent.change(screen.getByLabelText("新类型名"), { target: { value: "业务系统" } });
    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    await waitFor(() => expect(changes[0]).toEqual({ op: "add_type", type: "system", label: "业务系统" }));
  });
});
