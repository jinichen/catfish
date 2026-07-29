// 稻生万物 · 智能制造建设方案 · 落地上海临港 · v14 · 16 页
//
// ═══ v14 vs v13 (7/28 鸿波第二轮反馈) ═══
//
// 鸿波三条:
//   1. **牡蛎壳内容全部去掉** —— v13 的"牡蛎壳↔海洋园"选址主轴随之作废,
//      选址理由改为 政策强度 + 区位物流 + 产业配套 三项, 压缩进政策页
//   2. **不要太多谈稻生万物** —— v13 用整页 P6 讲专利/认证/客户,
//      v14 压成 P2 里一行, 不单独成页
//   3. **重点放在智能制造的目标与实现** —— 这是本版主体, 占 P4-P11 共 8 页
//
// ═══ 为什么用成熟度模型做目标框架 ═══
//
// "智能制造目标"不能只写"要智能化" —— 投资人会问"到什么程度算达成".
// 但客户现在没有产能/良品率/自动化率的基线数字, 硬写就是编.
//
// 解法: 用 GB/T 39116《智能制造能力成熟度模型》的 5 级体系做目标框架.
//   - 国标通行语言, 投资人和临港申报口径都认
//   - 描述的是**能力等级**不是绝对数字, 不需要编基线
//   - 客户此前做过智能体管理能力成熟度评估, 对这套语言熟悉
//   - 具体数字指标 (良品率/OEE/换型时间) 留占位, 基线测完再填
//
// 实现部分用 ISA-95 分层 (设备→控制→执行→管理→决策), 也是通行标准,
// 每层写清楚"建什么/解决什么/对应哪条临港政策".
//
// ═══ 军规 (延续) ═══
//   - 不点"鲶鱼"品牌名
//   - 不写人月工期
//   - 投资额/产能/良品率等一律占位"待测算", 不编
//   - 政策数字逐条标出处页, 不做加总
//   - 老 PPT 的 10万吨/IPO 口径本版**不再出现** (v13 P12 已归档该处理方式,
//     v14 主体是建设方案不是规划目标, 那套数字放进来会喧宾夺主)
//
// 数据来源:
//   政策 → 上海临港新片区政策情况.pdf (客户 7/27 提供) p7-p12
//   原料 → 稻壳纤维产业集群介绍0126.pptx p6
//   资质 → 同上 p8-p11 p18

const pptxgen = require("pptxgenjs");

const p = new pptxgen();
p.layout = "LAYOUT_WIDE";
p.title = "稻生万物 · 智能制造建设方案";
p.author = "稻生万物 (谷千合)";

const C = {
  primary: "2C5F2D", primaryDk: "1F4220",
  secondary: "97BC62", accent: "D4A574", accentDk: "A87F51",
  cream: "F5F1E8", white: "FFFFFF", bg: "FAFAFA",
  bg2: "F0EDE4",
  dark: "1A1A1A", gray: "5C5C5C", grayLt: "999999",
  redAccent: "B85042",
  steel: "34495E", steelLt: "5D6D7E",
};
const W = 13.3, H = 7.5;
const TOTAL = 16;

let PN = 0;
function nextP() { PN++; return PN; }

function footer(s) {
  s.addText("稻生万物  ·  智能制造建设方案  ·  上海临港新片区", {
    x: 0.5, y: 7.15, w: 11, h: 0.3,
    fontSize: 9, color: C.grayLt, fontFace: "Calibri", margin: 0,
  });
  s.addText(`${PN} / ${TOTAL}`, {
    x: W - 1.2, y: 7.15, w: 0.7, h: 0.3,
    fontSize: 9, color: C.grayLt, fontFace: "Calibri",
    align: "right", margin: 0,
  });
}

function pageTitle(s, title, kicker) {
  s.addText(String(PN).padStart(2, "0"), {
    x: 0.6, y: 0.5, w: 0.9, h: 0.7,
    fontSize: 32, bold: true, color: C.accent,
    fontFace: "Cambria", margin: 0,
  });
  s.addText(kicker, {
    x: 1.55, y: 0.55, w: 8, h: 0.28,
    fontSize: 10, color: C.grayLt, fontFace: "Calibri",
    charSpacing: 4, bold: true, margin: 0,
  });
  s.addText(title, {
    x: 1.55, y: 0.82, w: 11.5, h: 0.5,
    fontSize: 26, bold: true, color: C.primary,
    fontFace: "Cambria", margin: 0,
  });
}

function subTitle(s, text, color) {
  s.addText(text, {
    x: 1.55, y: 1.32, w: 11, h: 0.28,
    fontSize: 10.5, color: color || C.gray, fontFace: "Calibri", margin: 0,
  });
}

function card(s, { x, y, w, h, tag, title, body, fill, lineColor }) {
  s.addShape(p.ShapeType.roundRect, {
    x, y, w, h, fill: { color: fill || C.white },
    line: { color: lineColor || C.bg2, width: 1 }, rectRadius: 0.06,
  });
  let cy = y + 0.2;
  if (tag) {
    s.addText(tag, {
      x: x + 0.24, y: cy, w: w - 0.48, h: 0.22,
      fontSize: 9, bold: true, color: C.accentDk,
      fontFace: "Calibri", charSpacing: 2, margin: 0,
    });
    cy += 0.3;
  }
  if (title) {
    s.addText(title, {
      x: x + 0.24, y: cy, w: w - 0.48, h: 0.3,
      fontSize: 13, bold: true, color: C.primary,
      fontFace: "Cambria", margin: 0,
    });
    cy += 0.38;
  }
  if (body) {
    s.addText(body, {
      x: x + 0.24, y: cy, w: w - 0.48, h: y + h - cy - 0.16,
      fontSize: 9.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    });
  }
}

function sourceNote(s, text, y) {
  s.addText(text, {
    x: 0.6, y: y || 6.8, w: 12, h: 0.28,
    fontSize: 8, color: C.grayLt, fontFace: "Calibri",
    italic: true, margin: 0,
  });
}

