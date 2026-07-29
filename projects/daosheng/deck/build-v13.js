// 稻生万物 · 智能制造建设方案 · 落地上海临港海洋创新园 · v13 · 16 页
//
// ═══ v13 vs v12 (7/28 鸿波早上与客户沟通后改目标) ═══
//
// 目标整体转向 —— 不是改几页, 是受众和诉求都换了:
//   v12: 报国家数字经济与产业互联基金 · 要投资 · 讲产业集群规划
//   v13: 落地上海临港海洋创新园 · 客户内部/投资人决策用 · 讲智能制造建设方案
//
// 鸿波 7/28 定的三条:
//   1. 受众 = 客户内部 / 投资人 (决策用) —— 语气是"这个项目账算得过来"
//   2. 范围 = 园区层 (零碳制造园 + 上下游集群 + 共享服务), 不是单厂
//   3. 老 PPT 的产能/IPO 数字 **标注来源后引用** (见 P12)
//
// ═══ 两个新抓手 (v12 没用上) ═══
//
// ① 牡蛎壳 ↔ 海洋创新园
//    园区运营方全称"上海临港海洋高新技术产业发展有限公司", 封面 AZURE
//    INNOVATION PARK. 稻壳是农业副产物, 牡蛎壳是**海洋**副产物 —— 海洋生物质
//    材料落海洋园, 逻辑自洽不是硬蹭. 这是 v13 选址论证的主轴.
//
// ② 临港政策包
//    政策数字全部逐条摘自客户提供的《上海临港新片区政策情况》PDF (22 页),
//    每条都在 P5 标了出处页. 不做加总测算 —— 投资额/产能未定, 加总就是编.
//
// ═══ 军规 (延续 v12) ═══
//   - 不点"鲶鱼"品牌名, 用中性"自研 AI 底座"描述
//   - 不写人月工期
//   - 老 PPT 规划口径单独成页 + 标注来源, 不混进方案目标
//   - 投资额 / 用地 / 政策兑现金额一律占位"待测算", 不编
//
// 数据来源:
//   政策 → 上海临港新片区政策情况.pdf (客户 7/27 提供)
//   资产 → 稻壳纤维产业集群介绍0126.pptx (客户原始材料)
//   AI   → v12 P9-P12 (真实代码 audit 得出的 6 Agent 难度分级)

const pptxgen = require("pptxgenjs");

const p = new pptxgen();
p.layout = "LAYOUT_WIDE";
p.title = "稻生万物 · 智能制造建设方案 · 上海临港海洋创新园";
p.author = "稻生万物 (谷千合)";

const C = {
  primary: "2C5F2D", primaryDk: "1F4220",
  secondary: "97BC62", accent: "D4A574", accentDk: "A87F51",
  cream: "F5F1E8", white: "FFFFFF", bg: "FAFAFA",
  bg2: "F0EDE4",
  dark: "1A1A1A", gray: "5C5C5C", grayLt: "999999",
  redAccent: "B85042",
  // v13 新增 · 海洋主题辅色 (呼应 AZURE INNOVATION PARK)
  azure: "1B4F72", azureLt: "2E86AB",
};
const W = 13.3, H = 7.5;
const TOTAL = 16;

let PN = 0;
function nextP() { PN++; return PN; }

