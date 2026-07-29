// 稻生万物研产销一体化产业园 · 建设方案 · v15 · 16 页
//
// ═══ v15 vs v14 (7/28 鸿波第三轮反馈) ═══
//
// 鸿波原话: "我觉得这个过于泛, 应该是以支持稻生万物的基础上来支持上下游的模式,
//            来建立稻生万物研产销一体化产业园的方案"
//
// v14 的问题 —— 确实泛:
//   用 ISA-95 五层架构 + GB/T 39116 成熟度写智能工厂, 任何制造企业都能套用,
//   完全没有稻生万物的特殊性. 那是"一份智能制造方案", 不是"这个项目的方案".
//
// ═══ v15 的内核 · 链主赋能 ═══
//
// "以支持稻生万物为基础来支持上下游" 不是招商话术, 是一个可论证的商业逻辑:
//
//   稻生万物有: 材料技术 (100+ 专利) · 国际认证 (TUV/DIN) · 品牌客户
//               (迪士尼/宜家/乐高) —— 但产能与应用面有限
//   上下游有:   产能 · 细分市场渠道 —— 但没技术 · 没认证 · 进不了大客户
//
//   园区把两边接上: 稻生万物把已有能力**平台化开放**, 上下游用这些能力做自己的
//   生意, 稻生万物拿到稳定原料 / 产能消化 / 平台收入. 双向获益, 不是单向招商.
//
// 这就是"研产销一体化"的真实含义 —— 三个闭环都在园区内完成:
//   研 · 材料研发 (稻生万物) → 应用开发 (下游) → 中试验证 (共享)
//   产 · 原料预处理 (上游) → 材料制造 (稻生万物) → 制品成型 (下游)
//   销 · 品牌客户 (稻生万物既有) → 认证出海 (共享) → 国内渠道 (下游)
//
// ═══ v15 结构变化 ═══
//   新增 P4 链主赋能模式 (核心逻辑页) · P5-P7 研/产/销三闭环各一页
//   新增 P9 入驻企业画像 (招谁 · 给什么 · 要什么)
//   新增 P12 商业模式 (园区怎么赚钱 —— 投资人决策必看, v13/v14 都没有)
//   压缩 v14 的五层架构 → P10 一页 (降为支撑, 不是主体)
//
// ═══ 军规 ═══
//   - 品牌统一用"稻生万物" (鸿波 7/28 定: 不纠结注册名)
//   - 不点"鲶鱼"品牌名
//   - 不写人月工期
//   - 投资额 / 产能 / 收入一律占位"待测算", 不编
//   - 政策数字标出处页, 不做加总
//
// 数据来源:
//   政策 → 上海临港新片区政策情况.pdf p7-p12
//   资产 → 稻壳纤维产业集群介绍0126.pptx p6 p8-p11 p18

const pptxgen = require("pptxgenjs");

const p = new pptxgen();
p.layout = "LAYOUT_WIDE";
p.title = "稻生万物研产销一体化产业园 · 建设方案";
p.author = "稻生万物";

const C = {
  primary: "2C5F2D", primaryDk: "1F4220",
  secondary: "97BC62", accent: "D4A574", accentDk: "A87F51",
  cream: "F5F1E8", white: "FFFFFF", bg: "FAFAFA",
  bg2: "F0EDE4",
  dark: "1A1A1A", gray: "5C5C5C", grayLt: "999999",
  redAccent: "B85042",
  // 研 / 产 / 销 三色
  rd: "5B6C8F", mfg: "8B6F47", sales: "6B8E5A",
};
const W = 13.3, H = 7.5;
const TOTAL = 16;

let PN = 0;
function nextP() { PN++; return PN; }

function footer(s) {
  s.addText("稻生万物研产销一体化产业园  ·  建设方案  ·  上海临港新片区", {
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
    fontSize: 32, bold: true, color: C.accent, fontFace: "Cambria", margin: 0,
  });
  s.addText(kicker, {
    x: 1.55, y: 0.55, w: 8, h: 0.28,
    fontSize: 10, color: C.grayLt, fontFace: "Calibri",
    charSpacing: 4, bold: true, margin: 0,
  });
  s.addText(title, {
    x: 1.55, y: 0.82, w: 11.5, h: 0.5,
    fontSize: 26, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
}

function subTitle(s, text, color) {
  s.addText(text, {
    x: 1.55, y: 1.32, w: 11, h: 0.28,
    fontSize: 10.5, color: color || C.gray, fontFace: "Calibri", margin: 0,
  });
}

function sourceNote(s, text, y) {
  s.addText(text, {
    x: 0.6, y: y || 6.8, w: 12, h: 0.28,
    fontSize: 8, color: C.grayLt, fontFace: "Calibri", italic: true, margin: 0,
  });
}

// ═══════════════════════════════ P1 · 封面
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.primary };
  s.addShape(p.ShapeType.rect, { x: 0, y: 0, w: W, h: 0.12, fill: { color: C.accent } });

  s.addText("稻 生 万 物", {
    x: 1.1, y: 1.85, w: 11, h: 0.95,
    fontSize: 46, bold: true, color: C.white,
    fontFace: "Cambria", charSpacing: 8, margin: 0,
  });
  s.addText("研 产 销 一 体 化 产 业 园", {
    x: 1.1, y: 2.85, w: 11, h: 0.7,
    fontSize: 30, color: C.cream, fontFace: "Cambria",
    charSpacing: 5, margin: 0,
  });

  s.addShape(p.ShapeType.rect, { x: 1.15, y: 3.8, w: 1.6, h: 0.04, fill: { color: C.accent } });

  s.addText("以稻生万物为链主  ·  赋能上下游  ·  稻壳生物基材料产业生态", {
    x: 1.1, y: 4.1, w: 11, h: 0.4,
    fontSize: 15, color: C.secondary, fontFace: "Calibri", charSpacing: 1, margin: 0,
  });
  s.addText("落地  上海临港新片区", {
    x: 1.1, y: 4.55, w: 11, h: 0.35,
    fontSize: 13, color: C.secondary, fontFace: "Calibri", charSpacing: 1, margin: 0,
  });

  s.addText("建设方案  ·  2026 年 7 月", {
    x: 1.1, y: 5.95, w: 11, h: 0.3,
    fontSize: 11, color: C.grayLt, fontFace: "Calibri", margin: 0,
  });
}