// ═══════════════════════════════ P1 · 封面
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.primary };
  s.addShape(p.ShapeType.rect, { x: 0, y: 0, w: W, h: 0.12, fill: { color: C.accent } });

  s.addText("智 能 制 造", {
    x: 1.1, y: 1.95, w: 11, h: 1.0,
    fontSize: 50, bold: true, color: C.white,
    fontFace: "Cambria", charSpacing: 8, margin: 0,
  });
  s.addText("建 设 方 案", {
    x: 1.1, y: 2.95, w: 11, h: 0.75,
    fontSize: 36, color: C.cream, fontFace: "Cambria",
    charSpacing: 6, margin: 0,
  });

  s.addShape(p.ShapeType.rect, { x: 1.15, y: 3.95, w: 1.6, h: 0.04, fill: { color: C.accent } });

  s.addText("稻生万物  ·  稻壳生物基复合材料", {
    x: 1.1, y: 4.25, w: 11, h: 0.4,
    fontSize: 15, color: C.secondary, fontFace: "Calibri", charSpacing: 1, margin: 0,
  });
  s.addText("落地  上海临港新片区", {
    x: 1.1, y: 4.68, w: 11, h: 0.35,
    fontSize: 13, color: C.secondary, fontFace: "Calibri", charSpacing: 1, margin: 0,
  });

  s.addText("对标 GB/T 39116 智能制造能力成熟度  ·  2026 年 7 月", {
    x: 1.1, y: 5.95, w: 11, h: 0.3,
    fontSize: 11, color: C.grayLt, fontFace: "Calibri", margin: 0,
  });
}