function footer(s) {
  s.addText("稻生万物  ·  智能制造建设方案  ·  落地上海临港海洋创新园", {
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

/** 统一的卡片 —— 大量重复版式抽出来 */
function card(s, { x, y, w, h, tag, title, body, fill, tagColor }) {
  s.addShape(p.ShapeType.roundRect, {
    x, y, w, h, fill: { color: fill || C.white },
    line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
  });
  let cy = y + 0.18;
  if (tag) {
    s.addText(tag, {
      x: x + 0.22, y: cy, w: w - 0.44, h: 0.22,
      fontSize: 9, bold: true, color: tagColor || C.accentDk,
      fontFace: "Calibri", charSpacing: 2, margin: 0,
    });
    cy += 0.28;
  }
  if (title) {
    s.addText(title, {
      x: x + 0.22, y: cy, w: w - 0.44, h: 0.3,
      fontSize: 13, bold: true, color: C.primary,
      fontFace: "Cambria", margin: 0,
    });
    cy += 0.36;
  }
  if (body) {
    s.addText(body, {
      x: x + 0.22, y: cy, w: w - 0.44, h: y + h - cy - 0.15,
      fontSize: 9.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    });
  }
}

/** 出处标注 —— 政策/数字页统一在右下角标来源, 免得日后没人记得哪来的 */
function sourceNote(s, text, y) {
  s.addText(text, {
    x: 0.6, y: y || 6.75, w: 12, h: 0.28,
    fontSize: 8, color: C.grayLt, fontFace: "Calibri",
    italic: true, margin: 0,
  });
}

// ═══════════════════════════════════════════════════
// P1 · 封面
// ═══════════════════════════════════════════════════
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.primary };

  s.addShape(p.ShapeType.rect, {
    x: 0, y: 0, w: W, h: 0.12, fill: { color: C.accent },
  });

  s.addText("稻 生 万 物", {
    x: 1.1, y: 2.0, w: 11, h: 1.0,
    fontSize: 50, bold: true, color: C.white,
    fontFace: "Cambria", charSpacing: 8, margin: 0,
  });
  s.addText("智能制造建设方案", {
    x: 1.1, y: 3.05, w: 11, h: 0.6,
    fontSize: 27, color: C.cream, fontFace: "Cambria",
    charSpacing: 3, margin: 0,
  });

  s.addShape(p.ShapeType.rect, {
    x: 1.15, y: 3.85, w: 1.6, h: 0.04, fill: { color: C.accent },
  });

  s.addText("落地  ·  上海临港新片区  海洋创新园", {
    x: 1.1, y: 4.15, w: 11, h: 0.4,
    fontSize: 15, color: C.secondary, fontFace: "Calibri",
    charSpacing: 1, margin: 0,
  });
  s.addText("AZURE  INNOVATION  PARK", {
    x: 1.1, y: 4.55, w: 11, h: 0.3,
    fontSize: 10, color: C.secondary, fontFace: "Calibri",
    charSpacing: 4, margin: 0,
  });

  s.addText("稻壳 + 牡蛎壳双原料复合材料  ·  零碳智能制造园区  ·  2026 年 7 月", {
    x: 1.1, y: 5.9, w: 11, h: 0.3,
    fontSize: 11, color: C.grayLt, fontFace: "Calibri", margin: 0,
  });
}

// ═══════════════════════════════════════════════════
// P2 · 一页看懂 (投资决策要点)
// ═══════════════════════════════════════════════════
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "一页看懂  ·  决策 5 要点", "AT A GLANCE");

  const items = [
    ["01", "项目是什么",
      "以稻壳 + 牡蛎壳双原料复合材料为主业的零碳智能制造园区 · 首期建智能工厂 · 中期招上下游成集群"],
    ["02", "为什么落临港海洋园",
      "牡蛎壳是海洋副产物 · 与海洋创新园产业定位同源 · 叠加小洋山港原料进口与成品出口的物流优势"],
    ["03", "客户的底子",
      "100+ 项专利 (2012-2023) · 德国 DIN / 欧盟 TUV / 美国红点 / 英国皇室创新奖 · 迪士尼 宜家 乐高等品牌客户"],
    ["04", "政策可争取面",
      "临港技改与智能化升级最高 5000 万 · 关键核心技术最高 3000 万 · 研发总部 1000 万 · 所得税减按 15%"],
    ["05", "现阶段待定",
      "投资额 / 用地面积 / 产能规划 / 政策兑现测算 —— 需客户新规划详本确定后补入 (见 P12 P13)"],
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
      fontSize: 13, bold: true, color: C.primary,
      fontFace: "Cambria", margin: 0,
    });
    s.addText(it[2], {
      x: 4.05, y: y + 0.15, w: 8.4, h: 0.62,
      fontSize: 10, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    });
  });

  footer(s);
}

// ═══════════════════════════════════════════════════
// P3 · 目录
// ═══════════════════════════════════════════════════
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "目录", "CONTENTS");

  const parts = [
    ["PART 1", "选址论证  ·  为什么是临港海洋创新园", "P4 - P5"],
    ["PART 2", "项目基础  ·  客户资质与双原料技术", "P6 - P7"],
    ["PART 3", "建设方案  ·  零碳制造园 + 智能工厂 + 集群", "P8 - P11"],
    ["PART 4", "规划口径与测算框架", "P12 - P13"],
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
      fontSize: 11, color: C.grayLt, fontFace: "Calibri",
      align: "right", margin: 0,
    });
    s.addShape(p.ShapeType.rect, {
      x: 1.0, y: y + 0.52, w: 11.6, h: 0.01, fill: { color: C.bg2 },
    });
  });

  footer(s);
}

// ═══════════════════════════════════════════════════
// P4 · 为什么落临港海洋创新园 (选址论证主轴)
// ═══════════════════════════════════════════════════
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "为什么是临港海洋创新园", "PART 1 · WHY LIN-GANG");
  s.addText("选址不是就近原则 · 是产业定位同源 + 物流条件 + 政策强度三者叠加", {
    x: 1.55, y: 1.32, w: 11, h: 0.28,
    fontSize: 10.5, color: C.gray, fontFace: "Calibri", margin: 0,
  });

  // 主论点 · 双原料与海洋园的同源关系
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 1.78, w: 12.1, h: 1.55,
    fill: { color: C.azure }, line: { color: C.azure, width: 0 }, rectRadius: 0.06,
  });
  s.addText("核心逻辑  ·  海洋生物质材料落海洋创新园", {
    x: 0.95, y: 1.98, w: 8, h: 0.32,
    fontSize: 14, bold: true, color: C.white, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "园区运营主体为「上海临港海洋高新技术产业发展有限公司」· 定位海洋高新产业 —— " +
    "稻生万物双原料中的牡蛎壳属海洋渔业副产物, 与园区产业定位天然同源; " +
    "稻壳属农业副产物, 构成「农 + 渔」双副产物综合利用的完整叙事。",
    {
      x: 0.95, y: 2.38, w: 11.4, h: 0.8,
      fontSize: 10.5, color: C.cream, fontFace: "Calibri",
      lineSpacing: 16, margin: 0, valign: "top",
    }
  );

  // 三个支撑面
  const cols = [
    ["01  区位与物流", "小洋山港 · 全球吞吐量最大深水港\n南港 · 整车吞吐量超 200 万辆\n浦东机场 · 全球最大国际机场\n上海东站 · 沿海与沿江高铁交汇\n4 条地铁 (16 号线 / 两港快线等)"],
    ["02  产业与人才", "5 所全日制高校 · 3 个重点院系\n专业覆盖船舶 电力 机械 能源\n人工智能 航天航空\n77 所中小学幼儿园 · 3 所国际学校\n落户 / 安家 / 租购房补贴齐备"],
    ["03  政策强度", "自贸区 + 特殊综保区 + 自主改革\n三重授权\n技改与智能化升级最高 5000 万\n企业所得税减按 15% (5 年)\n详见下页政策清单"],
  ];
  cols.forEach((c, i) => {
    card(s, {
      x: 0.6 + i * 4.07, y: 3.55, w: 3.86, h: 2.75,
      title: c[0], body: c[1], fill: C.white,
    });
  });

  sourceNote(s, "数据来源：《上海临港新片区政策情况》(客户提供) p4-p6 · p17 · p22");
  footer(s);
}