// ═══════════════════════════════ P2 · 一页看懂
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "一页看懂  ·  这个园区凭什么成立", "AT A GLANCE");

  const items = [
    ["01", "园区是什么",
      "以稻生万物为链主的稻壳生物基材料产业园 —— 研发 / 制造 / 销售三大能力在园内闭环"],
    ["02", "凭什么招得来人",
      "稻生万物把材料技术 · 国际认证 · 品牌客户渠道开放给入驻企业 —— 入驻方省掉自建研发 / 认证 / 拓客的投入"],
    ["03", "稻生万物图什么",
      "上游锁定原料供应 · 下游消化产能 · 平台侧获得服务收入 —— 不是单向让利, 是双向获益"],
    ["04", "为什么在临港",
      "技改与智能化升级最高 5000 万 · 区内协同采购按发票额 10% 补贴 · 所得税减按 15% · 港口支撑出口"],
    ["05", "现阶段待定",
      "用地规模 / 投资额 / 各板块产能与收入结构 —— 需完成客户调研与测算后补入"],
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
      x: 1.45, y: y + 0.14, w: 2.6, h: 0.32,
      fontSize: 13, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(it[2], {
      x: 4.15, y: y + 0.15, w: 8.3, h: 0.62,
      fontSize: 10, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    });
  });
  footer(s);
}

// ═══════════════════════════════ P3 · 目录
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "目录", "CONTENTS");
  const parts = [
    ["PART 1", "核心模式  ·  链主赋能怎么运转", "P4"],
    ["PART 2", "三大闭环  ·  研 / 产 / 销 分别怎么做", "P5 - P7"],
    ["PART 3", "园区落地  ·  布局 · 招商 · 支撑体系", "P8 - P11"],
    ["PART 4", "商业模式与政策对接", "P12 - P13"],
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

// ═══════════════════════════════ P4 · 链主赋能模式 (★ 核心逻辑页)
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "核心模式  ·  链主赋能", "PART 1 · THE MODEL");
  subTitle(s, "稻生万物把已有能力平台化开放 · 入驻企业省掉重复投入 · 稻生万物获得原料与产能保障 —— 双向获益");

  // 上排 · 稻生万物有什么 → 平台化成什么
  const caps = [
    ["材料技术", "100+ 项专利\n稻壳生物基复合工艺", "研发与中试平台", "入驻企业不必自建实验室"],
    ["国际认证", "欧盟 TUV · 德国 DIN\n符合欧美日出口要求", "共享认证服务", "入驻企业不必重复做出口认证"],
    ["品牌客户", "迪士尼 · 宜家 · 乐高\n劳斯莱斯 · ARAMARK", "客户资源导入", "入驻企业不必从零开拓大客户"],
    ["制造能力", "智能产线\n工艺 know-how", "共享产能 / 代工", "入驻企业不必自建重资产产线"],
  ];
  caps.forEach((c, i) => {
    const x = 0.6 + i * 3.09;
    // 上 · 稻生万物已有
    s.addShape(p.ShapeType.roundRect, {
      x, y: 1.78, w: 2.92, h: 1.32,
      fill: { color: C.primary }, line: { width: 0 }, rectRadius: 0.06,
    });
    s.addText(c[0], {
      x: x + 0.2, y: 1.94, w: 2.5, h: 0.3,
      fontSize: 13, bold: true, color: C.accent, fontFace: "Cambria", margin: 0,
    });
    s.addText(c[1], {
      x: x + 0.2, y: 2.3, w: 2.5, h: 0.7,
      fontSize: 9, color: C.cream, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "top",
    });
    // 箭头
    s.addText("▼", {
      x: x + 1.25, y: 3.16, w: 0.5, h: 0.25,
      fontSize: 11, color: C.accent, fontFace: "Calibri", align: "center", margin: 0,
    });
    // 中 · 平台化
    s.addShape(p.ShapeType.roundRect, {
      x, y: 3.48, w: 2.92, h: 0.6,
      fill: { color: C.accent }, line: { width: 0 }, rectRadius: 0.05,
    });
    s.addText(c[2], {
      x: x + 0.2, y: 3.6, w: 2.5, h: 0.35,
      fontSize: 11.5, bold: true, color: C.white, fontFace: "Cambria",
      align: "center", margin: 0,
    });
    // 箭头
    s.addText("▼", {
      x: x + 1.25, y: 4.14, w: 0.5, h: 0.25,
      fontSize: 11, color: C.accent, fontFace: "Calibri", align: "center", margin: 0,
    });
    // 下 · 入驻企业获得
    s.addShape(p.ShapeType.roundRect, {
      x, y: 4.46, w: 2.92, h: 0.85,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 }, rectRadius: 0.05,
    });
    s.addText(c[3], {
      x: x + 0.2, y: 4.6, w: 2.5, h: 0.6,
      fontSize: 9, color: C.gray, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "top",
    });
  });

  // 底部 · 反向获益
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.55, w: 12.1, h: 1.05,
    fill: { color: C.cream }, line: { color: C.secondary, width: 1.5 }, rectRadius: 0.06,
  });
  s.addText("稻生万物获得什么  ·  这不是单向让利", {
    x: 0.95, y: 5.72, w: 5, h: 0.3,
    fontSize: 12, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "上游入驻 → 稻壳收储稳定, 原料品质可控, 降低采购波动    ·    " +
    "下游入驻 → 材料产能有稳定消化出口, 不必自己找散单    ·    " +
    "平台服务 → 研发 / 认证 / 代工 / 数字化服务形成经常性收入",
    {
      x: 0.95, y: 6.08, w: 11.4, h: 0.45,
      fontSize: 9.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    }
  );

  sourceNote(s, "稻生万物既有资产数据来源：《稻壳纤维产业集群介绍》p8-p11 · p18");
  footer(s);
}