// ═══════════════════════════════ P2 · 一页看懂
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "一页看懂  ·  这个方案要做什么", "AT A GLANCE");

  const items = [
    ["01", "建设目标",
      "建成对标 GB/T 39116 集成级(L3)、关键环节达优化级(L4) 的稻壳生物基材料智能工厂"],
    ["02", "核心能力",
      "工艺参数数字化固化 · 质检从抽检升级全检 · 生产数据全流程贯通 · 能碳可计量可追溯"],
    ["03", "技术路线",
      "按 ISA-95 五层架构分层建设 —— 设备层 / 控制层 / 车间执行层 / 工厂管理层 / 数据决策层"],
    ["04", "政策载体",
      "对应临港「技术改造和智能化升级」最高 5000 万 ·「首台(套)」·「研发总部」等专项"],
    ["05", "现阶段待定",
      "现状基线 (良品率 / OEE / 能耗) 未测 · 投资额与产能未定 —— 指标位置留占位, 基线测完补入"],
  ];

  items.forEach((it, i) => {
    const y = 1.62 + i * 1.02;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: 0.9,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 },
    });
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 0.05, h: 0.9,
      fill: { color: i === 4 ? C.redAccent : C.accent },
    });
    s.addText(it[0], {
      x: 0.85, y: y + 0.16, w: 0.5, h: 0.3,
      fontSize: 15, bold: true, color: i === 4 ? C.redAccent : C.accent,
      fontFace: "Cambria", margin: 0,
    });
    s.addText(it[1], {
      x: 1.45, y: y + 0.14, w: 2.5, h: 0.32,
      fontSize: 13, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(it[2], {
      x: 4.05, y: y + 0.15, w: 8.4, h: 0.62,
      fontSize: 10, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    });
  });

  s.addText(
    "项目主体 · 稻生万物 (谷千合) —— 稻壳生物基复合材料深加工 · 100+ 项专利 · 欧盟 TUV / 德国 DIN 等国际认证 · 具备品牌客户供应经验",
    {
      x: 0.6, y: 6.82, w: 12.1, h: 0.3,
      fontSize: 9, color: C.grayLt, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P3 · 目录
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "目录", "CONTENTS");

  const parts = [
    ["PART 1", "建设目标  ·  达到什么能力等级", "P4 - P5"],
    ["PART 2", "总体架构  ·  五层技术体系", "P6"],
    ["PART 3", "分层实现  ·  每层建什么 · 解决什么", "P7 - P11"],
    ["PART 4", "园区协同与政策对接", "P12 - P13"],
    ["PART 5", "实施路径  ·  风险  ·  下一步", "P14 - P16"],
  ];
  parts.forEach((pt, i) => {
    const y = 1.9 + i * 1.0;
    s.addText(pt[0], {
      x: 1.0, y, w: 1.6, h: 0.3,
      fontSize: 11, bold: true, color: C.accent,
      fontFace: "Calibri", charSpacing: 2, margin: 0,
    });
    s.addText(pt[1], {
      x: 2.8, y: y - 0.04, w: 8, h: 0.4,
      fontSize: 16, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(pt[2], {
      x: 11.2, y, w: 1.4, h: 0.3,
      fontSize: 11, color: C.grayLt, fontFace: "Calibri", align: "right", margin: 0,
    });
    s.addShape(p.ShapeType.rect, {
      x: 1.0, y: y + 0.52, w: 11.6, h: 0.01, fill: { color: C.bg2 },
    });
  });
  footer(s);
}

// ═══════════════════════════════ P4 · 建设目标 (成熟度等级)
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "建设目标  ·  智能制造能力等级", "PART 1 · TARGET LEVEL");
  subTitle(s, "对标 GB/T 39116《智能制造能力成熟度模型》· 用国标等级定义目标, 避免\"要智能化\"这类无法验收的表述");

  const levels = [
    ["L1", "规划级", "开始规划,\n关键活动依赖人工", false],
    ["L2", "规范级", "关键活动规范化,\n单点自动化与信息化", false],
    ["L3", "集成级", "设备与系统集成,\n数据跨环节共享", true],
    ["L4", "优化级", "数据驱动优化,\n模型辅助决策", true],
    ["L5", "引领级", "全产业链协同,\n模式创新引领", false],
  ];
  levels.forEach((l, i) => {
    const x = 0.6 + i * 2.45;
    const isTarget = l[3];
    s.addShape(p.ShapeType.roundRect, {
      x, y: 1.85, w: 2.3, h: 2.15,
      fill: { color: isTarget ? C.primary : C.white },
      line: { color: isTarget ? C.primary : C.bg2, width: isTarget ? 2 : 1 },
      rectRadius: 0.06,
    });
    s.addText(l[0], {
      x: x + 0.2, y: 2.05, w: 1.9, h: 0.45,
      fontSize: 24, bold: true, color: isTarget ? C.accent : C.grayLt,
      fontFace: "Cambria", margin: 0,
    });
    s.addText(l[1], {
      x: x + 0.2, y: 2.55, w: 1.9, h: 0.3,
      fontSize: 13, bold: true, color: isTarget ? C.white : C.gray,
      fontFace: "Cambria", margin: 0,
    });
    s.addText(l[2], {
      x: x + 0.2, y: 2.95, w: 1.9, h: 0.9,
      fontSize: 9, color: isTarget ? C.cream : C.grayLt,
      fontFace: "Calibri", lineSpacing: 13, margin: 0, valign: "top",
    });
    if (isTarget) {
      s.addShape(p.ShapeType.rect, {
        x, y: 1.85, w: 2.3, h: 0.06, fill: { color: C.accent },
      });
    }
  });

  s.addText("▲  本项目目标区间", {
    x: 5.3, y: 4.08, w: 4.5, h: 0.3,
    fontSize: 11, bold: true, color: C.accentDk, fontFace: "Calibri", margin: 0,
  });

  // 四个能力维度的具体目标
  s.addText("分维度目标", {
    x: 0.6, y: 4.5, w: 4, h: 0.3,
    fontSize: 13, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const dims = [
    ["工艺与装备", "配方与工艺参数从经验固化为数字模型 · 关键工序自动化 · 换型可程序化调用"],
    ["质量管理", "从抽检升级到在线全检 · 缺陷可追溯到批次与工艺参数 · 质量数据反哺工艺优化"],
    ["数据集成", "设备 → 车间 → 管理层数据贯通 · 消除信息孤岛 · 单一数据源"],
    ["绿色制造", "能耗与碳排在线计量 · 产品级碳足迹可核算 · 支撑零碳认证与出口合规"],
  ];
  dims.forEach((d, i) => {
    const x = 0.6 + (i % 2) * 6.15;
    const y = 4.9 + Math.floor(i / 2) * 0.92;
    s.addShape(p.ShapeType.rect, {
      x, y, w: 5.95, h: 0.82,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 },
    });
    s.addText(d[0], {
      x: x + 0.2, y: y + 0.12, w: 1.5, h: 0.28,
      fontSize: 11, bold: true, color: C.accentDk, fontFace: "Calibri", margin: 0,
    });
    s.addText(d[1], {
      x: x + 0.2, y: y + 0.4, w: 5.5, h: 0.38,
      fontSize: 9, color: C.gray, fontFace: "Calibri",
      lineSpacing: 12, margin: 0, valign: "top",
    });
  });

  sourceNote(s, "注 · 各维度的量化指标 (良品率 / OEE / 单位产品能耗 / 换型时间) 需完成现状基线测评后确定, 见 P5");
  footer(s);
}

// ═══════════════════════════════ P5 · 现状基线与差距
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "从现状到目标  ·  要跨过什么", "PART 1 · GAP ANALYSIS");
  subTitle(s, "生物基复合材料加工的典型痛点 · 也是本方案要解决的具体问题");

  const gaps = [
    ["工艺依赖老师傅",
      "配方与加工参数靠经验判断 · 不同批次稻壳含水率/粒径波动需人工调参 · 老师傅离岗即断层",
      "参数寻优模型 + 工艺知识库 · 把经验变成可复用的数字资产"],
    ["质量靠抽检",
      "生物基材料外观缺陷 (色差 / 气泡 / 杂质) 抽检漏检率高 · problem 批次难追溯到具体工艺段",
      "在线视觉全检 + 缺陷与工艺参数关联分析 · 从事后判废转向事中拦截"],
    ["设备各管各的",
      "混配 / 成型 / 后处理设备来自不同厂商 · 数据不互通 · 排产与实际进度脱节",
      "设备统一联网 + MES 排产闭环 · 计划与执行实时对齐"],
    ["能碳算不清",
      "能耗按整厂电表分摊 · 无法核算单位产品碳足迹 · 出口碳关税与零碳认证缺数据支撑",
      "分表分项计量 + 产品级碳足迹核算 · 直接支撑认证与出口合规"],
  ];
  gaps.forEach((g, i) => {
    const y = 1.8 + i * 1.22;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: 1.1,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 },
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 0.05, h: 1.1, fill: { color: C.redAccent } });
    s.addText(g[0], {
      x: 0.88, y: y + 0.14, w: 2.2, h: 0.3,
      fontSize: 12, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText("现状", {
      x: 0.88, y: y + 0.52, w: 0.6, h: 0.24,
      fontSize: 8.5, bold: true, color: C.redAccent, fontFace: "Calibri", margin: 0,
    });
    s.addText(g[1], {
      x: 3.25, y: y + 0.14, w: 4.6, h: 0.85,
      fontSize: 9, color: C.gray, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "middle",
    });
    s.addShape(p.ShapeType.rect, {
      x: 8.0, y: y + 0.25, w: 0.28, h: 0.02, fill: { color: C.accent },
    });
    s.addText(g[2], {
      x: 8.45, y: y + 0.14, w: 4.0, h: 0.85,
      fontSize: 9, color: C.primary, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "middle",
    });
  });

  s.addText(
    "⚠ 上述为行业典型问题梳理 · 稻生万物现场实际状况需实地调研与基线测评后确认",
    {
      x: 0.6, y: 6.72, w: 12.1, h: 0.3,
      fontSize: 9, color: C.redAccent, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P6 · 总体架构 (五层)
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "总体架构  ·  五层技术体系", "PART 2 · ARCHITECTURE");
  subTitle(s, "按 ISA-95 分层建设 · 自下而上打通 · 每层可独立验收, 也可分期投资");

  const layers = [
    ["L4", "数据与决策层", "数据中台 · 工艺知识库 · 经营分析 · 碳足迹核算", C.primary, C.white, "P11"],
    ["L3", "AI 应用层", "视觉质检 · 参数寻优 · 智能排产 · 异常预警", C.steel, C.white, "P10"],
    ["L2", "车间执行层 MES", "工单管理 · 排产调度 · 物料追溯 · 质量记录", C.secondary, C.dark, "P9"],
    ["L1", "控制层", "PLC / DCS · SCADA 数据采集 · 设备联网", C.accent, C.dark, "P8"],
    ["L0", "设备层", "混配 / 成型 / 后处理产线 · 传感器 · 检测装置", C.cream, C.dark, "P7"],
  ];
  layers.forEach((l, i) => {
    const y = 1.8 + i * 0.92;
    const indent = i * 0.28;
    s.addShape(p.ShapeType.roundRect, {
      x: 0.6 + indent, y, w: 11.2 - indent, h: 0.8,
      fill: { color: l[3] }, line: { color: C.bg2, width: 1 }, rectRadius: 0.05,
    });
    s.addText(l[0], {
      x: 0.85 + indent, y: y + 0.24, w: 0.6, h: 0.32,
      fontSize: 15, bold: true, color: l[4] === C.white ? C.accent : C.accentDk,
      fontFace: "Cambria", margin: 0,
    });
    s.addText(l[1], {
      x: 1.6 + indent, y: y + 0.24, w: 2.6, h: 0.32,
      fontSize: 13, bold: true, color: l[4], fontFace: "Cambria", margin: 0,
    });
    s.addText(l[2], {
      x: 4.4 + indent, y: y + 0.26, w: 6.2, h: 0.3,
      fontSize: 9.5, color: l[4], fontFace: "Calibri", margin: 0,
    });
    s.addText(l[5], {
      x: 12.0, y: y + 0.26, w: 0.7, h: 0.3,
      fontSize: 9, color: C.grayLt, fontFace: "Calibri", align: "right", margin: 0,
    });
  });

  // 贯穿的两根柱子
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 6.45, w: 5.95, h: 0.62,
    fill: { color: C.white }, line: { color: C.secondary, width: 1.5 }, rectRadius: 0.05,
  });
  s.addText("贯穿 · 网络与安全", {
    x: 0.85, y: 6.58, w: 2.4, h: 0.3,
    fontSize: 10.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText("工业以太网 · 数据安全 · 权限管控", {
    x: 3.3, y: 6.6, w: 3.1, h: 0.28,
    fontSize: 9, color: C.gray, fontFace: "Calibri", margin: 0,
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 6.75, y: 6.45, w: 5.95, h: 0.62,
    fill: { color: C.white }, line: { color: C.secondary, width: 1.5 }, rectRadius: 0.05,
  });
  s.addText("贯穿 · 能碳计量", {
    x: 7.0, y: 6.58, w: 2.4, h: 0.3,
    fontSize: 10.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText("分表分项 · 碳排采集 · 零碳认证支撑", {
    x: 9.3, y: 6.6, w: 3.2, h: 0.28,
    fontSize: 9, color: C.gray, fontFace: "Calibri", margin: 0,
  });

  footer(s);
}

// ═══════════════════════════════ P7 · L0 设备层
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "L0 设备层  ·  产线与装备", "PART 3 · EQUIPMENT");
  subTitle(s, "智能制造的物理基础 —— 设备本身要具备数据接口与自动化能力, 否则上层系统无米可炊");

  const mods = [
    ["01  原料预处理", "稻壳除杂 / 干燥 / 粉碎 / 分级\n在线含水率与粒径检测\n批次信息自动采集", "解决原料波动导致的工艺不稳"],
    ["02  混配与改性", "自动配料与计量\n混炼参数程序化控制\n配方调用与切换", "配方从纸质工单变为系统调用"],
    ["03  成型加工", "注塑 / 挤出 / 热压成型\n模温与压力闭环控制\n模具身份识别与寿命管理", "换型时间与首件合格率的关键"],
    ["04  后处理与包装", "表面处理 / 检测 / 分拣\n自动码垛与标识\n成品条码与批次绑定", "追溯链的最后一环"],
  ];
  mods.forEach((m, i) => {
    const x = 0.6 + (i % 2) * 6.15;
    const y = 1.82 + Math.floor(i / 2) * 2.3;
    s.addShape(p.ShapeType.roundRect, {
      x, y, w: 5.95, h: 2.08,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
    });
    s.addText(m[0], {
      x: x + 0.24, y: y + 0.2, w: 5.5, h: 0.3,
      fontSize: 13, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(m[1], {
      x: x + 0.24, y: y + 0.58, w: 5.5, h: 0.95,
      fontSize: 9.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    });
    s.addShape(p.ShapeType.rect, {
      x: x + 0.24, y: y + 1.6, w: 5.5, h: 0.01, fill: { color: C.bg2 },
    });
    s.addText(m[2], {
      x: x + 0.24, y: y + 1.68, w: 5.5, h: 0.3,
      fontSize: 9, color: C.accentDk, fontFace: "Calibri", margin: 0,
    });
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 6.45, w: 12.1, h: 0.62,
    fill: { color: C.cream }, line: { color: C.accent, width: 1 }, rectRadius: 0.05,
  });
  s.addText(
    "政策对接 · 关键装备若属国内首台(套)可申报 合同额 10% / 最高 2000 万 · 国际首台 20% / 最高 3000 万",
    {
      x: 0.9, y: 6.6, w: 11.5, h: 0.3,
      fontSize: 9.5, color: C.accentDk, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P8 · L1 控制层
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "L1 控制层  ·  设备联网与数据采集", "PART 3 · CONTROL & SCADA");
  subTitle(s, "这一层决定了数据能不能上得来 —— 是后面所有 AI 与优化的前提, 不能省");

  const items = [
    ["设备联网", "各厂商设备统一接入", "混配 / 成型 / 后处理设备来自不同供应商, 协议各异 (Modbus / OPC UA / 私有协议)。统一网关做协议转换, 消除\"数据孤岛\"。老设备可加装采集模块, 不必全部换新。"],
    ["数据采集 SCADA", "实时状态与工艺参数", "采集设备运行状态 · 工艺参数 (温度 / 压力 / 转速 / 时间) · 产量计数 · 报警信息。采集频率按工序需要分级, 关键工序秒级。"],
    ["能耗计量", "分表分项到设备", "电 / 气 / 水分表计量到主要设备或工段, 而非整厂一块表。这是后续核算单位产品能耗与碳足迹的唯一可靠来源。"],
    ["边缘计算", "现场侧实时处理", "视觉质检等对时延敏感的场景在边缘侧完成推理, 不依赖网络回传。断网时产线不停。"],
  ];
  items.forEach((it, i) => {
    const y = 1.8 + i * 1.22;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: 1.1,
      fill: { color: i % 2 ? C.white : C.cream }, line: { color: C.bg2, width: 1 },
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 0.05, h: 1.1, fill: { color: C.accent } });
    s.addText(it[0], {
      x: 0.88, y: y + 0.2, w: 2.2, h: 0.3,
      fontSize: 12.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(it[1], {
      x: 0.88, y: y + 0.58, w: 2.3, h: 0.3,
      fontSize: 9, color: C.accentDk, fontFace: "Calibri", margin: 0,
    });
    s.addText(it[2], {
      x: 3.35, y: y + 0.16, w: 9.1, h: 0.82,
      fontSize: 9.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "middle",
    });
  });

  footer(s);
}

// ═══════════════════════════════ P9 · L2 MES
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "L2 车间执行层  ·  MES", "PART 3 · MES");
  subTitle(s, "让\"计划\"和\"现场\"对上 —— 从纸质工单和口头协调, 变成系统闭环");

  const mes = [
    ["工单与排产", "订单拆解为工单 · 按设备产能与模具约束排产 · 插单与改单可重排"],
    ["生产执行", "工单下发到工位 · 开工/报工/暂停实时记录 · 进度可视"],
    ["物料追溯", "原料批次 → 混配批次 → 成型批次 → 成品条码 · 正查反查双向"],
    ["质量管理", "检验项与判定规则内置 · 不合格品自动拦截 · 缺陷代码统一"],
    ["设备管理", "OEE 统计 · 点检保养计划 · 故障记录与停机分析"],
    ["模具与工装", "模具身份 · 上下模记录 · 使用次数与寿命预警"],
  ];
  mes.forEach((m, i) => {
    const x = 0.6 + (i % 3) * 4.07;
    const y = 1.82 + Math.floor(i / 3) * 1.72;
    s.addShape(p.ShapeType.roundRect, {
      x, y, w: 3.86, h: 1.55,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
    });
    s.addShape(p.ShapeType.rect, { x, y, w: 3.86, h: 0.05, fill: { color: C.secondary } });
    s.addText(m[0], {
      x: x + 0.24, y: y + 0.28, w: 3.4, h: 0.3,
      fontSize: 13, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(m[1], {
      x: x + 0.24, y: y + 0.68, w: 3.4, h: 0.78,
      fontSize: 9.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    });
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.35, w: 12.1, h: 1.25,
    fill: { color: C.primary }, line: { width: 0 }, rectRadius: 0.06,
  });
  s.addText("这一层建成后能回答的问题", {
    x: 0.95, y: 5.55, w: 5, h: 0.3,
    fontSize: 12, bold: true, color: C.accent, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "这批货现在做到哪一道工序?  ·  昨天这台设备为什么停了 3 小时?  ·  客户投诉的这批产品用的是哪批原料、哪套参数?  ·  " +
    "下周能不能接这个急单?  ·  这个月哪条线的稼动率最低、卡在哪?",
    {
      x: 0.95, y: 5.92, w: 11.4, h: 0.6,
      fontSize: 10, color: C.cream, fontFace: "Calibri",
      lineSpacing: 15, margin: 0, valign: "top",
    }
  );
  footer(s);
}

// ═══════════════════════════════ P10 · L3 AI 应用层
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "L3 AI 应用层  ·  从数据到决策", "PART 3 · AI APPLICATIONS");
  subTitle(s, "AI 不是另起炉灶 · 是在 L1/L2 数据之上做优化 —— 按落地难度分期, 先易后难");

  const ai = [
    ["在线视觉质检", "低", C.secondary,
      "产品外观缺陷 (色差 / 气泡 / 裂纹 / 杂质) 在线识别\n从抽检升级到全检 · 缺陷自动分类与拦截",
      "技术成熟 · 需现场采集样本训练"],
    ["工艺参数寻优", "中", C.accent,
      "基于历史批次数据建模, 针对当批原料特性推荐参数\n减少试错批次与调机时间",
      "依赖 L1 数据质量与足够批次积累"],
    ["智能排产", "中", C.accent,
      "综合订单交期 / 模具约束 / 设备状态自动排产\n插单时快速重排并给出影响评估",
      "需 MES 数据完整 · 规则需与现场磨合"],
    ["设备异常预警", "中", C.accent,
      "从振动 / 温度 / 电流趋势识别异常征兆\n从事后维修转向计划性维护",
      "需积累足够故障样本"],
    ["能碳优化", "高", C.redAccent,
      "识别高耗能工序与时段 · 给出用能优化建议\n产品级碳足迹自动核算",
      "需分表计量完备 + 核算方法学确定"],
    ["工艺知识库", "高", C.redAccent,
      "把老师傅经验沉淀为可检索、可推理的知识资产\n新人可查询历史相似工况处置方案",
      "需长期积累 · 是最难但最有价值的一项"],
  ];
  ai.forEach((a, i) => {
    const x = 0.6 + (i % 3) * 4.07;
    const y = 1.82 + Math.floor(i / 3) * 2.35;
    s.addShape(p.ShapeType.roundRect, {
      x, y, w: 3.86, h: 2.12,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
    });
    s.addShape(p.ShapeType.rect, { x, y, w: 3.86, h: 0.05, fill: { color: a[2] } });
    s.addText(a[0], {
      x: x + 0.24, y: y + 0.26, w: 2.6, h: 0.3,
      fontSize: 12.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(`难度 ${a[1]}`, {
      x: x + 2.7, y: y + 0.29, w: 0.95, h: 0.26,
      fontSize: 8.5, bold: true, color: a[2], fontFace: "Calibri",
      align: "right", margin: 0,
    });
    s.addText(a[3], {
      x: x + 0.24, y: y + 0.66, w: 3.4, h: 0.85,
      fontSize: 9, color: C.gray, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "top",
    });
    s.addShape(p.ShapeType.rect, {
      x: x + 0.24, y: y + 1.58, w: 3.4, h: 0.01, fill: { color: C.bg2 },
    });
    s.addText(a[4], {
      x: x + 0.24, y: y + 1.66, w: 3.4, h: 0.4,
      fontSize: 8.5, color: C.grayLt, fontFace: "Calibri",
      lineSpacing: 11, margin: 0, valign: "top",
    });
  });

  s.addText(
    "落地原则 · 先做数据基础好、见效快的 (视觉质检) · 再做需要数据积累的 (参数寻优 / 预警) · 最后做知识沉淀类 —— 不一次性铺开",
    {
      x: 0.6, y: 6.5, w: 12.1, h: 0.3,
      fontSize: 9.5, color: C.accentDk, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P11 · L4 数据与决策层
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "L4 数据与决策层  ·  贯通与沉淀", "PART 3 · DATA PLATFORM");
  subTitle(s, "把各层数据汇到一处 · 让经营层看到真实情况 · 也让数据成为可复用的资产");

  const left = [
    ["数据中台", "统一数据模型与主数据管理 · 打通 L1 设备数据 / L2 生产数据 / ERP 经营数据 · 消除口径不一致"],
    ["经营分析", "成本 / 产能 / 交付 / 质量多维分析 · 管理层看板 · 异常自动提示"],
    ["碳足迹核算", "按产品核算全过程碳排 · 支撑零碳认证与欧盟碳关税(CBAM)等出口合规要求"],
  ];
  const right = [
    ["工艺知识资产", "配方 / 参数 / 处置经验结构化沉淀 · 人员流动不丢失 · 新厂复制时可直接迁移"],
    ["质量追溯档案", "每批产品完整档案 (原料 / 参数 / 检验 / 设备) · 客户审厂与投诉处理有据可查"],
    ["对外数据接口", "向客户开放订单进度与质量数据 · 向园区平台提供协同数据 · 向政府报送合规数据"],
  ];

  [left, right].forEach((col, ci) => {
    col.forEach((item, i) => {
      const x = 0.6 + ci * 6.15;
      const y = 1.82 + i * 1.6;
      s.addShape(p.ShapeType.roundRect, {
        x, y, w: 5.95, h: 1.45,
        fill: { color: ci === 0 ? C.white : C.cream },
        line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
      });
      s.addText(item[0], {
        x: x + 0.24, y: y + 0.22, w: 5.5, h: 0.3,
        fontSize: 12.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
      });
      s.addText(item[1], {
        x: x + 0.24, y: y + 0.62, w: 5.5, h: 0.72,
        fontSize: 9.5, color: C.gray, fontFace: "Calibri",
        lineSpacing: 14, margin: 0, valign: "top",
      });
    });
  });

  s.addText("左 · 面向经营决策", {
    x: 0.6, y: 6.68, w: 5.95, h: 0.3,
    fontSize: 9, color: C.grayLt, fontFace: "Calibri", margin: 0,
  });
  s.addText("右 · 面向资产沉淀与对外协同", {
    x: 6.75, y: 6.68, w: 5.95, h: 0.3,
    fontSize: 9, color: C.grayLt, fontFace: "Calibri", margin: 0,
  });
  footer(s);
}

// ═══════════════════════════════ P12 · 园区协同
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "园区协同  ·  单厂之外的延伸", "PART 4 · PARK SYNERGY");
  subTitle(s, "智能工厂建成后向园区层延伸 · 上下游集聚带来协同效率与政策叠加");

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 1.8, w: 5.95, h: 4.5,
    fill: { color: C.white }, line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
  });
  s.addText("上下游集聚", {
    x: 0.9, y: 2.0, w: 5.3, h: 0.32,
    fontSize: 13, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const targets = [
    ["上游", "稻壳收储与预处理 · 助剂与母粒供应"],
    ["中游", "材料改性 · 模具设计制造 · 表面处理"],
    ["下游", "食品包装 · 家居日用 · 工业件 · 农用制品 · 医疗耗材"],
    ["配套", "检测认证 · 设计服务 · 供应链金融 · 外贸服务"],
  ];
  targets.forEach((t, i) => {
    const y = 2.5 + i * 0.92;
    s.addShape(p.ShapeType.rect, { x: 0.9, y, w: 0.04, h: 0.75, fill: { color: C.accent } });
    s.addText(t[0], {
      x: 1.1, y, w: 0.8, h: 0.28,
      fontSize: 11, bold: true, color: C.accentDk, fontFace: "Calibri", margin: 0,
    });
    s.addText(t[1], {
      x: 1.1, y: y + 0.28, w: 5.1, h: 0.5,
      fontSize: 9.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "top",
    });
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 6.75, y: 1.8, w: 5.95, h: 4.5,
    fill: { color: C.cream }, line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
  });
  s.addText("共享服务  ·  数字化对外输出", {
    x: 7.05, y: 2.0, w: 5.3, h: 0.32,
    fontSize: 13, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const shared = [
    "绿电与零碳认证 —— 中小企业难以单独自建",
    "共享仓储物流 —— 衔接港口与外贸",
    "共享检测认证 —— 复用既有出口认证经验",
    "共享财税 · 人力 · 行政",
    "数字化平台开放 —— 入驻企业接入本厂 MES / 质检能力",
    "供应链金融 —— 对接临港贷款贴息",
  ];
  shared.forEach((t, i) => {
    s.addText("—", {
      x: 7.05, y: 2.55 + i * 0.62, w: 0.3, h: 0.28,
      fontSize: 11, color: C.accent, fontFace: "Calibri", margin: 0,
    });
    s.addText(t, {
      x: 7.4, y: 2.55 + i * 0.62, w: 5.0, h: 0.55,
      fontSize: 9.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "top",
    });
  });

  s.addText(
    "政策联动 · 区内企业互相采购可按发票额 10% 申请补贴 (最高 1000 万/年) —— 集聚度越高政策收益越大",
    {
      x: 0.6, y: 6.45, w: 12.1, h: 0.3,
      fontSize: 9.5, color: C.accentDk, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P13 · 政策对接
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "临港政策对接  ·  按建设内容匹配", "PART 4 · POLICY MAP");
  subTitle(s, "下列为政策公示上限 · 实际额度取决于投资额与认定结果 · 本页不做加总测算", C.redAccent);

  const rows = [
    ["L0-L1  产线与联网改造", "企业技术改造和智能化升级", "重点 5000 万 / 一般 1000 万", "固投比例 ≥ 60%"],
    ["L0  关键装备", "重大技术装备首台(套)", "国内 10% / 2000 万\n国际 20% / 3000 万", "需认定"],
    ["L3  工艺技术攻关", "支持关键核心技术突破", "新增投资 10-30% · 重点 3000 万", "填补国内空白"],
    ["L4  研发机构", "设立研发总部 / 功能性平台", "项目总投资 50% · 最高 1000 万", "重点实验室 / 工程技术中心"],
    ["园区  上下游协同", "区内协同采购", "采购发票额 10% · 1000 万 / 年", "双方无股权关联"],
    ["资金成本", "贷款贴息", "固投贷款利息 50% · 1000 万 / 年", "按实际支付利息"],
    ["整体税负", "企业所得税优惠", "减按 15% · 自设立起 5 年", "需实质性生产研发"],
  ];

  const y0 = 1.78, rh = 0.63;
  s.addShape(p.ShapeType.rect, { x: 0.6, y: y0, w: 12.1, h: 0.38, fill: { color: C.primary } });
  ["对应建设内容", "政策名称", "支持上限", "主要条件"].forEach((h, i) => {
    const xs = [0.78, 3.9, 7.3, 10.4];
    s.addText(h, {
      x: xs[i], y: y0 + 0.08, w: 3, h: 0.24,
      fontSize: 10, bold: true, color: C.white, fontFace: "Calibri", margin: 0,
    });
  });
  rows.forEach((r, i) => {
    const y = y0 + 0.38 + i * rh;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: rh,
      fill: { color: i % 2 ? C.white : C.cream }, line: { color: C.bg2, width: 0.5 },
    });
    const xs = [0.78, 3.9, 7.3, 10.4];
    const ws = [3.0, 3.3, 3.0, 2.2];
    r.forEach((cell, j) => {
      s.addText(cell, {
        x: xs[j], y: y + 0.08, w: ws[j], h: 0.48,
        fontSize: j === 2 ? 9 : 9.2,
        bold: j === 0 || j === 2,
        color: j === 2 ? C.accentDk : (j === 0 ? C.primary : (j === 3 ? C.grayLt : C.dark)),
        fontFace: "Calibri", margin: 0, valign: "middle", lineSpacing: 12,
      });
    });
  });

  sourceNote(s,
    "逐条摘自《上海临港新片区政策情况》p7-p12 · 以临港新片区管委会发布最新版本为准 · " +
    "另有拨改投 / 高企奖励 / 人才政策等未列入",
    6.9);
  footer(s);
}

// ═══════════════════════════════ P14 · 实施路径
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "实施路径  ·  分层推进", "PART 5 · ROADMAP");
  subTitle(s, "自下而上建设 · 每阶段可独立验收并申报政策 · 避免一次性大投入");

  const phases = [
    ["PHASE 1", "基线与设计", "现状调研与基线测评\n(良品率 / OEE / 能耗)\n总体方案与设备选型\n政策预沟通与申报准备", C.primary],
    ["PHASE 2", "L0-L1 建设", "产线建设与设备进场\n设备联网与数据采集\n分表分项能耗计量\n技改与首台套申报", C.white],
    ["PHASE 3", "L2 打通", "MES 上线\n工单 / 追溯 / 质量闭环\n数据贯通验证\n对标 L3 集成级验收", C.white],
    ["PHASE 4", "L3-L4 优化", "视觉质检先行\n参数寻优与排产跟进\n数据中台与碳核算\n关键环节对标 L4", C.white],
  ];
  phases.forEach((ph, i) => {
    const x = 0.6 + i * 3.09;
    const isFirst = i === 0;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 1.85, w: 2.92, h: 3.95,
      fill: { color: ph[4] || (isFirst ? C.primary : C.white) },
      line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
    });
    s.addText(ph[0], {
      x: x + 0.22, y: 2.1, w: 2.5, h: 0.28,
      fontSize: 10, bold: true, color: isFirst ? C.accent : C.accentDk,
      fontFace: "Calibri", charSpacing: 2, margin: 0,
    });
    s.addText(ph[1], {
      x: x + 0.22, y: 2.45, w: 2.5, h: 0.35,
      fontSize: 15, bold: true, color: isFirst ? C.white : C.primary,
      fontFace: "Cambria", margin: 0,
    });
    s.addText(ph[2], {
      x: x + 0.22, y: 3.0, w: 2.5, h: 2.6,
      fontSize: 9.5, color: isFirst ? C.cream : C.gray,
      fontFace: "Calibri", lineSpacing: 17, margin: 0, valign: "top",
    });
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.98, w: 12.1, h: 0.72,
    fill: { color: C.cream }, line: { color: C.accent, width: 1 }, rectRadius: 0.05,
  });
  s.addText(
    "关键前提 · PHASE 1 的基线测评决定了后续所有指标目标值 —— 没有基线就没有可验收的目标, 这一步不能跳过",
    {
      x: 0.9, y: 6.15, w: 11.5, h: 0.4,
      fontSize: 9.5, color: C.accentDk, fontFace: "Calibri", margin: 0,
    }
  );
  s.addText("注 · 各阶段时长需结合用地取得与建设周期确定 · 本页不列具体月份", {
    x: 0.6, y: 6.85, w: 12.1, h: 0.28,
    fontSize: 9, color: C.grayLt, fontFace: "Calibri", margin: 0,
  });
  footer(s);
}

// ═══════════════════════════════ P15 · 风险
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "关键风险与应对", "PART 5 · RISK");
  subTitle(s, "智能制造项目的典型失败模式 · 提前识别并设计规避方案");

  const risks = [
    ["数据基础不牢", "设备不联网或采集不全, 上层 AI 无米之炊 —— 智能制造项目最常见的失败原因", "L1 层足额投入 · 数据完整性作为 PHASE 2 验收硬指标"],
    ["系统建了没人用", "MES 与现场作业习惯脱节, 工人绕开系统操作, 数据失真", "关键用户全程参与设计 · 分阶段试点再推广 · 配套作业规范"],
    ["AI 期望过高", "把 AI 当万能解, 在数据不足时强上高难度场景, 效果不及预期损伤信心", "按难度分期 (P10) · 先做视觉质检等确定性高的 · 用效果说话"],
    ["原料波动大", "稻壳含水率 / 粒径随产地批次波动, 影响工艺稳定性与模型效果", "原料入厂检测标准化 · 参数寻优模型将原料特性作为输入变量"],
    ["政策兑现不确定", "政策为上限值, 实际获批取决于认定结果与年度资金安排", "不按上限做投资回报测算 · 提前与管委会预沟通申报口径"],
  ];
  risks.forEach((r, i) => {
    const y = 1.8 + i * 0.98;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: 0.86,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 },
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 0.05, h: 0.86, fill: { color: C.redAccent } });
    s.addText(r[0], {
      x: 0.85, y: y + 0.13, w: 2.2, h: 0.6,
      fontSize: 11, bold: true, color: C.primary, fontFace: "Cambria",
      margin: 0, valign: "middle",
    });
    s.addText(r[1], {
      x: 3.2, y: y + 0.1, w: 5.0, h: 0.66,
      fontSize: 9, color: C.gray, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "middle",
    });
    s.addText(r[2], {
      x: 8.4, y: y + 0.1, w: 4.1, h: 0.66,
      fontSize: 9, color: C.accentDk, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "middle",
    });
  });
  footer(s);
}