// ═══════════════════════════════════════════════════
// P5 · 临港政策清单 (核心新页)
// ═══════════════════════════════════════════════════
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "可对接的临港政策  ·  按项目环节梳理", "PART 1 · POLICY MAP");
  s.addText(
    "下列为政策文件公示的支持上限 · 实际可争取额度取决于项目投资额与认定结果 · 本页不做加总测算",
    {
      x: 1.55, y: 1.32, w: 11, h: 0.28,
      fontSize: 10.5, color: C.redAccent, fontFace: "Calibri", margin: 0,
    }
  );

  const rows = [
    ["产线建设与技改", "企业技术改造和智能化升级", "重点项目最高 5000 万 · 一般项目最高 1000 万", "固投比例≥60%"],
    ["核心工艺", "支持关键核心技术突破", "新增投资 10-30% · 重点最高 3000 万", "填补国内空白 / 国产替代"],
    ["关键装备", "重大技术装备首台(套)突破", "国际首台 合同额 20% 最高 3000 万\n国内首台 合同额 10% 最高 2000 万", "需认定"],
    ["研发机构", "设立研发总部 / 功能性平台", "项目总投资 50% · 最高 1000 万", "重点实验室 / 工程技术中心等"],
    ["集群招商", "区内协同采购 (前沿产业集群强链)", "采购发票额 10% · 最高 1000 万 / 年", "采购双方无股权关联"],
    ["资金成本", "贷款贴息", "固定资产贷款利息 50% · 最高 1000 万 / 年", "流动资金另计 20% / 100 万"],
    ["税负", "企业所得税优惠", "减按 15% 征收 · 自设立之日起 5 年", "需实质性生产或研发活动"],
    ["早期投入", "拨改投", "新增投资最高 50% · 不超 500 万", "实施周期不超 2 年"],
  ];

  const y0 = 1.75, rh = 0.6;
  // 表头
  s.addShape(p.ShapeType.rect, {
    x: 0.6, y: y0, w: 12.1, h: 0.38, fill: { color: C.primary },
  });
  ["项目环节", "政策名称", "支持上限", "主要条件"].forEach((h, i) => {
    const xs = [0.75, 2.5, 5.9, 10.3];
    s.addText(h, {
      x: xs[i], y: y0 + 0.08, w: 3, h: 0.24,
      fontSize: 10, bold: true, color: C.white, fontFace: "Calibri", margin: 0,
    });
  });

  rows.forEach((r, i) => {
    const y = y0 + 0.38 + i * rh;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: rh,
      fill: { color: i % 2 ? C.white : C.cream },
      line: { color: C.bg2, width: 0.5 },
    });
    s.addText(r[0], {
      x: 0.75, y: y + 0.1, w: 1.7, h: 0.4,
      fontSize: 9.5, bold: true, color: C.primary, fontFace: "Calibri",
      margin: 0, valign: "middle",
    });
    s.addText(r[1], {
      x: 2.5, y: y + 0.1, w: 3.3, h: 0.4,
      fontSize: 9, color: C.dark, fontFace: "Calibri", margin: 0, valign: "middle",
    });
    s.addText(r[2], {
      x: 5.9, y: y + 0.06, w: 4.3, h: 0.48,
      fontSize: 9, bold: true, color: C.accentDk, fontFace: "Calibri",
      margin: 0, valign: "middle", lineSpacing: 12,
    });
    s.addText(r[3], {
      x: 10.3, y: y + 0.1, w: 2.3, h: 0.4,
      fontSize: 8.5, color: C.grayLt, fontFace: "Calibri",
      margin: 0, valign: "middle",
    });
  });

  sourceNote(s,
    "逐条摘自《上海临港新片区政策情况》p7-p12 · 以临港新片区管委会发布最新版本为准 · " +
    "另有人才政策 (落户 / 安家 / 租购房 / 高端人才奖励 4-17%) 见 p14-p15",
    6.85);
  footer(s);
}

