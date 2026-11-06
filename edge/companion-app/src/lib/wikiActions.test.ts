import { describe, expect, it } from "vitest";
import { parseWikiActionRefs, wikiActionChatKey, wikiActionPrompt, wikiTypeActionPrompt, writeWikiActionRefs } from "./wikiActions";

describe("Wiki action refs", () => {
  it("parses quoted inline refs and removes duplicates", () => {
    expect(parseWikiActionRefs('title: x\naction_refs: ["skill.a", "skill.a", skill.b]')).toEqual([
      "skill.a",
      "skill.b",
    ]);
  });

  it("writes only action_refs and preserves the body", () => {
    const source = "---\ntitle: x\ntags: [a]\n---\n\n正文\n";
    const output = writeWikiActionRefs(source, ["skill.a", "skill.a"]);
    expect(output).toContain('action_refs: ["skill.a"]');
    expect(output).toContain("tags: [a]");
    expect(output).toContain("正文");
  });

  it("rejects unsafe ids", () => {
    expect(() => writeWikiActionRefs("---\ntitle: x\n---\n", ["../shell"])).toThrow(
      "action_id",
    );
  });
});

describe("wikiActionPrompt (11/06 执行交给小鲶)", () => {
  const entry = { title: "数智化业务事业部", relPath: "wiki/entities/数智化业务事业部.md" };

  it("能直接调的技能: 让小鲶读这条知识取参数, 用 catfish_run_skill, 缺的问人", () => {
    const text = wikiActionPrompt(
      { label: "生成员工周报", skill_path: "department/weekly-report", skill_dir: "/x/department/weekly-report", runnable: true },
      entry,
    );
    expect(text).toContain("《数智化业务事业部》");
    expect(text).toContain("catfish_wiki_read (rel_path=wiki/entities/数智化业务事业部.md");
    expect(text).toContain("catfish_run_skill 调技能 department/weekly-report");
    expect(text).toContain("不要编");
    expect(text).toContain("先问我");
  });

  it("只有说明的技能: 让小鲶读 SKILL.md 照着做, 不调 catfish_run_skill", () => {
    const text = wikiActionPrompt(
      { label: "办理 EIS 资质审批", skill_path: "department/eis-zizhi-shenpi", skill_dir: "/h/.catfish/skills/department/eis-zizhi-shenpi", runnable: false },
      entry,
    );
    expect(text).toContain("/h/.catfish/skills/department/eis-zizhi-shenpi/SKILL.md");
    expect(text).not.toContain("catfish_run_skill");
  });

  it("同一条知识同一个行动用同一个对话 key", () => {
    expect(wikiActionChatKey("wiki/a.md", "skill.x")).toBe(wikiActionChatKey("wiki/a.md", "skill.x"));
    expect(wikiActionChatKey("wiki/a.md", "skill.x")).not.toBe(wikiActionChatKey("wiki/b.md", "skill.x"));
  });
});

describe("wikiTypeActionPrompt (11/07 类型上的行动)", () => {
  const entry = {
    title: "CCRC 信息安全服务资质软件安全开发一级",
    relPath: "wiki/entities/ccrc.md",
    typeLabel: "资质证书",
    properties: [
      { label: "证书编号", value: "CCRC-2019-ISV-SD-210" },
      { label: "复审日期", value: "" },
    ],
  };

  it("属性直接带过去, 没填的写未填; 要做的事照定义里那句", () => {
    const text = wikiTypeActionPrompt(
      { label: "到期提醒", instruction: "用 catfish_create_reminder 建提醒", skill_path: "", skill_dir: null, runnable: false },
      entry,
    );
    expect(text).toContain("（资质证书）做「到期提醒」");
    expect(text).toContain("证书编号: CCRC-2019-ISV-SD-210");
    expect(text).toContain("复审日期: （未填）");
    expect(text).toContain("要做的事：用 catfish_create_reminder 建提醒");
    expect(text).not.toContain("catfish_run_skill");
    expect(text).toContain("不要编");
  });

  it("要技能的行动: 能直接调的提 catfish_run_skill, 只有说明的提 SKILL.md", () => {
    const run = wikiTypeActionPrompt({ label: "官网核查", instruction: "核对", skill_path: "personal/x", skill_dir: "/d/personal/x", runnable: true }, entry);
    expect(run).toContain("catfish_run_skill 调技能 personal/x");
    const doc = wikiTypeActionPrompt({ label: "官网核查", instruction: "核对", skill_path: "personal/x", skill_dir: "/d/personal/x", runnable: false }, entry);
    expect(doc).toContain("/d/personal/x/SKILL.md");
  });
});