// ═══════════════════════════════ P16 · 下一步
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.primary };
  s.addShape(p.ShapeType.rect, { x: 0, y: 0, w: W, h: 0.12, fill: { color: C.accent } });

  s.addText("下一步", {
    x: 1.1, y: 1.15, w: 11, h: 0.7,
    fontSize: 34, bold: true, color: C.white, fontFace: "Cambria", margin: 0,
  });
  s.addText("稻生万物 · 稻壳生物基材料智能工厂 · 上海临港", {
    x: 1.1, y: 1.92, w: 11, h: 0.4,
    fontSize: 14, color: C.secondary, fontFace: "Calibri", margin: 0,
  });

  const steps = [
    ["01", "现状基线测评", "实地调研产线与现有系统 · 测评良品率 / OEE / 单位产品能耗\n没有基线就没有可验收的目标"],
    ["02", "总体方案深化", "设备选型与联网方案 · MES 选型 · AI 场景优先级排序\n形成可报价的建设清单"],
    ["03", "投资测算", "分层投资额估算 · 按 P13 匹配政策可申报项\n形成投资回报模型"],
    ["04", "政策预沟通", "与临港管委会就技改 / 首台套 / 研发总部申报口径预沟通\n确认认定条件与申报窗口"],
  ];
  steps.forEach((st, i) => {
    const y = 2.75 + i * 1.05;
    s.addShape(p.ShapeType.rect, {
      x: 1.1, y, w: 11.1, h: 0.92,
      fill: { color: C.primaryDk }, line: { width: 0 },
    });
    s.addText(st[0], {
      x: 1.4, y: y + 0.24, w: 0.6, h: 0.4,
      fontSize: 18, bold: true, color: C.accent, fontFace: "Cambria", margin: 0,
    });
    s.addText(st[1], {
      x: 2.15, y: y + 0.16, w: 2.9, h: 0.32,
      fontSize: 13, bold: true, color: C.white, fontFace: "Cambria", margin: 0,
    });
    s.addText(st[2], {
      x: 5.2, y: y + 0.16, w: 6.8, h: 0.65,
      fontSize: 9.5, color: C.cream, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    });
  });
}

p.writeFile({ fileName: "daosheng-v14-smart-manufacturing.pptx" })
  .then(() => console.log("✓ daosheng-v14-smart-manufacturing.pptx  ·  16 页"));