// ═══════════════════════════════════════════════════
// P6 · 客户资质
// ═══════════════════════════════════════════════════
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "项目主体  ·  稻生万物的既有基础", "PART 2 · WHO IS DAOSHENG");
  s.addText("智能制造园区的可行性首先取决于主体自身能力 · 以下为已验证的客观资产", {
    x: 1.55, y: 1.32, w: 11, h: 0.28,
    fontSize: 10.5, color: C.gray, fontFace: "Calibri", margin: 0,
  });

  const assets = [
    ["技术积累", "100+", "项专利 (2012-2023)", "稻壳 + 牡蛎壳双原料专利复合工艺 · 国内 / 美国 / 台湾发明专利"],
    ["认证矩阵", "4", "项国际认证与奖项", "德国 DIN 环保 · 欧盟 TUV 环保 · 美国红点设计奖 · 英国皇室创新奖"],
    ["出口合规", "3", "大市场认证", "符合欧盟 / 美国 / 日本出口认证要求"],
    ["客户网络", "7+", "国际品牌客户", "迪士尼 · 哈利波特 · 宜家 · 乐高 · 劳斯莱斯 · ARAMARK · 壳氏唯"],
  ];

  assets.forEach((a, i) => {
    const x = 0.6 + (i % 2) * 6.15;
    const y = 1.85 + Math.floor(i / 2) * 2.35;
    s.addShape(p.ShapeType.roundRect, {
      x, y, w: 5.95, h: 2.1,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
    });
    s.addText(a[0], {
      x: x + 0.3, y: y + 0.22, w: 3, h: 0.28,
      fontSize: 11, bold: true, color: C.accentDk,
      fontFace: "Calibri", charSpacing: 2, margin: 0,
    });
    s.addText(a[1], {
      x: x + 0.3, y: y + 0.58, w: 1.8, h: 0.6,
      fontSize: 34, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(a[2], {
      x: x + 2.0, y: y + 0.78, w: 3.6, h: 0.3,
      fontSize: 11, color: C.gray, fontFace: "Calibri", margin: 0,
    });
    s.addText(a[3], {
      x: x + 0.3, y: y + 1.3, w: 5.35, h: 0.6,
      fontSize: 9.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    });
  });

  sourceNote(s, "数据来源：客户原始材料《稻壳纤维产业集群介绍》p8-p11 · p18");
  footer(s);
}

// ═══════════════════════════════════════════════════
// P7 · 双原料技术
// ═══════════════════════════════════════════════════
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "双原料复合工艺  ·  产线的技术前提", "PART 2 · CORE TECHNOLOGY");
  s.addText("两种天然副产物性质互补 · 复合后性能超越单一材料 · 这是智能产线要固化的工艺基础", {
    x: 1.55, y: 1.32, w: 11, h: 0.28,
    fontSize: 10.5, color: C.gray, fontFace: "Calibri", margin: 0,
  });

  // 稻壳
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 1.8, w: 5.95, h: 2.5,
    fill: { color: C.cream }, line: { color: C.secondary, width: 1.5 }, rectRadius: 0.06,
  });
  s.addText("稻壳  ·  农业副产物", {
    x: 0.9, y: 2.0, w: 5.3, h: 0.35,
    fontSize: 16, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "我国年产约 4000 万吨 · 全球约 1.2 亿吨 · 供应稳定\n" +
    "富含二氧化硅 —— 耐高温 · 绝缘 · 抗菌 · 防腐 · 阻燃 · 防水\n" +
    "非粮食生物基 · 不与口粮争地",
    {
      x: 0.9, y: 2.5, w: 5.35, h: 1.6,
      fontSize: 10, color: C.gray, fontFace: "Calibri",
      lineSpacing: 18, margin: 0, valign: "top",
    }
  );

  // 牡蛎壳
  s.addShape(p.ShapeType.roundRect, {
    x: 6.75, y: 1.8, w: 5.95, h: 2.5,
    fill: { color: "E8F1F5" }, line: { color: C.azureLt, width: 1.5 }, rectRadius: 0.06,
  });
  s.addText("牡蛎壳  ·  海洋渔业副产物", {
    x: 7.05, y: 2.0, w: 5.3, h: 0.35,
    fontSize: 16, bold: true, color: C.azure, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "独特片层纳米复合结构 —— 显著增强材料强度与韧性\n" +
    "天然生物材料 · 优良生物相容性与活性\n" +
    "含甲壳素 · 具驱虫抗菌作用 · 含氨基酸与微量元素",
    {
      x: 7.05, y: 2.5, w: 5.35, h: 1.6,
      fontSize: 10, color: C.gray, fontFace: "Calibri",
      lineSpacing: 18, margin: 0, valign: "top",
    }
  );

  // 下游应用带
  s.addText("复合材料下游应用领域", {
    x: 0.6, y: 4.55, w: 6, h: 0.3,
    fontSize: 12, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const apps = ["餐饮食品包装", "日常用品 家居", "工业件 汽车内饰", "农用地膜 育苗盆", "医疗耗材", "服饰辅料"];
  apps.forEach((a, i) => {
    const x = 0.6 + (i % 6) * 2.03;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 4.95, w: 1.93, h: 0.85,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 }, rectRadius: 0.05,
    });
    s.addText(a, {
      x: x + 0.1, y: 5.12, w: 1.73, h: 0.5,
      fontSize: 9.5, color: C.gray, fontFace: "Calibri",
      align: "center", margin: 0, valign: "middle",
    });
  });
  s.addText(
    "共性属性 · 可堆肥降解 · 非粮食生物基 · 抗菌 · 符合禁塑政策方向",
    {
      x: 0.6, y: 5.95, w: 12.1, h: 0.3,
      fontSize: 9.5, color: C.accentDk, fontFace: "Calibri", margin: 0,
    }
  );

  sourceNote(s, "数据来源：客户原始材料《稻壳纤维产业集群介绍》p6-p7 · p11-p17");
  footer(s);
}