// ═══════════════════════════════ P5 · 研 闭环
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "研  ·  技术从稻生万物流向全园", "PART 2 · R&D LOOP");
  subTitle(s, "稻生万物做材料底层研发 · 入驻企业做应用开发 · 中试线共享 —— 分工不重复投入");

  // 三段流程
  const flow = [
    ["01  材料研发", "稻生万物主导", "配方与改性工艺 · 性能优化\n新牌号开发 · 专利布局", C.primary, C.white],
    ["02  应用开发", "入驻企业主导", "针对细分场景做结构设计\n模具与成型工艺适配", C.rd, C.white],
    ["03  中试验证", "园区共享平台", "小批量试制 · 性能测试\n量产前工艺验证", C.secondary, C.dark],
  ];
  flow.forEach((f, i) => {
    const x = 0.6 + i * 4.15;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 1.8, w: 3.85, h: 2.1,
      fill: { color: f[3] }, line: { width: 0 }, rectRadius: 0.06,
    });
    s.addText(f[0], {
      x: x + 0.25, y: 2.0, w: 3.35, h: 0.32,
      fontSize: 14, bold: true, color: f[4] === C.white ? C.accent : C.accentDk,
      fontFace: "Cambria", margin: 0,
    });
    s.addText(f[1], {
      x: x + 0.25, y: 2.4, w: 3.35, h: 0.28,
      fontSize: 10, color: f[4], fontFace: "Calibri", margin: 0,
    });
    s.addText(f[2], {
      x: x + 0.25, y: 2.78, w: 3.35, h: 0.95,
      fontSize: 9.5, color: f[4], fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    });
    if (i < 2) {
      s.addText("▶", {
        x: x + 3.92, y: 2.7, w: 0.3, h: 0.3,
        fontSize: 13, color: C.accent, fontFace: "Calibri", margin: 0,
      });
    }
  });

  // 研发平台建设内容
  s.addText("研发平台建设内容", {
    x: 0.6, y: 4.15, w: 5, h: 0.3,
    fontSize: 13, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const items = [
    ["材料实验室", "配方开发 · 性能测试\n对应临港「研发总部/功能性平台」政策"],
    ["中试线", "小批量试制 · 放大验证\n入驻企业按需预约使用"],
    ["检测中心", "理化性能 · 降解性 · 安全性\n支撑出口认证送检"],
    ["工艺知识库", "配方与参数结构化沉淀\n新入驻企业可快速上手"],
  ];
  items.forEach((it, i) => {
    const x = 0.6 + i * 3.09;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 4.55, w: 2.92, h: 1.75,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
    });
    s.addText(it[0], {
      x: x + 0.22, y: 4.75, w: 2.5, h: 0.3,
      fontSize: 12, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(it[1], {
      x: x + 0.22, y: 5.15, w: 2.5, h: 1.0,
      fontSize: 9, color: C.gray, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "top",
    });
  });

  s.addText(
    "政策对接 · 研发总部 / 重点实验室 / 工程技术研究中心 可按项目总投资 50% 申请支持, 最高 1000 万",
    {
      x: 0.6, y: 6.48, w: 12.1, h: 0.3,
      fontSize: 9.5, color: C.accentDk, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P6 · 产 闭环
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "产  ·  从稻壳到成品的园内闭环", "PART 2 · MANUFACTURING LOOP");
  subTitle(s, "上游预处理 → 稻生万物制材料 → 下游做制品 · 三段在园内完成, 减少物流与库存");

  // 产业链三段
  const chain = [
    ["上游  ·  入驻企业", "稻壳收储与预处理", "除杂 / 干燥 / 粉碎 / 分级\n按稻生万物工艺标准供料\n就近供应减少运输损耗", C.cream, C.dark],
    ["中游  ·  稻生万物", "生物基材料智能制造", "混配改性 → 造粒 / 板材\n智能产线 + 在线质检\n产能部分自用 部分外供", C.primary, C.white],
    ["下游  ·  入驻企业", "制品成型与品牌代工", "注塑 / 挤出 / 热压成型\n餐饮包装 家居 工业件 农用\n可承接园区导入的品牌订单", C.cream, C.dark],
  ];
  chain.forEach((c, i) => {
    const x = 0.6 + i * 4.15;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 1.8, w: 3.85, h: 2.55,
      fill: { color: c[3] },
      line: { color: i === 1 ? C.primary : C.bg2, width: i === 1 ? 0 : 1 },
      rectRadius: 0.06,
    });
    s.addText(c[0], {
      x: x + 0.25, y: 2.0, w: 3.35, h: 0.28,
      fontSize: 9.5, bold: true, color: i === 1 ? C.accent : C.accentDk,
      fontFace: "Calibri", charSpacing: 1, margin: 0,
    });
    s.addText(c[1], {
      x: x + 0.25, y: 2.34, w: 3.35, h: 0.35,
      fontSize: 13.5, bold: true, color: i === 1 ? C.white : C.primary,
      fontFace: "Cambria", margin: 0,
    });
    s.addText(c[2], {
      x: x + 0.25, y: 2.82, w: 3.35, h: 1.4,
      fontSize: 9.5, color: i === 1 ? C.cream : C.gray, fontFace: "Calibri",
      lineSpacing: 15, margin: 0, valign: "top",
    });
    if (i < 2) {
      s.addText("▶", {
        x: x + 3.92, y: 2.95, w: 0.3, h: 0.3,
        fontSize: 13, color: C.accent, fontFace: "Calibri", margin: 0,
      });
    }
  });

  // 共享制造服务
  s.addText("面向入驻企业的制造服务", {
    x: 0.6, y: 4.6, w: 5, h: 0.3,
    fontSize: 13, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const svc = [
    ["共享产能", "自有产线富余产能对外开放 · 入驻企业订单波动时无需自建冗余"],
    ["代工服务", "承接下游企业的材料加工需求 · 按单收取加工费"],
    ["工艺输出", "把稻生万物的成型参数与经验提供给下游 · 缩短其调试周期"],
    ["质量托底", "统一质检标准与检测能力 · 园区出品对外有一致品质背书"],
  ];
  svc.forEach((v, i) => {
    const x = 0.6 + (i % 2) * 6.15;
    const y = 5.0 + Math.floor(i / 2) * 0.78;
    s.addShape(p.ShapeType.rect, {
      x, y, w: 5.95, h: 0.68,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 },
    });
    s.addText(v[0], {
      x: x + 0.2, y: y + 0.08, w: 1.3, h: 0.5,
      fontSize: 11, bold: true, color: C.accentDk, fontFace: "Calibri",
      margin: 0, valign: "middle",
    });
    s.addText(v[1], {
      x: x + 1.6, y: y + 0.08, w: 4.2, h: 0.52,
      fontSize: 9, color: C.gray, fontFace: "Calibri",
      lineSpacing: 12, margin: 0, valign: "middle",
    });
  });

  s.addText(
    "政策对接 · 智能产线建设可申报「技术改造和智能化升级」重点项目最高 5000 万 · 关键装备可申报首台(套)",
    {
      x: 0.6, y: 6.62, w: 12.1, h: 0.3,
      fontSize: 9.5, color: C.accentDk, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P7 · 销 闭环
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "销  ·  用稻生万物的渠道带上下游出海", "PART 2 · SALES LOOP");
  subTitle(s, "这是园区最难被复制的一环 —— 品牌客户关系与出口认证不是花钱就能买到的");

  // 三条通路
  const ch = [
    ["品牌客户导入", C.sales,
      "稻生万物既有客户：迪士尼 · 哈利波特 · 宜家 · 乐高 · 劳斯莱斯 · ARAMARK",
      "这些客户对供应商有严格准入门槛 (审厂 / 认证 / 一致性)。园区统一满足门槛后, 入驻企业可作为产能承接方进入供应链, 而非各自从零申请。"],
    ["认证共享出海", C.sales,
      "既有认证：欧盟 TUV · 德国 DIN · 符合欧美日出口要求",
      "生物基材料出口的认证成本高、周期长。园区建共享认证服务, 复用稻生万物的认证经验与检测能力, 帮入驻企业以更低成本拿到出口资质。"],
    ["国内渠道协同", C.sales,
      "禁塑政策驱动的替代需求：餐饮 · 商超 · 电商包装 · 农业",
      "入驻企业各自有细分渠道。园区侧做统一品牌背书与集中采购议价, 单个企业接不下的大单可由园区拆分协同交付。"],
  ];
  ch.forEach((c, i) => {
    const y = 1.8 + i * 1.62;
    s.addShape(p.ShapeType.roundRect, {
      x: 0.6, y, w: 12.1, h: 1.48,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 0.05, h: 1.48, fill: { color: c[1] } });
    s.addText(c[0], {
      x: 0.9, y: y + 0.18, w: 2.6, h: 0.32,
      fontSize: 14, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(c[2], {
      x: 0.9, y: y + 0.58, w: 3.4, h: 0.75,
      fontSize: 9, color: C.accentDk, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "top",
    });
    s.addShape(p.ShapeType.rect, {
      x: 4.6, y: y + 0.2, w: 0.01, h: 1.08, fill: { color: C.bg2 },
    });
    s.addText(c[3], {
      x: 4.85, y: y + 0.2, w: 7.6, h: 1.1,
      fontSize: 9.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 15, margin: 0, valign: "middle",
    });
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 6.7, w: 12.1, h: 0.4,
    fill: { color: C.cream }, line: { width: 0 }, rectRadius: 0.04,
  });
  s.addText(
    "⚠ 客户资源导入的具体方式 (是否排他 / 分成比例 / 准入标准) 需稻生万物与既有客户确认后确定",
    {
      x: 0.9, y: 6.76, w: 11.5, h: 0.3,
      fontSize: 9, color: C.redAccent, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P8 · 园区空间布局
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "园区布局  ·  研产销三区", "PART 3 · SPATIAL PLAN");
  subTitle(s, "功能分区对应三大闭环 · 物流动线尽量短 · 具体面积待用地确定后细化");

  const zones = [
    ["研发区", C.rd, "材料实验室 · 中试线 · 检测中心\n设计与应用开发办公\n入驻企业研发工位",
      "临近办公区 · 便于协作"],
    ["制造区", C.mfg, "稻生万物主产线 (混配 / 造粒 / 板材)\n下游制品成型厂房\n上游原料预处理与仓储",
      "按物流动线布置 · 上游近仓储 下游近成品发货"],
    ["服务区", C.sales, "展示中心与客户接待\n共享办公 · 检测认证服务窗口\n物流集散与集中发货",
      "临近园区主入口 · 便于客户到访"],
  ];
  zones.forEach((z, i) => {
    const x = 0.6 + i * 4.15;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 1.8, w: 3.85, h: 3.5,
      fill: { color: C.white }, line: { color: z[1], width: 2 }, rectRadius: 0.06,
    });
    s.addShape(p.ShapeType.rect, { x, y: 1.8, w: 3.85, h: 0.5, fill: { color: z[1] } });
    s.addText(z[0], {
      x: x + 0.25, y: 1.9, w: 3.35, h: 0.32,
      fontSize: 15, bold: true, color: C.white, fontFace: "Cambria", margin: 0,
    });
    s.addText(z[2], {
      x: x + 0.25, y: 2.5, w: 3.35, h: 1.6,
      fontSize: 10, color: C.gray, fontFace: "Calibri",
      lineSpacing: 17, margin: 0, valign: "top",
    });
    s.addShape(p.ShapeType.rect, {
      x: x + 0.25, y: 4.25, w: 3.35, h: 0.01, fill: { color: C.bg2 },
    });
    s.addText(z[3], {
      x: x + 0.25, y: 4.38, w: 3.35, h: 0.7,
      fontSize: 9, color: C.accentDk, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "top",
    });
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.55, w: 12.1, h: 1.05,
    fill: { color: C.cream }, line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
  });
  s.addText("配套与绿色设施", {
    x: 0.95, y: 5.72, w: 4, h: 0.3,
    fontSize: 12, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "绿电供给与屋顶光伏  ·  能碳在线计量与碳足迹核算  ·  废料闭环回收  ·  员工生活配套 (可对接临港人才公寓与租房补贴政策)",
    {
      x: 0.95, y: 6.08, w: 11.4, h: 0.45,
      fontSize: 9.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    }
  );

  s.addText("注 · 各区面积与厂房形态需在用地条件确定后细化", {
    x: 0.6, y: 6.75, w: 12.1, h: 0.28,
    fontSize: 9, color: C.redAccent, fontFace: "Calibri", margin: 0,
  });
  footer(s);
}

// ═══════════════════════════════ P9 · 入驻企业画像
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "招商  ·  招谁 · 给什么 · 要什么", "PART 3 · TENANT PROFILE");
  subTitle(s, "招商不是填满厂房 · 是补齐产业链缺口 —— 每类入驻企业都要说清双方给予关系");

  const rows = [
    ["上游  原料端", "稻壳收储 / 预处理\n助剂与母粒供应",
      "稳定采购订单 · 品质标准与技术指导\n临港区内协同采购补贴",
      "按标准稳定供料 · 优先保障园内需求"],
    ["下游  制品端", "食品包装 / 家居日用\n工业件 / 农用制品 / 医疗耗材",
      "材料稳定供应 · 品牌客户订单导入\n共享认证与检测 · 中试线使用",
      "优先采购园内材料 · 承接协同订单"],
    ["配套  服务端", "模具设计制造 / 表面处理\n检测认证 / 设计服务",
      "稳定的园内业务量 · 集中需求规模效应",
      "响应时效承诺 · 园内优先服务"],
    ["贸易  渠道端", "外贸公司 / 跨境电商\n国内经销渠道",
      "园区统一品牌背书 · 出口认证支撑\n洋山保税相关政策",
      "带来订单 · 反馈市场需求给研发端"],
  ];

  const y0 = 1.78, rh = 1.14;
  s.addShape(p.ShapeType.rect, { x: 0.6, y: y0, w: 12.1, h: 0.38, fill: { color: C.primary } });
  ["类型", "招什么企业", "园区给什么", "对方要承诺什么"].forEach((h, i) => {
    const xs = [0.78, 2.5, 5.9, 9.9];
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
    const xs = [0.78, 2.5, 5.9, 9.9];
    const ws = [1.6, 3.2, 3.8, 2.7];
    r.forEach((cell, j) => {
      s.addText(cell, {
        x: xs[j], y: y + 0.12, w: ws[j], h: rh - 0.24,
        fontSize: j === 0 ? 11 : 9,
        bold: j === 0,
        color: j === 0 ? C.primary : (j === 2 ? C.accentDk : C.gray),
        fontFace: j === 0 ? "Cambria" : "Calibri",
        margin: 0, valign: "middle", lineSpacing: 13,
      });
    });
  });

  footer(s);
}

// ═══════════════════════════════ P10 · 智能制造与数字化支撑
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "支撑体系  ·  智能制造与数字化平台", "PART 3 · ENABLERS");
  subTitle(s, "智能制造是手段不是目的 —— 它要服务于三大闭环的运转效率");

  const left = [
    ["智能产线", "混配 / 成型 / 后处理自动化 · 在线视觉质检从抽检升级全检 · 工艺参数数字化固化"],
    ["设备联网与数采", "各厂商设备统一接入 · 工艺参数与能耗实时采集 · 是所有优化的数据基础"],
    ["MES 车间执行", "工单 / 排产 / 物料追溯 / 质量记录闭环 · 让计划与现场对齐"],
  ];
  const right = [
    ["园区协同平台", "入驻企业订单 / 库存 / 产能可见 · 协同订单自动拆分派工 · 支撑区内协同采购结算"],
    ["共享质量档案", "从原料批次到成品条码全程可追溯 · 客户审厂与投诉处理有据可查"],
    ["能碳与碳足迹", "分表分项计量 · 产品级碳足迹核算 · 支撑零碳认证与出口合规"],
  ];
  [left, right].forEach((col, ci) => {
    s.addText(ci === 0 ? "工厂侧  ·  制造能力" : "园区侧  ·  协同能力", {
      x: 0.6 + ci * 6.15, y: 1.78, w: 5.95, h: 0.3,
      fontSize: 12, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    col.forEach((item, i) => {
      const x = 0.6 + ci * 6.15;
      const y = 2.18 + i * 1.5;
      s.addShape(p.ShapeType.roundRect, {
        x, y, w: 5.95, h: 1.35,
        fill: { color: ci === 0 ? C.white : C.cream },
        line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
      });
      s.addText(item[0], {
        x: x + 0.24, y: y + 0.2, w: 5.5, h: 0.3,
        fontSize: 12.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
      });
      s.addText(item[1], {
        x: x + 0.24, y: y + 0.58, w: 5.5, h: 0.68,
        fontSize: 9.5, color: C.gray, fontFace: "Calibri",
        lineSpacing: 14, margin: 0, valign: "top",
      });
    });
  });

  s.addText(
    "AI 应用按落地难度分期 · 先做视觉质检等确定性高的场景 · 参数寻优与智能排产需数据积累后跟进 · 不一次性铺开",
    {
      x: 0.6, y: 6.72, w: 12.1, h: 0.3,
      fontSize: 9.5, color: C.grayLt, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P11 · 对稻生万物的价值
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "回到起点  ·  园区对稻生万物意味着什么", "PART 3 · VALUE TO ANCHOR");
  subTitle(s, "投资人会问：为什么稻生万物要做园区, 而不是只把自己的厂建好");

  const vals = [
    ["原料端", "从被动采购到主动掌控",
      "稻壳品质随产地批次波动, 是工艺不稳的主因之一。上游入驻后可按稻生万物的标准供料, 原料一致性提升, 直接降低调机成本与废品率。"],
    ["产能端", "从自产自销到产能杠杆",
      "自建产线的产能利用率受自身订单波动影响。下游入驻企业消化一部分产能, 平抑波动; 富余产能还可对外代工创收。"],
    ["技术端", "从单点专利到生态壁垒",
      "专利可以被绕开, 但\"材料 + 应用 + 认证 + 客户\"的组合能力很难被复制。园区把这套组合固化成生态, 提高竞争门槛。"],
    ["收入端", "从卖材料到卖能力",
      "除材料销售外, 增加研发服务 / 代工 / 认证服务 / 平台服务等经常性收入, 收入结构更抗周期。"],
  ];
  vals.forEach((v, i) => {
    const y = 1.8 + i * 1.24;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: 1.12,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 },
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 0.05, h: 1.12, fill: { color: C.accent } });
    s.addText(v[0], {
      x: 0.88, y: y + 0.2, w: 1.3, h: 0.3,
      fontSize: 12, bold: true, color: C.accentDk, fontFace: "Cambria", margin: 0,
    });
    s.addText(v[1], {
      x: 2.3, y: y + 0.18, w: 3.0, h: 0.32,
      fontSize: 12.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(v[2], {
      x: 5.5, y: y + 0.16, w: 6.95, h: 0.85,
      fontSize: 9.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "middle",
    });
  });

  footer(s);
}

// ═══════════════════════════════ P12 · 商业模式
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "商业模式  ·  园区的收入来源", "PART 4 · BUSINESS MODEL");
  subTitle(s, "列出收入结构与成本项 —— 具体金额与占比需完成招商测算后填入", C.redAccent);

  s.addText("收入来源", {
    x: 0.6, y: 1.75, w: 4, h: 0.3,
    fontSize: 13, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const rev = [
    ["材料销售", "稻生万物主营 · 园内下游 + 园外客户", "主营"],
    ["代工加工费", "为下游企业加工材料或半成品", "服务"],
    ["研发与中试服务", "实验室 / 中试线对入驻企业开放收费", "服务"],
    ["认证与检测服务", "共享认证平台 · 检测中心对外服务", "服务"],
    ["园区物业与租金", "厂房 / 办公 / 仓储租赁", "资产"],
    ["平台与数据服务", "协同平台使用费 · 供应链金融服务", "平台"],
  ];
  rev.forEach((r, i) => {
    const x = 0.6 + (i % 2) * 6.15;
    const y = 2.15 + Math.floor(i / 2) * 0.82;
    s.addShape(p.ShapeType.rect, {
      x, y, w: 5.95, h: 0.72,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 },
    });
    s.addShape(p.ShapeType.rect, {
      x, y, w: 0.04, h: 0.72,
      fill: { color: r[2] === "主营" ? C.primary : (r[2] === "资产" ? C.mfg : C.accent) },
    });
    s.addText(r[0], {
      x: x + 0.22, y: y + 0.1, w: 2.0, h: 0.28,
      fontSize: 11.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(r[1], {
      x: x + 0.22, y: y + 0.38, w: 4.6, h: 0.28,
      fontSize: 9, color: C.gray, fontFace: "Calibri", margin: 0,
    });
    s.addText(r[2], {
      x: x + 5.0, y: y + 0.22, w: 0.8, h: 0.28,
      fontSize: 8.5, bold: true, color: C.grayLt, fontFace: "Calibri",
      align: "right", margin: 0,
    });
  });

  s.addText("主要成本与投入", {
    x: 0.6, y: 4.75, w: 4, h: 0.3,
    fontSize: 13, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const cost = [
    "土地与厂房建设", "产线与装备投入", "研发平台建设", "数字化系统",
    "园区运营团队", "招商与市场投入",
  ];
  cost.forEach((c, i) => {
    const x = 0.6 + (i % 3) * 4.07;
    const y = 5.15 + Math.floor(i / 3) * 0.62;
    s.addShape(p.ShapeType.roundRect, {
      x, y, w: 3.86, h: 0.52,
      fill: { color: C.cream }, line: { color: C.bg2, width: 1 }, rectRadius: 0.04,
    });
    s.addText(c, {
      x: x + 0.2, y: y + 0.12, w: 3.5, h: 0.3,
      fontSize: 10, color: C.gray, fontFace: "Calibri", margin: 0,
    });
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 6.45, w: 12.1, h: 0.55,
    fill: { color: "FDF3F2" }, line: { color: C.redAccent, width: 1 }, rectRadius: 0.04,
  });
  s.addText(
    "⚠ 各项收入的规模与占比、投资回收期 —— 需在完成招商意向摸底与产能测算后建立财务模型, 本页只给结构不给数字",
    {
      x: 0.9, y: 6.56, w: 11.5, h: 0.35,
      fontSize: 9.5, color: C.redAccent, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P13 · 政策对接
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "临港政策对接  ·  按园区建设内容匹配", "PART 4 · POLICY MAP");
  subTitle(s, "下列为政策公示上限 · 实际额度取决于投资额与认定结果 · 本页不做加总测算", C.redAccent);

  const rows = [
    ["制造区  智能产线", "企业技术改造和智能化升级", "重点 5000 万 / 一般 1000 万", "固投比例 ≥ 60%"],
    ["制造区  关键装备", "重大技术装备首台(套)", "国内 10%/2000 万 · 国际 20%/3000 万", "需认定"],
    ["研发区  实验室平台", "设立研发总部 / 功能性平台", "项目总投资 50% · 最高 1000 万", "重点实验室等"],
    ["研发区  工艺攻关", "支持关键核心技术突破", "新增投资 10-30% · 重点 3000 万", "填补国内空白"],
    ["招商  上下游协同", "区内协同采购", "采购发票额 10% · 1000 万 / 年", "双方无股权关联"],
    ["运营  资金成本", "贷款贴息", "固投贷款利息 50% · 1000 万 / 年", "按实际支付利息"],
    ["整体  税负", "企业所得税优惠", "减按 15% · 自设立起 5 年", "需实质性生产研发"],
  ];
  const y0 = 1.78, rh = 0.63;
  s.addShape(p.ShapeType.rect, { x: 0.6, y: y0, w: 12.1, h: 0.38, fill: { color: C.primary } });
  ["对应园区板块", "政策名称", "支持上限", "主要条件"].forEach((h, i) => {
    const xs = [0.78, 3.9, 7.3, 10.5];
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
    const xs = [0.78, 3.9, 7.3, 10.5];
    const ws = [3.0, 3.3, 3.1, 2.1];
    r.forEach((cell, j) => {
      s.addText(cell, {
        x: xs[j], y: y + 0.1, w: ws[j], h: 0.44,
        fontSize: 8.8, bold: j === 0 || j === 2,
        color: j === 2 ? C.accentDk : (j === 0 ? C.primary : (j === 3 ? C.grayLt : C.dark)),
        fontFace: "Calibri", margin: 0, valign: "middle", lineSpacing: 12,
      });
    });
  });

  sourceNote(s,
    "逐条摘自《上海临港新片区政策情况》p7-p12 · 以临港新片区管委会发布最新版本为准 · 另有人才与保税区政策未列入",
    6.9);
  footer(s);
}

// ═══════════════════════════════ P14 · 实施路径
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "实施路径  ·  先立主体再长生态", "PART 5 · ROADMAP");
  subTitle(s, "链主先站稳 · 再谈赋能 —— 稻生万物自身产线与研发能力是招商的前提条件");

  const phases = [
    ["PHASE 1", "链主先立起来", "稻生万物产线与研发平台建成投产\n认证与质量体系就位\n没有这一步, 赋能无从谈起", C.primary],
    ["PHASE 2", "首批上下游入驻", "锁定 2-3 家上游原料与下游制品企业\n跑通共享产能与技术输出模式\n验证协同订单可行性", null],
    ["PHASE 3", "服务平台成型", "研发 / 认证 / 检测服务对外开放\n园区协同平台上线\n形成服务性收入", null],
    ["PHASE 4", "生态与外溢", "客户资源导入常态化\n模式对外可复制输出\n评估异地园区可能", null],
  ];
  phases.forEach((ph, i) => {
    const x = 0.6 + i * 3.09;
    const isFirst = i === 0;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 1.85, w: 2.92, h: 3.9,
      fill: { color: isFirst ? C.primary : C.white },
      line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
    });
    s.addText(ph[0], {
      x: x + 0.22, y: 2.1, w: 2.5, h: 0.28,
      fontSize: 10, bold: true, color: isFirst ? C.accent : C.accentDk,
      fontFace: "Calibri", charSpacing: 2, margin: 0,
    });
    s.addText(ph[1], {
      x: x + 0.22, y: 2.45, w: 2.5, h: 0.35,
      fontSize: 14.5, bold: true, color: isFirst ? C.white : C.primary,
      fontFace: "Cambria", margin: 0,
    });
    s.addText(ph[2], {
      x: x + 0.22, y: 3.0, w: 2.5, h: 2.6,
      fontSize: 9.5, color: isFirst ? C.cream : C.gray,
      fontFace: "Calibri", lineSpacing: 17, margin: 0, valign: "top",
    });
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.95, w: 12.1, h: 0.72,
    fill: { color: C.cream }, line: { color: C.accent, width: 1 }, rectRadius: 0.05,
  });
  s.addText(
    "关键判断 · 招商的说服力来自链主的真实能力, 不是政策优惠 —— PHASE 1 做扎实, PHASE 2 才谈得动人",
    {
      x: 0.9, y: 6.12, w: 11.5, h: 0.4,
      fontSize: 9.5, color: C.accentDk, fontFace: "Calibri", margin: 0,
    }
  );
  s.addText("注 · 各阶段时长需结合用地取得与建设周期确定 · 本页不列具体月份", {
    x: 0.6, y: 6.82, w: 12.1, h: 0.28,
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
  subTitle(s, "链主型产业园的特有风险 · 与普通制造项目不同");

  const risks = [
    ["招不来配套企业", "上下游企业不愿放弃自主性入驻 · 或规模太小撑不起园区", "先锁定 2-3 家意向企业再启动建设 · 以订单和技术支持换入驻承诺"],
    ["赋能变成失血", "技术与客户开放后被入驻企业绕开自立门户", "核心配方与工艺不外流 · 客户导入设准入与分成机制 · 用共享产能而非技术转让"],
    ["链主自身跟不上", "稻生万物产能与研发投入不足以支撑全园需求", "PHASE 1 先把自身能力建扎实 · 招商节奏与自身产能匹配"],
    ["原料供应半径", "临港距稻壳主产区较远 · 运输成本与碳排需核算", "评估近沪集散方案 · 或在产区设预处理点后运至园区"],
    ["政策兑现不确定", "政策为上限值 · 实际获批取决于认定结果与年度资金安排", "不按上限做投资回报测算 · 提前与管委会预沟通口径"],
  ];
  risks.forEach((r, i) => {
    const y = 1.78 + i * 0.99;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: 0.87,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 },
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 0.05, h: 0.87, fill: { color: C.redAccent } });
    s.addText(r[0], {
      x: 0.85, y: y + 0.13, w: 2.3, h: 0.6,
      fontSize: 11, bold: true, color: C.primary, fontFace: "Cambria",
      margin: 0, valign: "middle",
    });
    s.addText(r[1], {
      x: 3.3, y: y + 0.1, w: 4.6, h: 0.67,
      fontSize: 8.8, color: C.gray, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "middle",
    });
    s.addText(r[2], {
      x: 8.1, y: y + 0.1, w: 4.4, h: 0.67,
      fontSize: 8.8, color: C.accentDk, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "middle",
    });
  });

  s.addText(
    "「赋能变成失血」是链主型园区最核心的风险 —— 开放的边界需要在招商协议中明确约定",
    {
      x: 0.6, y: 6.78, w: 12.1, h: 0.3,
      fontSize: 9.5, color: C.redAccent, fontFace: "Calibri", margin: 0,
    }
  );
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
  s.addText("稻生万物研产销一体化产业园  ·  上海临港新片区", {
    x: 1.1, y: 1.92, w: 11, h: 0.4,
    fontSize: 14, color: C.secondary, fontFace: "Calibri", margin: 0,
  });

  const steps = [
    ["01", "招商意向摸底", "接触 2-3 家上游原料与下游制品企业 · 验证入驻意愿与条件\n这决定了园区模式能否成立, 比建设方案更优先"],
    ["02", "明确开放边界", "确定技术 / 客户 / 产能的开放范围与对价机制\n避免赋能变成失血 (见 P15)"],
    ["03", "财务模型测算", "按 P12 收入结构建模 · 结合 P13 政策测算可兑现额度\n形成投资回收期与敏感性分析"],
    ["04", "用地与政策沟通", "与临港洽谈地块条件 · 就技改 / 研发总部 / 协同采购申报口径预沟通"],
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

p.writeFile({ fileName: "daosheng-v15-park.pptx" })
  .then(() => console.log("✓ daosheng-v15-park.pptx  ·  16 页"));
