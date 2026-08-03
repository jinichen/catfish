/** 「扫描关联建议」报「LLM 返回无法解析」的三个原因。
 *
 * 8/4 鸿波在「中电福富信息科技有限公司」上点扫描, 报:
 *     LLM 返回无法解析 (tool_calls + content 均失败)
 *
 * 那句话没有任何信息量 —— 定位它全靠翻源码 + 推理。查下来三个问题叠在一起:
 *
 * 1. robustJsonParse 处理不了**截断**。max_tokens 打满时 arguments 在数组中间
 *    断掉, 根本没有收尾的 }, `lastIndexOf("}")` 切出来的片段照样不合法。
 *    profile.ts:455 有同款实测记录 ("JSON 在 keyPeople 数组中间截断"), 同一个
 *    模型家族、同样的 max_tokens 2500 —— 而本文件的 prompt 要塞全部候选 title
 *    (实测 225 个), 比写它的时候大得多。
 *
 * 2. 只认 `{suggestions: [...]}` 一种形状。模型返 `{}` / 裸数组 /
 *    `{suggestions: null}` 时两条路径全落空。schema 里的 required 是给模型的
 *    期望, 不是保证。
 *
 * 3. 报错不说发生了什么。一个不说明情况的错误提示等于把问题藏起来。
 */
import { describe, expect, it } from "vitest";
import {
  extractSuggestions,
  repairTruncatedJson,
  robustJsonParse,
} from "./wikiLinkSuggest";

describe("robustJsonParse", () => {
  it("正常 JSON", () => {
    expect(robustJsonParse('{"suggestions":[]}')).toEqual({ suggestions: [] });
  });

  it("JSON 前后有解释文字", () => {
    expect(robustJsonParse('好的:\n{"suggestions":[]}\n以上')).toEqual({
      suggestions: [],
    });
  });

  it("截断在数组中间 —— 老版本在这里返 null, 整个功能报错", () => {
    const truncated =
      '{"suggestions":[{"title":"中电福富","confidence":0.9,"snippet":"a"},' +
      '{"title":"北京福富","confidence":0.8,"snippet":"b"},{"title":"福富","conf';
    const parsed = robustJsonParse<{ suggestions: unknown[] }>(truncated);
    expect(parsed).not.toBeNull();
    // 断在第 3 条中间 → 保住前 2 条完整的
    expect(parsed!.suggestions).toHaveLength(2);
  });

  it("截断在第一个元素中间 —— 补不出完整元素就老实返 null, 不硬凑", () => {
    expect(repairTruncatedJson('{"suggestions":[{"title":"中')).toBeNull();
  });

  it("回归钉子: 截断元素内部的逗号不能当安全点", () => {
    // 第一版把 `,` 也当安全点, 结果被 `{"title":"福富","conf` 里那个逗号带偏,
    // 补齐后仍然不合法 —— 修复功能自己静默失败。node 实测复现过。
    const t =
      '{"suggestions":[{"title":"A","confidence":0.9,"snippet":"a"},' +
      '{"title":"B","confidence":0.8,"snippet":"b"},{"title":"C","conf';
    const parsed = robustJsonParse<{ suggestions: unknown[] }>(t);
    expect(parsed?.suggestions).toHaveLength(2);
  });

  it("完全不是 JSON", () => {
    expect(robustJsonParse("我没找到任何匹配")).toBeNull();
  });
});

describe("extractSuggestions —— 接受模型可能返的几种形状", () => {
  it("标准形状", () => {
    expect(extractSuggestions({ suggestions: [{ title: "a" }] })).toHaveLength(1);
  });

  it("裸数组", () => {
    expect(extractSuggestions([{ title: "a" }])).toHaveLength(1);
  });

  it("suggestions: null → 空结果, 不是失败", () => {
    expect(extractSuggestions({ suggestions: null })).toEqual([]);
  });

  it("换了个键名但只有一个数组字段", () => {
    expect(extractSuggestions({ result: [{ title: "a" }] })).toHaveLength(1);
  });

  it("真的取不到就返 null, 不瞎认", () => {
    expect(extractSuggestions({ a: [1], b: [2] })).toBeNull();
    expect(extractSuggestions("字符串")).toBeNull();
    expect(extractSuggestions(null)).toBeNull();
  });
});