// ═══════════════════════════════════════════════════
// P8 · 零碳制造园总体规划 (园区层 · 本方案主体)
// ═══════════════════════════════════════════════════
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "零碳智能制造园  ·  总体构成", "PART 3 · PARK MASTER PLAN");
  s.addText("园区层规划 · 首期以自建智能工厂为核心 · 中期招上下游企业入驻形成集群", {
    x: 1.55, y: 1.32, w: 11, h: 0.28,
    fontSize: 10.5, color: C.gray, fontFace: "Calibri", margin: 0,
  });

  // 三层结构
  const layers = [
    [C.primary, "核心层  ·  智能工厂", "稻壳 + 牡蛎壳复合材料智能产线 · 数字化车间 · AI 质检 · 中试与小批量试制线", C.white],
    [C.secondary, "协同层  ·  上下游入驻", "上游 原料收储与预处理 (稻壳 / 牡蛎壳)  ·  下游 制品成型与品牌代工 · 区内协同采购可享补贴", C.dark],
    [C.accent, "服务层  ·  共享平台", "共享能源 (绿电 / 零碳认证)  ·  共享仓储物流  ·  共享财税与人力  ·  共享检测认证  ·  数字化管理平台", C.dark],
  ];
  layers.forEach((l, i) => {
    const y = 1.85 + i * 1.28;
    s.addShape(p.ShapeType.roundRect, {
      x: 0.6, y, w: 12.1, h: 1.12,
      fill: { color: l[0] }, line: { width: 0 }, rectRadius: 0.06,
    });
    s.addText(l[1], {
      x: 0.95, y: y + 0.18, w: 3.3, h: 0.35,
      fontSize: 14, bold: true, color: l[3], fontFace: "Cambria", margin: 0,
    });
    s.addText(l[2], {
      x: 4.4, y: y + 0.2, w: 7.9, h: 0.75,
      fontSize: 10, color: l[3], fontFace: "Calibri",
      lineSpacing: 15, margin: 0, valign: "top",
    });
  });

  // 底部 · 零碳属性
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.78, w: 12.1, h: 0.95,
    fill: { color: C.white }, line: { color: C.secondary, width: 1.5 }, rectRadius: 0.06,
  });
  s.addText("零碳定位", {
    x: 0.95, y: 5.98, w: 1.6, h: 0.3,
    fontSize: 12, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "绿能 / 绿电供给  ·  碳足迹核算与认证  ·  生产废料闭环回收  —— " +
    "对齐《关于加快经济社会发展全面绿色转型的意见》与禁塑政策方向, 也是园区招商的差异点",
    {
      x: 2.7, y: 6.0, w: 9.7, h: 0.6,
      fontSize: 9.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    }
  );

  footer(s);
}

