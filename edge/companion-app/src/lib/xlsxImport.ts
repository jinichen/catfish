/** xlsxImport — P3.5.173 (7/3 鸿波 catch "组织架构 xlsx 读入没抽实体").
 *
 * 严格背景 (P3.5.172 Phase C 后员工上传福富组织架构.xlsx 现状):
 *   xlsx 严格 3 sheets (市场经营体系 6 一级部 15 二级部 / 生产运营体系 20+ 一级部
 *   80+ 二级部 / 典型职能体系 9 一级部 2 二级部), 但严格 catfish 现有能力:
 *   - catfish-memory sync_turn 严格只做 buffer + summary, 严格不抽 wiki
 *   - P16 attachment ingest 严格只存 raw/sources/, 严格不生成 concept/entity
 *   - WikiCreateModal 严格员工手动一次一个
 *   → 员工手动创建 3 concept + 135+ entity 严格 5+ 小时, catfish 应该自动化.
 *
 * P3.5.173 fix (鸿波拍板 C sprint): WikiTab 加 "📊 从 xlsx 导入" flow:
 *   Stage 1: 员工选文件 (native <input type="file"> 严格无 dialog plugin)
 *   Stage 2: 前端 SheetJS 严格 parse → tree preview → 员工 checkbox 确认
 *   Stage 3: 前端 for-loop wikiCreateEntityOrConcept + progress bar
 *
 * 严格数据主权: parse 严格前端 (员工 mac local), 数据严格不出端.
 *
 * 依赖: `npm i xlsx` (SheetJS ~500KB bundle) — 严格员工本机需装.
 *
 * xlsx 严格结构 heuristic (审"福富组织架构.xlsx" 5 sheets):
 *   - 严格找 header row 含 "一级部" 二字 → 严格 pri col idx
 *   - 严格 pri col+1 = 二级部
 *   - Merge cell: openpyxl / SheetJS 严格拿 merged value 只在 top-left, 严格其他
 *     blank → 严格 forward-fill 一级部 (blank 严格取上一 non-blank)
 *   - 严格 skip 总图 / 组织机构调整发文 等 non-架构 sheet (可选)
 */

import * as XLSX from "xlsx";

/** 严格 xlsx 严格 sheet 严格抽出的结构. */
export interface XlsxSheetTree {
  /** Sheet 名 严格作 concept title (e.g. "市场经营体系") */
  concept: string;
  /** 一级部 严格 list (每个下挂 secondary 二级部) */
  primary: Array<{
    name: string; // 严格一级部名 (e.g. "市场部")
    secondary: string[]; // 严格二级部 list (e.g. ["产品管理中心", "项目管理中心"])
  }>;
}

/** 严格 parse xlsx buffer → 严格 tree structure list.
 *
 * 严格 heuristic:
 *   1. 严格每 sheet iter rows
 *   2. 严格找**含 "一级部" 二字**严格 header row → 严格 col idx
 *   3. 严格 next rows: 严格 col_idx = 一级部, col_idx+1 = 二级部
 *   4. 严格 merge cell forward-fill 一级部 (blank 严格取上一 non-blank)
 *   5. 严格 skip: sheet name 严格含"总图" / "发文" / "调整" 严格 skip
 *
 * 严格 caller: XlsxImportModal Stage 2 preview.
 */
export function parseOrgXlsx(buf: ArrayBuffer): XlsxSheetTree[] {
  const wb = XLSX.read(buf, { type: "array" });
  const result: XlsxSheetTree[] = [];

  for (const sheetName of wb.SheetNames) {
    const sn = sheetName.trim();
    // 严格 skip non-架构 sheet
    if (
      sn.includes("总图") ||
      sn.includes("发文") ||
      sn.includes("调整") ||
      sn.includes("说明") ||
      sn === "Sheet1" // 严格 skip 默认空 sheet
    ) {
      continue;
    }

    const sheet = wb.Sheets[sheetName];
    // 严格 defval: "" 严格 blank cell 严格 return "" 严格不 undefined
    const rows = XLSX.utils.sheet_to_json<unknown[]>(sheet, {
      header: 1,
      defval: "",
    });

    // 严格 find header row 含 "一级部" 二字
    let headerRow = -1;
    let priCol = -1;
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i] as unknown[];
      if (!Array.isArray(row)) continue;
      for (let j = 0; j < row.length; j++) {
        const cell = String(row[j] ?? "").trim();
        if (cell === "一级部") {
          headerRow = i;
          priCol = j;
          break;
        }
      }
      if (headerRow >= 0) break;
    }

    if (headerRow < 0 || priCol < 0) continue; // 严格无 header 严格 skip

    // 严格 parse rows after header, forward-fill 一级部
    const primary: XlsxSheetTree["primary"] = [];
    let currentPrimary: XlsxSheetTree["primary"][number] | null = null;

    for (let i = headerRow + 1; i < rows.length; i++) {
      const row = rows[i] as unknown[];
      if (!Array.isArray(row)) continue;

      const pri = String(row[priCol] ?? "").trim();
      const sec = String(row[priCol + 1] ?? "").trim();

      if (pri) {
        // 严格新一级部
        currentPrimary = { name: pri, secondary: [] };
        primary.push(currentPrimary);
        if (sec) currentPrimary.secondary.push(sec);
      } else if (sec && currentPrimary) {
        // 严格 forward-fill 一级部 (blank pri) 严格加二级部
        currentPrimary.secondary.push(sec);
      }
    }

    // 严格 skip 空 sheet (无一级部)
    if (primary.length === 0) continue;

    result.push({ concept: sheetName.trim(), primary });
  }

  return result;
}

/** 严格 tree 严格计数 (给 UI 显 "X sheet, Y 一级部, Z 二级部"). */
export function countXlsxTree(trees: XlsxSheetTree[]): {
  concepts: number;
  primaries: number;
  secondaries: number;
} {
  let primaries = 0;
  let secondaries = 0;
  for (const t of trees) {
    primaries += t.primary.length;
    for (const p of t.primary) {
      secondaries += p.secondary.length;
    }
  }
  return {
    concepts: trees.length,
    primaries,
    secondaries,
  };
}

/** 严格 selected 严格 tree 严格计数 (员工 checkbox 选后, 只算 checked). */
export function countSelectedTree(
  trees: XlsxSheetTree[],
  selectedConcepts: Set<string>,
  selectedPrimaries: Set<string>, // 严格 key = `${concept}||${primary}`
  selectedSecondaries: Set<string>, // 严格 key = `${concept}||${primary}||${secondary}`
): { concepts: number; primaries: number; secondaries: number } {
  let concepts = 0;
  let primaries = 0;
  let secondaries = 0;
  for (const t of trees) {
    if (!selectedConcepts.has(t.concept)) continue;
    concepts++;
    for (const p of t.primary) {
      const priKey = `${t.concept}||${p.name}`;
      if (!selectedPrimaries.has(priKey)) continue;
      primaries++;
      for (const s of p.secondary) {
        const secKey = `${t.concept}||${p.name}||${s}`;
        if (selectedSecondaries.has(secKey)) secondaries++;
      }
    }
  }
  return { concepts, primaries, secondaries };
}
