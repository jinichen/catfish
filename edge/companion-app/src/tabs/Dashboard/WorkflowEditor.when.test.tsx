// @vitest-environment jsdom
/** 工作流编辑器 · 满足条件才做 (11/08): 从前面步骤的结果里选、选怎么比, 要比较值的才有输入框。 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Workflow } from "../../lib/tauri_workflows";
import WorkflowEditor, { withOp } from "./WorkflowEditor";

afterEach(cleanup);
const cat = { actions: [{ name: "email_search", label: "搜邮件", level: "read", group: "查", params: ["query"], required: ["query"], outputs: ["matches", "count"] },
                        { name: "create_task", label: "建任务", level: "local", group: "写", params: ["title"], required: ["title"], outputs: ["task"] }],
              skills: [] } as never;
const wf: Workflow = { kind: "workflow", version: "1.0.0", steps: [
  { id: "mails", action: "email_search", params: { query: "资质" } },
  { id: "todo", action: "create_task", params: { title: "x" } },
] } as Workflow;

describe("WorkflowEditor · 条件", () => {
  it("第二步选「满足条件才做」, 默认用前面的结果「有内容」", () => {
    const onChange = vi.fn();
    render(<WorkflowEditor wf={wf} cat={cat} onChange={onChange} />);
    expect(screen.getAllByLabelText("这一步什么时候做")).toHaveLength(1);   // 第一步没有可比的东西 (没参数)
    fireEvent.change(screen.getByLabelText("这一步什么时候做"), { target: { value: "if" } });
    const next = onChange.mock.calls[0][0] as Workflow;
    expect(next.steps[1].when).toEqual({ of: "${steps.mails.matches}", is: "not_empty" });
  });

  it("有条件的: 改比法 / 填比较值", () => {
    const onChange = vi.fn();
    const w = { ...wf, steps: [wf.steps[0], { ...wf.steps[1], when: { of: "${steps.mails.count}", is: "gt" as const, to: 3 } }] };
    render(<WorkflowEditor wf={w} cat={cat} onChange={onChange} />);
    expect((screen.getByLabelText("跟什么比") as HTMLInputElement).value).toBe("3");
    fireEvent.change(screen.getByLabelText("怎么比"), { target: { value: "empty" } });
    expect((onChange.mock.calls[0][0] as Workflow).steps[1].when).toEqual({ of: "${steps.mails.count}", is: "empty" });
  });

  it("withOp: 不用比较值的去掉 to, 要的保留", () => {
    expect(withOp({ of: "${a}", is: "gt", to: 3 }, "not_empty")).toEqual({ of: "${a}", is: "not_empty" });
    expect(withOp({ of: "${a}", is: "gt", to: 3 }, "le")).toEqual({ of: "${a}", is: "le", to: 3 });
  });
});