// ═══════════════════════════════════════════════════
// P9 · 智能工厂 (核心层展开)
// ═══════════════════════════════════════════════════
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "核心层  ·  智能工厂建设内容", "PART 3 · SMART FACTORY");
  s.addText("对应临港「企业技术改造和智能化升级」与「首台(套)」政策 · 是政策申报的主要载体", {
    x: 1.55, y: 1.32, w: 11, h: 0.28,
    fontSize: 10.5, color: C.gray, fontFace: "Calibri", margin: 0,
  });

  const mods = [
    ["01  产线与装备", "复合材料混配 / 成型 / 后处理产线\n关键装备国产化替代 (对应首台套政策)\n中试线支撑新配方快速验证"],
    ["02  数字化车间", "MES 生产执行 · 排产与工单闭环\n设备联网与 OEE 监控\n能耗与碳排数据采集 (支撑零碳认证)"],
    ["03  AI 质检与工艺", "多模态视觉质检 —— 从抽检升级到全检\n配方参数寻优 · 减少试错批次\n工艺知识沉淀为可复用模型"],
    ["04  仓储物流", "智能仓储与自动搬运\n与小洋山港 / 南港物流衔接\n保税仓储与检测维修可另行申报"],
  ];
  mods.forEach((m, i) => {
    card(s, {
      x: 0.6 + (i % 2) * 6.15, y: 1.85 + Math.floor(i / 2) * 2.3,
      w: 5.95, h: 2.08,
      title: m[0], body: m[1], fill: C.white,
    });
  });

  s.addText(
    "注 · 具体设备清单 / 投资额 / 产能参数需客户新规划详本确定后补入",
    {
      x: 0.6, y: 6.5, w: 12.1, h: 0.3,
      fontSize: 9.5, color: C.redAccent, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════════════════════════
// P10 · 集群招商与共享服务
// ═══════════════════════════════════════════════════
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "协同层与服务层  ·  集群怎么长起来", "PART 3 · CLUSTER & SERVICES");
  s.addText("园区价值不只来自自建产能 · 更来自上下游集聚后的协同效率与政策叠加", {
    x: 1.55, y: 1.32, w: 11, h: 0.28,
    fontSize: 10.5, color: C.gray, fontFace: "Calibri", margin: 0,
  });

  // 左 · 招商对象
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 1.8, w: 5.95, h: 4.5,
    fill: { color: C.white }, line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
  });
  s.addText("招商对象  ·  产业链定位", {
    x: 0.9, y: 2.0, w: 5.3, h: 0.32,
    fontSize: 13, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const targets = [
    ["上游", "稻壳收储与预处理 · 牡蛎壳收储 (对接沿海养殖) · 助剂与母粒供应"],
    ["中游", "复合材料改性 · 模具设计与制造 · 表面处理"],
    ["下游", "食品包装成型 · 家居日用 · 工业件与汽车内饰 · 农用制品 · 医疗耗材"],
    ["配套", "检测认证机构 · 设计服务 · 供应链金融 · 跨境电商与外贸服务"],
  ];
  targets.forEach((t, i) => {
    const y = 2.5 + i * 0.92;
    s.addShape(p.ShapeType.rect, {
      x: 0.9, y, w: 0.04, h: 0.75, fill: { color: C.accent },
    });
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

  // 右 · 共享服务
  s.addShape(p.ShapeType.roundRect, {
    x: 6.75, y: 1.8, w: 5.95, h: 4.5,
    fill: { color: C.cream }, line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
  });
  s.addText("共享服务  ·  入驻企业的降本项", {
    x: 7.05, y: 2.0, w: 5.3, h: 0.32,
    fontSize: 13, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const shared = [
    "绿能绿电与零碳认证 —— 单个中小企业难以自建",
    "共享仓储与物流 —— 衔接小洋山港 / 南港",
    "共享检测与出口认证 —— 复用既有欧盟 / 美国 / 日本认证经验",
    "共享财税 · 人力 · 行政 —— 降低入驻企业管理成本",
    "数字化管理平台 —— 订单 / 仓储 / 财务系统统一接入",
    "供应链金融 —— 对接临港贷款贴息等政策工具",
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
    "政策联动 · 区内企业互相采购可按发票额 10% 申请补贴 (最高 1000 万/年) —— 集聚度越高, 政策收益越大",
    {
      x: 0.6, y: 6.45, w: 12.1, h: 0.3,
      fontSize: 9.5, color: C.accentDk, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════════════════════════
// P11 · AI 数字化底座 (降为支撑章节)
// ═══════════════════════════════════════════════════
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "数字化底座  ·  贯穿园区运营", "PART 3 · AI FOUNDATION");
  s.addText("以自研 AI 底座支撑园区 6 个运营环节 · 按落地难度分期 · 不空喊全面智能化", {
    x: 1.55, y: 1.32, w: 11, h: 0.28,
    fontSize: 10.5, color: C.gray, fontFace: "Calibri", margin: 0,
  });

  const agents = [
    ["原料采购", "稻壳 / 牡蛎壳价格与库存预测 · 供应商比价", "低", C.secondary],
    ["加工生产", "视觉质检 · 配方参数寻优 · 智能排产", "中", C.accent],
    ["入驻企业", "入驻申请与合规预审 · 服务工单自动分派", "低", C.secondary],
    ["客户与订单", "客户需求分层 · 报价辅助 · 定制方案生成", "中", C.accent],
    ["平台与数据", "跨企业数据打通 · 碳足迹自动核算", "高", C.redAccent],
    ["认证与出口", "出口认证材料自动准备 · 法规变更提醒重认证", "中", C.accent],
  ];
  agents.forEach((a, i) => {
    const x = 0.6 + (i % 3) * 4.07;
    const y = 1.85 + Math.floor(i / 3) * 2.2;
    s.addShape(p.ShapeType.roundRect, {
      x, y, w: 3.86, h: 1.98,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
    });
    s.addShape(p.ShapeType.rect, {
      x, y, w: 3.86, h: 0.05, fill: { color: a[3] },
    });
    s.addText(a[0], {
      x: x + 0.25, y: y + 0.28, w: 2.4, h: 0.32,
      fontSize: 13, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(`落地难度 ${a[2]}`, {
      x: x + 2.5, y: y + 0.32, w: 1.2, h: 0.26,
      fontSize: 8.5, bold: true, color: a[3], fontFace: "Calibri",
      align: "right", margin: 0,
    });
    s.addText(a[1], {
      x: x + 0.25, y: y + 0.72, w: 3.4, h: 1.1,
      fontSize: 9.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    });
  });

  s.addText(
    "难度分级依据自研 AI 底座的现有能力评估 (工具调用 / 流程凝固 / 外部系统接入 / 视觉识别) · 先易后难分期实施",
    {
      x: 0.6, y: 6.4, w: 12.1, h: 0.3,
      fontSize: 9.5, color: C.grayLt, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════════════════════════
// P12 · 客户既有规划口径 (★ 标注来源的产能/IPO)
// ═══════════════════════════════════════════════════
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "客户既有规划口径", "PART 4 · EXISTING TARGETS");

  // 醒目的来源声明
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 1.6, w: 12.1, h: 0.72,
    fill: { color: "FDF3F2" }, line: { color: C.redAccent, width: 1.5 }, rectRadius: 0.06,
  });
  s.addText(
    "本页数字引自客户《稻壳纤维产业集群介绍》0126 版材料 —— 系落地临港前的原方案口径。" +
    "落地临港海洋创新园后, 用地 · 产线 · 原料供应链条件均有变化, 相关指标需在新规划详本中重新测算。",
    {
      x: 0.9, y: 1.72, w: 11.5, h: 0.55,
      fontSize: 10, color: C.redAccent, fontFace: "Calibri",
      lineSpacing: 15, margin: 0, valign: "top",
    }
  );

  const phases = [
    ["2026 - 2027", "扩大招商业务及团队", "业务量 5 万吨 / 年\n纯利润不少于 5000 万 / 年"],
    ["2028", "产能爬坡", "产能达到 10 万吨\n纯利润不少于 1 亿 / 年"],
    ["2029", "业务与产能持续提升", "(原材料表述不完整)"],
    ["2030", "规模化与资本化", "产能及销售 100 万吨以上\n打造十亿级产业园 · 并实行 IPO"],
  ];
  phases.forEach((ph, i) => {
    const x = 0.6 + i * 3.09;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 2.6, w: 2.92, h: 2.6,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
    });
    s.addShape(p.ShapeType.rect, {
      x, y: 2.6, w: 2.92, h: 0.05, fill: { color: C.accent },
    });
    s.addText(ph[0], {
      x: x + 0.22, y: 2.85, w: 2.5, h: 0.35,
      fontSize: 15, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(ph[1], {
      x: x + 0.22, y: 3.25, w: 2.5, h: 0.5,
      fontSize: 9.5, color: C.grayLt, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "top",
    });
    s.addText(ph[2], {
      x: x + 0.22, y: 3.9, w: 2.5, h: 1.1,
      fontSize: 10, color: C.gray, fontFace: "Calibri",
      lineSpacing: 15, margin: 0, valign: "top",
    });
  });

  // 需重新测算的项
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.45, w: 12.1, h: 1.2,
    fill: { color: C.cream }, line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
  });
  s.addText("落地临港后需重新测算的项", {
    x: 0.95, y: 5.62, w: 4, h: 0.3,
    fontSize: 12, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "用地面积与厂房形态  ·  单线产能与产线数量  ·  原料到厂半径与供应稳定性 (稻壳产区 / 牡蛎壳养殖区距离)  ·  " +
    "投资强度与分期节奏  ·  达产时间表  ·  临港政策实际可兑现额度",
    {
      x: 0.95, y: 5.98, w: 11.4, h: 0.6,
      fontSize: 9.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    }
  );

  sourceNote(s, "数据来源：《稻壳纤维产业集群介绍》0126 版 p24 · 原文如此", 6.75);
  footer(s);
}

// ═══════════════════════════════════════════════════
// P13 · 政策资金测算框架 (占位)
// ═══════════════════════════════════════════════════
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "政策资金测算框架", "PART 4 · FUNDING FRAMEWORK");
  s.addText("投资额确定后按此框架逐项测算 · 现阶段只给方法与依据 · 不给结论数字", {
    x: 1.55, y: 1.32, w: 11, h: 0.28,
    fontSize: 10.5, color: C.redAccent, fontFace: "Calibri", margin: 0,
  });

  const calc = [
    ["技改与智能化升级", "固定资产投资额 × 政策比例", "重点项目最高 5000 万", "固投占比需 ≥ 60%"],
    ["关键核心技术突破", "研发新增投资 × 10-30%", "重点最高 3000 万", "需认定填补国内空白"],
    ["首台(套)装备", "装备合同金额 × 10-20%", "2000 - 3000 万", "国内 / 国际首台分档"],
    ["研发总部/平台", "机构建设投资 × 50%", "最高 1000 万", "一次性支持"],
    ["区内协同采购", "区内采购发票额 × 10%", "最高 1000 万 / 年", "随集群规模逐年增长"],
    ["贷款贴息", "固定资产贷款利息 × 50%", "最高 1000 万 / 年", "按实际支付利息"],
    ["所得税优惠", "应纳税所得额 × (25% - 15%)", "按年测算", "自设立起 5 年"],
  ];

  const y0 = 1.78, rh = 0.62;
  s.addShape(p.ShapeType.rect, {
    x: 0.6, y: y0, w: 12.1, h: 0.36, fill: { color: C.primary },
  });
  ["测算项", "计算方式", "政策上限", "备注"].forEach((h, i) => {
    const xs = [0.78, 3.7, 7.4, 10.2];
    s.addText(h, {
      x: xs[i], y: y0 + 0.07, w: 3, h: 0.24,
      fontSize: 10, bold: true, color: C.white, fontFace: "Calibri", margin: 0,
    });
  });
  calc.forEach((r, i) => {
    const y = y0 + 0.36 + i * rh;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: rh,
      fill: { color: i % 2 ? C.white : C.cream },
      line: { color: C.bg2, width: 0.5 },
    });
    const xs = [0.78, 3.7, 7.4, 10.2];
    const ws = [2.8, 3.6, 2.7, 2.4];
    r.forEach((cell, j) => {
      s.addText(cell, {
        x: xs[j], y: y + 0.1, w: ws[j], h: 0.42,
        fontSize: 9, bold: j === 0 || j === 2,
        color: j === 2 ? C.accentDk : (j === 0 ? C.primary : C.gray),
        fontFace: "Calibri", margin: 0, valign: "middle",
      });
    });
  });

  s.addText(
    "⚠ 上述为政策公示上限 · 实际获批额度取决于项目认定结果与当年度资金安排 · 不应按上限计入投资回报测算",
    {
      x: 0.6, y: 6.35, w: 12.1, h: 0.3,
      fontSize: 9.5, bold: true, color: C.redAccent, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════════════════════════
// P14 · 实施路径
// ═══════════════════════════════════════════════════
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "实施路径  ·  建设与政策申报双轨", "PART 5 · ROADMAP");
  s.addText("园区建设节奏与政策申报窗口对齐 · 避免错过申报期", {
    x: 1.55, y: 1.32, w: 11, h: 0.28,
    fontSize: 10.5, color: C.gray, fontFace: "Calibri", margin: 0,
  });

  const phases = [
    ["PHASE 1", "落地准备", "选址确认与用地洽谈\n主体注册 (三地合一)\n新规划详本定稿\n政策预沟通与申报准备"],
    ["PHASE 2", "首期建设", "厂房与产线建设\n技改与首台套申报\n研发机构设立申报\n数字化车间同步实施"],
    ["PHASE 3", "投产与招商", "首期投产爬坡\n上下游招商入驻\n区内协同采购政策启动\nAI 低难度场景先上"],
    ["PHASE 4", "集群成型", "共享服务平台完善\n零碳认证落地\nAI 中高难度场景推进\n对外输出园区模式"],
  ];
  phases.forEach((ph, i) => {
    const x = 0.6 + i * 3.09;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 1.85, w: 2.92, h: 3.9,
      fill: { color: i === 0 ? C.primary : C.white },
      line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
    });
    s.addText(ph[0], {
      x: x + 0.22, y: 2.1, w: 2.5, h: 0.28,
      fontSize: 10, bold: true, color: i === 0 ? C.accent : C.accentDk,
      fontFace: "Calibri", charSpacing: 2, margin: 0,
    });
    s.addText(ph[1], {
      x: x + 0.22, y: 2.45, w: 2.5, h: 0.35,
      fontSize: 15, bold: true, color: i === 0 ? C.white : C.primary,
      fontFace: "Cambria", margin: 0,
    });
    s.addText(ph[2], {
      x: x + 0.22, y: 3.0, w: 2.5, h: 2.5,
      fontSize: 9.5, color: i === 0 ? C.cream : C.gray,
      fontFace: "Calibri", lineSpacing: 17, margin: 0, valign: "top",
    });
  });

  s.addText(
    "注 · 各阶段时长需结合用地取得时间与建设周期确定 · 本页不列具体月份",
    {
      x: 0.6, y: 5.95, w: 12.1, h: 0.3,
      fontSize: 9.5, color: C.redAccent, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════════════════════════
// P15 · 风险
// ═══════════════════════════════════════════════════
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "关键风险与应对", "PART 5 · RISK");
  s.addText("坦诚列出需要正视的风险 · 投资决策前应逐条评估", {
    x: 1.55, y: 1.32, w: 11, h: 0.28,
    fontSize: 10.5, color: C.gray, fontFace: "Calibri", margin: 0,
  });

  const risks = [
    ["原料供应半径", "临港地处上海 · 稻壳主产区与牡蛎壳养殖区距离较远 · 运输成本与碳排需核算", "评估近沪原料集散方案 · 或在产区设预处理点后运至临港"],
    ["政策兑现不确定", "政策为上限值 · 实际获批取决于认定结果与年度资金安排", "不按上限做回报测算 · 提前与管委会预沟通口径"],
    ["用地与成本", "临港土地成本与内陆园区存在差距 · 影响单位产能投资强度", "结合楼宇租金补贴等政策 · 评估租建结合方案"],
    ["市场需求节奏", "禁塑政策推进节奏影响替代品需求释放速度", "依托既有品牌客户订单打底 · 分期扩产不一次性铺满"],
    ["技术转化", "实验室工艺到规模化产线存在放大效应", "设中试线验证 · AI 参数寻优缩短调试周期"],
  ];
  risks.forEach((r, i) => {
    const y = 1.8 + i * 0.98;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: 0.86,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 },
    });
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 0.05, h: 0.86, fill: { color: C.redAccent },
    });
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

