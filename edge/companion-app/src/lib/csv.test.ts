import { describe, expect, it } from "vitest";
import { parseDelimited } from "./csv";

describe("parseDelimited", () => {
  it("引号里的逗号、换行、转义引号; CRLF; 结尾没换行", () => {
    const { rows, truncated } = parseDelimited('名称,备注\r\n"甲,乙","第一行\n第二行"\r\n丙,"说 ""好"""', ",");
    expect(rows).toEqual([["名称", "备注"], ["甲,乙", "第一行\n第二行"], ["丙", '说 "好"']]);
    expect(truncated).toBe(false);
  });

  it("TSV 和空格子", () => {
    expect(parseDelimited("a\t\tc\n", "\t").rows).toEqual([["a", "", "c"]]);
  });

  it("行数上限", () => {
    const r = parseDelimited("1\n2\n3\n4\n", ",", 2);
    expect(r.rows).toEqual([["1"], ["2"]]);
    expect(r.truncated).toBe(true);
    expect(parseDelimited("1\n2\n", ",", 2).truncated).toBe(false);
  });
});