// ═══════════════════════════════════════════════════
// P16 · 下一步
// ═══════════════════════════════════════════════════
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.primary };

  s.addShape(p.ShapeType.rect, {
    x: 0, y: 0, w: W, h: 0.12, fill: { color: C.accent },
  });

  s.addText("落地上海临港海洋创新园", {
    x: 1.1, y: 1.1, w: 11, h: 0.7,
    fontSize: 32, bold: true, color: C.white, fontFace: "Cambria", margin: 0,
  });
  s.addText("稻生万物 · 稻壳 + 牡蛎壳双原料零碳智能制造园区", {
    x: 1.1, y: 1.85, w: 11, h: 0.4,
    fontSize: 14, color: C.secondary, fontFace: "Calibri", margin: 0,
  });

  const steps = [
    ["01", "补齐规划数据", "新规划详本定稿 —— 用地 / 产能 / 投资额 / 分期节奏\n是后续所有测算的前提"],
    ["02", "选址与政策预沟通", "与临港海洋高新 (港城集团) 洽谈地块与入驻条件\n就技改 / 首台套 / 研发总部申报口径预沟通"],
    ["03", "完成资金测算", "按 P13 框架逐项测算政策可兑现额度\n形成投资回报模型"],
    ["04", "编制正式方案书", "本 PPT 定稿后展开为文本方案书\n用于内部决策与对外报批"],
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

// ═══════════════════════════════════════════════════
p.writeFile({ fileName: "daosheng-v13-lingang.pptx" })
  .then(() => console.log("✓ daosheng-v13-lingang.pptx  ·  16 页"));
