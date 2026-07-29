// 稻生万物研产销一体化产业园 · 数字化技术方案 · v19 · 22 页
//
// ═══ v19 vs v18 (7/28 鸿波第七轮 · 交付边界收缩) ═══
//
// 鸿波: "我不建议直接介入设备侧，这样的压力实在太大"
//
// 这个判断是对的, 设备侧的四类压力我方扛不动也不该扛:
//   ① 现场施工   布线 / 装传感器 / 进车间调试, 要驻场、要安全资质、要配合停线
//   ② 协议长尾   设备来自不同厂商, 私有协议逐个啃, 一台接不通整个项目卡住
//   ③ 责任模糊   产线停了算谁的? 数据不准是设备问题还是采集问题? 扯皮无穷
//   ④ 能力错配   我方是数据与应用侧, 不是工控集成商, 做了也不专业
//
// ═══ v19 的核心改动：在「数据接入层」切一刀 ═══
//
//   线以下（设备 → 数据接入点）  系统集成商 / 设备供应商 实施
//   线本身（接口规范与验收标准）  **我方定义**, 集成商实现, 双方联调
//   线以上（数据平台 → 应用）    **我方**建设
//
// 新增 P4「交付边界与分工」专讲这件事 —— 这不是缩范围, 是把责任划清楚。
// 一份成熟的技术方案本来就该说明谁干什么; 含糊其辞才是给自己埋雷。
//
// 关键设计：**接口规范仍由我方定义**。
//   如果连规范都交出去, 结果就是"数据接进来了但不能用"——
//   点位缺失、频率不够、时间戳不同步, 最后返工的还是我方。
//   所以边界是"不实施"而非"不介入"。
//
// 连带调整:
//   封面      加范围声明
//   P5 架构    边缘层标注「集成商实施」, 与平台/应用层视觉区分
//   P7 接入前提 → 「数据接入规范」, 从"客户要满足"改为三方责任划分
//   P16 建设清单 加「实施方」列, 01-03 标为集成商
//   P15 集成边界 加「实施方」列
//   P21 风险    "设备数据拿不到"的应对改为合同条款 + 验收标准转移风险
//   P20 实施路径 PHASE 1 标明设备侧不占我方排期
//
// ═══ 延续 v18 的结构性决定 ═══
//   ① 场景按研产销分组: 研 2 / 产 2 / 销 2 / 跨 1
//   ② 商业逻辑压成 P2 一页, 完整版见 v15/v16
//   ③ 产线设备选型与工艺不在范围内
//
// ═══ 军规 ═══
//   - 不点"鲶鱼"品牌名 · 不出现"自主研发/自研"
//   - 不写人月工期
//   - 指标基线 [待实测] / 目标 [待定]
//   - 样本量 / 数据量 / 投资额 不编, 写清评估方法代替
//   - 政策数字标出处页, 不做加总
//   - 申报要件标明"以当年申报指南为准"
//
// 数据来源:
//   政策 → 上海临港新片区政策情况.pdf p7-p12
//   背景 → 稻壳纤维产业集群介绍0126.pptx p6 p8-p11 p18

const pptxgen = require("pptxgenjs");

const p = new pptxgen();
p.layout = "LAYOUT_WIDE";
p.title = "稻生万物研产销一体化产业园 · 数字化技术方案";
p.author = "稻生万物";

const C = {
  primary: "2C5F2D", primaryDk: "1F4220",
  secondary: "97BC62", accent: "D4A574", accentDk: "A87F51",
  cream: "F5F1E8", white: "FFFFFF", bg: "FAFAFA", bg2: "F0EDE4",
  dark: "1A1A1A", gray: "5C5C5C", grayLt: "999999",
  redAccent: "B85042",
  steel: "34495E",
  // 研 / 产 / 销 三色 —— 全篇一致, 读者一眼知道当前在哪一段
  rd: "5B6C8F", mfg: "8B6F47", sales: "6B8E5A",
};
const W = 13.3;
const TOTAL = 22;

let PN = 0;
function nextP() { PN++; return PN; }

function footer(s) {
  s.addText("稻生万物研产销一体化产业园  ·  数字化技术方案", {
    x: 0.5, y: 7.15, w: 11, h: 0.3,
    fontSize: 9, color: C.grayLt, fontFace: "Calibri", margin: 0,
  });
  s.addText(`${PN} / ${TOTAL}`, {
    x: W - 1.2, y: 7.15, w: 0.7, h: 0.3,
    fontSize: 9, color: C.grayLt, fontFace: "Calibri", align: "right", margin: 0,
  });
}

function pageTitle(s, title, kicker, tagColor, tagText) {
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
    x: 1.55, y: 0.82, w: 10.3, h: 0.5,
    fontSize: 25, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  // 右上角 研/产/销 色标
  if (tagText) {
    s.addShape(p.ShapeType.roundRect, {
      x: 11.95, y: 0.55, w: 0.75, h: 0.75,
      fill: { color: tagColor }, line: { width: 0 }, rectRadius: 0.08,
    });
    s.addText(tagText, {
      x: 11.95, y: 0.72, w: 0.75, h: 0.42,
      fontSize: 22, bold: true, color: C.white, fontFace: "Cambria",
      align: "center", margin: 0,
    });
  }
}

function subTitle(s, text, color) {
  s.addText(text, {
    x: 1.55, y: 1.32, w: 10.2, h: 0.28,
    fontSize: 10.5, color: color || C.gray, fontFace: "Calibri", margin: 0,
  });
}

/** 场景页四段式：要解决什么 / 实现路径 / 依赖前提 / 分期 */
function scenarioPage(s, { problem, steps, needs, phase, phaseColor }) {
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 1.72, w: 5.3, h: 1.5,
    fill: { color: "FDF3F2" }, line: { color: C.redAccent, width: 1 }, rectRadius: 0.05,
  });
  s.addText("要解决什么", {
    x: 0.85, y: 1.85, w: 4.8, h: 0.26,
    fontSize: 10, bold: true, color: C.redAccent, fontFace: "Calibri",
    charSpacing: 1, margin: 0,
  });
  s.addText(problem, {
    x: 0.85, y: 2.15, w: 4.8, h: 0.95,
    fontSize: 9.5, color: C.gray, fontFace: "Calibri",
    lineSpacing: 14, margin: 0, valign: "top",
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 3.35, w: 5.3, h: 2.35,
    fill: { color: C.cream }, line: { color: C.bg2, width: 1 }, rectRadius: 0.05,
  });
  s.addText("依赖前提", {
    x: 0.85, y: 3.48, w: 4.8, h: 0.26,
    fontSize: 10, bold: true, color: C.accentDk, fontFace: "Calibri",
    charSpacing: 1, margin: 0,
  });
  needs.forEach((n, i) => {
    s.addText("·", {
      x: 0.85, y: 3.8 + i * 0.42, w: 0.15, h: 0.24,
      fontSize: 11, color: C.accent, fontFace: "Calibri", margin: 0,
    });
    s.addText(n, {
      x: 1.05, y: 3.78 + i * 0.42, w: 4.6, h: 0.4,
      fontSize: 9, color: C.gray, fontFace: "Calibri",
      lineSpacing: 12, margin: 0, valign: "top",
    });
  });

  s.addText("实现路径", {
    x: 6.15, y: 1.75, w: 4, h: 0.28,
    fontSize: 11, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  steps.forEach((st, i) => {
    const y = 2.1 + i * 0.92;
    s.addShape(p.ShapeType.rect, {
      x: 6.15, y, w: 6.55, h: 0.82,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 },
    });
    s.addShape(p.ShapeType.rect, { x: 6.15, y, w: 0.05, h: 0.82, fill: { color: C.steel } });
    s.addText(`${i + 1}`, {
      x: 6.35, y: y + 0.26, w: 0.3, h: 0.3,
      fontSize: 13, bold: true, color: C.accent, fontFace: "Cambria", margin: 0,
    });
    s.addText(st[0], {
      x: 6.75, y: y + 0.1, w: 2.5, h: 0.28,
      fontSize: 10.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(st[1], {
      x: 6.75, y: y + 0.38, w: 5.7, h: 0.4,
      fontSize: 8.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 11, margin: 0, valign: "top",
    });
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.85, w: 12.1, h: 0.62,
    fill: { color: C.white }, line: { color: phaseColor || C.secondary, width: 1.5 },
    rectRadius: 0.05,
  });
  s.addText(phase[0], {
    x: 0.85, y: 5.98, w: 2.0, h: 0.3,
    fontSize: 10.5, bold: true, color: phaseColor || C.primary,
    fontFace: "Cambria", margin: 0,
  });
  s.addText(phase[1], {
    x: 2.95, y: 6.0, w: 9.5, h: 0.3,
    fontSize: 9, color: C.gray, fontFace: "Calibri", margin: 0,
  });
}

// ═══════════════════════════════ P1 · 封面
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.primary };
  s.addShape(p.ShapeType.rect, { x: 0, y: 0, w: W, h: 0.12, fill: { color: C.accent } });

  s.addText("数 字 化 技 术 方 案", {
    x: 1.1, y: 1.85, w: 11, h: 1.0,
    fontSize: 42, bold: true, color: C.white,
    fontFace: "Cambria", charSpacing: 6, margin: 0,
  });
  s.addShape(p.ShapeType.rect, { x: 1.15, y: 3.0, w: 1.6, h: 0.04, fill: { color: C.accent } });
  s.addText("稻生万物研产销一体化产业园", {
    x: 1.1, y: 3.3, w: 11, h: 0.45,
    fontSize: 19, color: C.cream, fontFace: "Cambria", charSpacing: 2, margin: 0,
  });
  s.addText("上海临港新片区", {
    x: 1.1, y: 3.8, w: 11, h: 0.35,
    fontSize: 13, color: C.secondary, fontFace: "Calibri", charSpacing: 1, margin: 0,
  });

  // 研产销三色条 —— 封面就点明覆盖三段
  const tags = [["研", "研发数据 · 工艺知识 · 新品协同", C.rd],
                ["产", "生产执行 · 全流程追溯 · 质量工艺", C.mfg],
                ["销", "认证合规 · 碳足迹 · 客户交付", C.sales]];
  tags.forEach((t, i) => {
    const x = 1.1 + i * 3.75;
    s.addShape(p.ShapeType.rect, { x, y: 4.65, w: 3.5, h: 0.72, fill: { color: t[2] } });
    s.addText(t[0], {
      x: x + 0.18, y: 4.82, w: 0.45, h: 0.4,
      fontSize: 20, bold: true, color: C.white, fontFace: "Cambria", margin: 0,
    });
    s.addText(t[1], {
      x: x + 0.72, y: 4.88, w: 2.7, h: 0.3,
      fontSize: 8.5, color: C.white, fontFace: "Calibri", margin: 0,
    });
  });

  s.addText(
    "本方案范围为数据平台层与应用层  ·  设备联网与现场施工由系统集成商实施\n"
    + "本方案提供接口规范、数据标准与验收要求（见 P4）",
    {
      x: 1.1, y: 5.8, w: 11, h: 0.5,
      fontSize: 9.5, color: C.secondary, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    }
  );
  s.addText("2026 年 7 月", {
    x: 1.1, y: 6.2, w: 11, h: 0.3,
    fontSize: 11, color: C.grayLt, fontFace: "Calibri", margin: 0,
  });
}

// ═══════════════════════════════ P2 · 背景 · 数字化在研产销各段承担什么
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "数字化在研产销各段承担什么", "CONTEXT");
  subTitle(s, "园区商业模式与招商规划见《产业园建设方案》· 本页只交代数字化的落点");

  const segs = [
    ["研", C.rd, "材料研发（链主）→ 应用开发（下游）→ 中试验证（共享）",
      ["把配方与工艺经验变成可检索、可受控输出的数字资产",
       "让下游企业的应用需求能结构化流入，开发进度可追踪",
       "中试数据自动回流，量产转移不靠人工抄参数"]],
    ["产", C.mfg, "原料预处理（上游）→ 材料制造（链主）→ 制品成型（下游）",
      ["批次链贯通，从原料到成品可正查反查",
       "质量从抽检升级在线全检，缺陷可关联到工艺参数",
       "能耗分项计量，为碳足迹与成本核算提供底数"]],
    ["销", C.sales, "品牌客户（链主既有）→ 认证出海（共享）→ 国内渠道（下游）",
      ["认证与检测档案结构化，客户审厂材料可快速组包",
       "产品级碳足迹可核算，支撑出口合规要求",
       "订单进度对客户可见，园区内可拆单协同交付"]],
  ];

  segs.forEach((sg, i) => {
    const y = 1.7 + i * 1.72;
    s.addShape(p.ShapeType.roundRect, {
      x: 0.6, y, w: 12.1, h: 1.6,
      fill: { color: C.white }, line: { color: sg[1], width: 1.5 }, rectRadius: 0.06,
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 0.75, h: 1.6, fill: { color: sg[1] } });
    s.addText(sg[0], {
      x: 0.6, y: y + 0.58, w: 0.75, h: 0.45,
      fontSize: 24, bold: true, color: C.white, fontFace: "Cambria",
      align: "center", margin: 0,
    });
    s.addText(sg[2], {
      x: 1.6, y: y + 0.16, w: 10.9, h: 0.3,
      fontSize: 9.5, color: C.accentDk, fontFace: "Calibri", margin: 0,
    });
    s.addShape(p.ShapeType.rect, {
      x: 1.6, y: y + 0.5, w: 10.9, h: 0.01, fill: { color: C.bg2 },
    });
    sg[3].forEach((t, j) => {
      s.addText("—", {
        x: 1.6, y: y + 0.6 + j * 0.32, w: 0.25, h: 0.26,
        fontSize: 9, color: sg[1], fontFace: "Calibri", margin: 0,
      });
      s.addText(t, {
        x: 1.95, y: y + 0.6 + j * 0.32, w: 10.5, h: 0.28,
        fontSize: 9.5, color: C.gray, fontFace: "Calibri", margin: 0,
      });
    });
  });

  s.addText(
    "边界声明 · 产线设备选型、工艺路线、厂房与土建不在本方案范围 —— 本方案给出对产线的数据接入要求（见 P7）",
    {
      x: 0.6, y: 6.85, w: 12.1, h: 0.3,
      fontSize: 9, color: C.accentDk, fontFace: "Calibri", margin: 0,
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
    ["PART 1", "交付边界  ·  谁做什么", "P4", null],
    ["PART 2", "总体设计  ·  架构 / 数据流 / 接入规范", "P5 - P7", null],
    ["PART 3", "研  ·  研发数据与新品协同", "P8 - P9", C.rd],
    ["PART 4", "产  ·  生产执行与质量工艺", "P10 - P11", C.mfg],
    ["PART 5", "销  ·  认证合规与客户交付", "P12 - P13", C.sales],
    ["PART 6", "园区协同 · 集成 · 建设内容 · 指标", "P14 - P17", null],
    ["PART 7", "政策申报  ·  实施  ·  风险", "P18 - P22", null],
  ];
  parts.forEach((pt, i) => {
    const y = 1.82 + i * 0.75;
    if (pt[3]) {
      s.addShape(p.ShapeType.rect, { x: 0.62, y: y - 0.02, w: 0.14, h: 0.42, fill: { color: pt[3] } });
    }
    s.addText(pt[0], {
      x: 1.0, y, w: 1.6, h: 0.3,
      fontSize: 11, bold: true, color: pt[3] || C.accent,
      fontFace: "Calibri", charSpacing: 2, margin: 0,
    });
    s.addText(pt[1], {
      x: 2.8, y: y - 0.04, w: 8, h: 0.4,
      fontSize: 15.5, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(pt[2], {
      x: 11.2, y, w: 1.4, h: 0.3,
      fontSize: 11, color: C.grayLt, fontFace: "Calibri", align: "right", margin: 0,
    });
    s.addShape(p.ShapeType.rect, {
      x: 1.0, y: y + 0.5, w: 11.6, h: 0.01, fill: { color: C.bg2 },
    });
  });
  footer(s);
}

// ═══════════════════════════════ P4 · 交付边界与分工 ★ v19 核心
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "交付边界与分工  ·  谁做什么", "PART 1 · SCOPE & RESPONSIBILITY");
  subTitle(s, "在「数据接入层」切一刀 —— 线以下由系统集成商实施，线以上为本方案范围", C.redAccent);

  const bands = [
    ["应用层", C.secondary, "MES · 质检算法 · 知识库 · 新品协同 · 认证档案 · 订单交付 · 园区协同",
      "本方案范围", true],
    ["数据平台层", C.rd, "时序库 / 关系库 / 对象存储 · 数据治理 · 数据服务 API · 权限与隔离",
      "本方案范围", true],
    ["数据接入层", C.accent, "点位表 · 数据格式与命名标准 · 接口协议 · 时间同步 · 验收标准",
      "★ 我方定义规范，集成商实现，双方联调", "half"],
    ["边缘与网络层", C.steel, "协议网关 · 边缘节点 · 采集代理 · 工业以太网 · 网络隔离",
      "系统集成商实施", false],
    ["设备与现场层", C.mfg, "PLC / 控制器 · 传感器与计量表具 · 相机与光源 · 现场布线与安装",
      "设备供应商 + 系统集成商", false],
  ];

  bands.forEach((b, i) => {
    const y = 1.72 + i * 0.86;
    const ours = b[4] === true;
    const half = b[4] === "half";
    s.addShape(p.ShapeType.roundRect, {
      x: 0.6, y, w: 12.1, h: 0.78,
      fill: { color: ours ? C.white : (half ? "FDF8F0" : C.bg2) },
      line: { color: b[1], width: ours ? 2 : (half ? 2 : 1) },
      rectRadius: 0.05,
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 1.75, h: 0.78, fill: { color: b[1] } });
    s.addText(b[0], {
      x: 0.6, y: y + 0.25, w: 1.75, h: 0.3,
      fontSize: 11.5, bold: true, color: C.white, fontFace: "Cambria",
      align: "center", margin: 0,
    });
    s.addText(b[2], {
      x: 2.55, y: y + 0.26, w: 6.6, h: 0.3,
      fontSize: 8.8, color: ours ? C.gray : C.grayLt, fontFace: "Calibri", margin: 0,
    });
    s.addShape(p.ShapeType.rect, {
      x: 9.35, y: y + 0.16, w: 3.15, h: 0.46,
      fill: { color: ours ? C.primary : (half ? C.accent : C.grayLt) },
    });
    s.addText(b[3], {
      x: 9.4, y: y + 0.24, w: 3.05, h: 0.3,
      fontSize: ours ? 9.5 : 8.2, bold: true, color: C.white,
      fontFace: "Calibri", align: "center", margin: 0,
    });
  });

  // 分界线标注
  s.addShape(p.ShapeType.rect, {
    x: 0.6, y: 4.28, w: 12.1, h: 0.03, fill: { color: C.redAccent },
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 6.14, w: 5.95, h: 0.82,
    fill: { color: C.cream }, line: { color: C.bg2, width: 1 }, rectRadius: 0.05,
  });
  s.addText("为什么设备侧不由本方实施", {
    x: 0.85, y: 6.24, w: 5.4, h: 0.26,
    fontSize: 10, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "现场施工需驻场与安全资质、须配合停线；设备协议长尾需逐个适配；" +
    "产线故障的责任归属难以切割 —— 交由专业集成商承担更可控。",
    {
      x: 0.85, y: 6.5, w: 5.4, h: 0.42,
      fontSize: 8.2, color: C.gray, fontFace: "Calibri",
      lineSpacing: 11, margin: 0, valign: "top",
    }
  );

  s.addShape(p.ShapeType.roundRect, {
    x: 6.75, y: 6.14, w: 5.95, h: 0.82,
    fill: { color: "FDF3F2" }, line: { color: C.redAccent, width: 1.5 }, rectRadius: 0.05,
  });
  s.addText("但接口规范必须由本方定义", {
    x: 7.0, y: 6.24, w: 5.4, h: 0.26,
    fontSize: 10, bold: true, color: C.redAccent, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "规范一并交出去的后果是「数据接进来了但不能用」—— 点位缺失、频率不足、" +
    "时间戳不同步，返工代价仍由应用侧承担。边界是「不实施」不是「不介入」。",
    {
      x: 7.0, y: 6.5, w: 5.4, h: 0.42,
      fontSize: 8.2, color: C.gray, fontFace: "Calibri",
      lineSpacing: 11, margin: 0, valign: "top",
    }
  );

  footer(s);
}
// ═══════════════════════════════ P5 · 总体架构
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "总体架构  ·  组件与部署", "PART 2 · ARCHITECTURE");
  subTitle(s, "应用层按研产销分组，与园区结构同构 · 平台层统一存算 · 边缘层就近处理");

  // 应用层 · 三组
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 1.7, w: 12.1, h: 1.62,
    fill: { color: C.white }, line: { color: C.secondary, width: 1.5 }, rectRadius: 0.06,
  });
  s.addShape(p.ShapeType.rect, { x: 0.6, y: 1.7, w: 1.3, h: 1.62, fill: { color: C.secondary } });
  s.addText("应用层", {
    x: 0.6, y: 2.32, w: 1.3, h: 0.3,
    fontSize: 12, bold: true, color: C.white, fontFace: "Cambria", align: "center", margin: 0,
  });
  const appGroups = [
    ["研", C.rd, ["研发数据管理", "工艺知识库", "新品开发协同"]],
    ["产", C.mfg, ["MES 生产执行", "质量追溯", "在线质检", "能碳管理"]],
    ["销", C.sales, ["认证合规档案", "客户订单协同", "碳足迹报告"]],
  ];
  appGroups.forEach((g, i) => {
    const x = 2.05 + i * 3.58;
    s.addShape(p.ShapeType.rect, { x, y: 1.82, w: 3.42, h: 0.3, fill: { color: g[1] } });
    s.addText(g[0], {
      x: x + 0.1, y: 1.85, w: 0.4, h: 0.24,
      fontSize: 11, bold: true, color: C.white, fontFace: "Cambria", margin: 0,
    });
    g[2].forEach((it, j) => {
      const cy = 2.2 + Math.floor(j / 2) * 0.5;
      const cx = x + (j % 2) * 1.73;
      s.addShape(p.ShapeType.roundRect, {
        x: cx, y: cy, w: 1.66, h: 0.42,
        fill: { color: C.cream }, line: { color: C.bg2, width: 0.5 }, rectRadius: 0.03,
      });
      s.addText(it, {
        x: cx + 0.05, y: cy + 0.06, w: 1.56, h: 0.3,
        fontSize: 8, color: C.dark, fontFace: "Calibri",
        align: "center", valign: "middle", margin: 0,
      });
    });
  });

  // 平台层
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 3.44, w: 12.1, h: 1.28,
    fill: { color: C.white }, line: { color: C.rd, width: 1.5 }, rectRadius: 0.06,
  });
  s.addShape(p.ShapeType.rect, { x: 0.6, y: 3.44, w: 1.3, h: 1.28, fill: { color: C.rd } });
  s.addText("平台层", {
    x: 0.6, y: 3.9, w: 1.3, h: 0.3,
    fontSize: 12, bold: true, color: C.white, fontFace: "Cambria", align: "center", margin: 0,
  });
  ["时序数据库\n(工艺参数/能耗)", "关系数据库\n(工单/批次/订单)", "文档与对象存储\n(图像/报告/证书)",
   "消息队列\n(采集缓冲)", "数据服务 API\n(统一取数)", "身份与权限\n(跨企业隔离)"].forEach((it, i) => {
    const cx = 2.05 + (i % 3) * 3.58;
    const cy = 3.56 + Math.floor(i / 3) * 0.56;
    s.addShape(p.ShapeType.roundRect, {
      x: cx, y: cy, w: 3.42, h: 0.5,
      fill: { color: C.cream }, line: { color: C.bg2, width: 0.5 }, rectRadius: 0.03,
    });
    s.addText(it, {
      x: cx + 0.08, y: cy + 0.04, w: 3.26, h: 0.42,
      fontSize: 8, color: C.dark, fontFace: "Calibri",
      align: "center", valign: "middle", lineSpacing: 10, margin: 0,
    });
  });

  // 边缘层
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 4.84, w: 12.1, h: 0.86,
    fill: { color: C.white }, line: { color: C.mfg, width: 1.5 }, rectRadius: 0.06,
  });
  s.addShape(p.ShapeType.rect, { x: 0.6, y: 4.84, w: 1.3, h: 0.86, fill: { color: C.mfg } });
  s.addText("边缘层", {
    x: 0.6, y: 4.98, w: 1.3, h: 0.3,
    fontSize: 12, bold: true, color: C.white, fontFace: "Cambria", align: "center", margin: 0,
  });
  s.addText("集成商实施", {
    x: 0.6, y: 5.3, w: 1.3, h: 0.24,
    fontSize: 7.5, color: C.cream, fontFace: "Calibri", align: "center", margin: 0,
  });
  ["协议网关\n(OPC UA / Modbus / 私有)", "边缘计算节点\n(视觉推理 / 本地缓存)",
   "采集代理\n(点位轮询 / 事件上报)"].forEach((it, i) => {
    const cx = 2.05 + i * 3.58;
    s.addShape(p.ShapeType.roundRect, {
      x: cx, y: 4.96, w: 3.42, h: 0.62,
      fill: { color: C.cream }, line: { color: C.bg2, width: 0.5 }, rectRadius: 0.03,
    });
    s.addText(it, {
      x: cx + 0.08, y: 5.02, w: 3.26, h: 0.5,
      fontSize: 8, color: C.dark, fontFace: "Calibri",
      align: "center", valign: "middle", lineSpacing: 10, margin: 0,
    });
  });

  // 贯穿 · 园区协同
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.82, w: 12.1, h: 0.5,
    fill: { color: C.primary }, line: { width: 0 }, rectRadius: 0.05,
  });
  s.addText("贯穿  ·  园区协同平台", {
    x: 0.85, y: 5.93, w: 3.2, h: 0.3,
    fontSize: 11, bold: true, color: C.accent, fontFace: "Cambria", margin: 0,
  });
  s.addText("把研产销三段的能力按授权范围向入驻企业开放（见 P14）", {
    x: 4.3, y: 5.95, w: 8.2, h: 0.28,
    fontSize: 9, color: C.cream, fontFace: "Calibri", margin: 0,
  });

  s.addText(
    "设计原则 · 边缘自治：断网时采集与质检不停    ·    单一数据源：应用不直连设备，统一走数据服务 API    ·    边缘层由集成商实施，本方案定接口规范（见 P4）",
    {
      x: 0.6, y: 6.48, w: 12.1, h: 0.3,
      fontSize: 9, color: C.gray, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P6 · 数据流 · 三类数据源
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "数据流  ·  三类数据源汇聚", "PART 2 · DATA FLOW");
  subTitle(s, "研产销各有数据源 · 汇到统一平台后互相支撑 —— 这是「一体化」在数据层的含义");

  const sources = [
    ["研", C.rd, "研发侧", "实验记录与配方版本\n中试试制数据\n检测报告与性能数据\n下游应用需求", "按项目/批次录入\n中试线自动采集"],
    ["产", C.mfg, "生产侧", "工艺参数（温度/压力/转速/时间）\n设备状态与报警\n质检结果与缺陷图像\n分项能耗读数", "关键工序秒级\n一般工序分钟级"],
    ["销", C.sales, "销售侧", "订单与交付记录\n客户要求与审厂标准\n认证证书与有效期\n投诉与退货记录", "业务系统同步\n人工维护为主"],
  ];
  sources.forEach((sc, i) => {
    const x = 0.6 + i * 4.13;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 1.72, w: 3.9, h: 2.35,
      fill: { color: C.white }, line: { color: sc[1], width: 1.5 }, rectRadius: 0.06,
    });
    s.addShape(p.ShapeType.rect, { x, y: 1.72, w: 3.9, h: 0.42, fill: { color: sc[1] } });
    s.addText(sc[0], {
      x: x + 0.18, y: 1.79, w: 0.4, h: 0.3,
      fontSize: 14, bold: true, color: C.white, fontFace: "Cambria", margin: 0,
    });
    s.addText(sc[2], {
      x: x + 0.7, y: 1.82, w: 2.5, h: 0.26,
      fontSize: 10, color: C.white, fontFace: "Calibri", margin: 0,
    });
    s.addText(sc[3], {
      x: x + 0.22, y: 2.24, w: 3.5, h: 1.15,
      fontSize: 8.8, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    });
    s.addShape(p.ShapeType.rect, {
      x: x + 0.22, y: 3.5, w: 3.5, h: 0.01, fill: { color: C.bg2 },
    });
    s.addText(sc[4], {
      x: x + 0.22, y: 3.6, w: 3.5, h: 0.42,
      fontSize: 8, color: C.accentDk, fontFace: "Calibri",
      lineSpacing: 11, margin: 0, valign: "top",
    });
    s.addText("▼", {
      x: x + 1.8, y: 4.12, w: 0.3, h: 0.25,
      fontSize: 11, color: C.accent, fontFace: "Calibri", margin: 0,
    });
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 4.45, w: 12.1, h: 0.55,
    fill: { color: C.primary }, line: { width: 0 }, rectRadius: 0.05,
  });
  s.addText("统一数据平台  ·  主数据一致 / 批次为主线串联三类数据", {
    x: 0.6, y: 4.58, w: 12.1, h: 0.3,
    fontSize: 12, bold: true, color: C.white, fontFace: "Cambria",
    align: "center", margin: 0,
  });

  // 三个"互相支撑"的例子 —— 说明为什么要汇到一起
  s.addText("汇到一起之后能做什么（这是「一体化」的实际收益）", {
    x: 0.6, y: 5.15, w: 8, h: 0.28,
    fontSize: 10.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const combos = [
    ["销 → 研", "客户投诉关联到批次与配方版本，反馈回研发形成改进闭环"],
    ["产 → 销", "批次链 + 能耗数据算出产品碳足迹，支撑出口合规与客户审厂"],
    ["研 → 产", "中试确认的参数直接生成量产工艺卡，减少转产偏差与试错"],
  ];
  combos.forEach((cb, i) => {
    const x = 0.6 + i * 4.13;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 5.5, w: 3.9, h: 1.0,
      fill: { color: C.cream }, line: { color: C.bg2, width: 1 }, rectRadius: 0.05,
    });
    s.addText(cb[0], {
      x: x + 0.2, y: 5.62, w: 3.5, h: 0.28,
      fontSize: 11, bold: true, color: C.accentDk, fontFace: "Cambria", margin: 0,
    });
    s.addText(cb[1], {
      x: x + 0.2, y: 5.92, w: 3.5, h: 0.5,
      fontSize: 8.8, color: C.gray, fontFace: "Calibri",
      lineSpacing: 12, margin: 0, valign: "top",
    });
  });

  s.addText(
    "⚠ 采集点位数、采样频率、图像分辨率决定存储与带宽选型，进而决定投资额 —— 需设备选型后按工序梳理点位表再核算。点位表是数字化的第一份交付物。",
    {
      x: 0.6, y: 6.68, w: 12.1, h: 0.3,
      fontSize: 8.5, color: C.redAccent, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P7 · 数据接入规范与责任划分
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "数据接入规范  ·  三方责任划分", "PART 2 · DATA INTERFACE SPEC");
  subTitle(s, "本方案定义「接进来的数据要长什么样」· 实施由集成商承担 · 前提条件由建设方保障", C.redAccent);

  const rows = [
    ["点位表", "本方定义模板\n集成商填报", "设备 / 参数名 / 数据类型 / 量程 / 精度 / 采样频率 / 单位",
      "缺一项则该点位数据不可用"],
    ["命名与编码标准", "本方定义", "设备编码、参数命名、批次编码规则统一，跨系统一致",
      "命名不统一是后期数据治理最大成本"],
    ["时间同步", "本方提要求\n集成商实施", "全场统一时钟源，采集时间戳误差在可接受范围内",
      "时间不同步则参数与质检结果无法对齐"],
    ["数据格式与协议", "本方定义接口\n集成商对接", "上传格式、消息结构、断点续传与补传机制",
      "决定断网后数据能否恢复"],
    ["数据质量验收", "本方定验收标准\n双方联调", "完整性（缺失率）、及时性（时延）、准确性（比对校验）",
      "验收不过则不进入应用层建设"],
    ["设备接口开放", "建设方在采购合同中约定", "PLC 读取权限、协议文档、点位地址表",
      "★ 合同未约定则后期难以补救"],
    ["现场条件", "建设方保障", "工业网络布线、机柜位置、供电散热、计量表具安装位",
      "属土建与产线建设范围"],
    ["既有系统接口", "建设方推动供应商配合", "ERP 物料 / 订单 / 成本科目的读取接口",
      "常见排期风险点"],
  ];

  const y0 = 1.72, rh = 0.6;
  s.addShape(p.ShapeType.rect, { x: 0.6, y: y0, w: 12.1, h: 0.34, fill: { color: C.primary } });
  ["规范项", "责任方", "内容要求", "不满足的后果"].forEach((h, i) => {
    const xs = [0.78, 2.75, 4.9, 9.6];
    s.addText(h, {
      x: xs[i], y: y0 + 0.05, w: 3, h: 0.24,
      fontSize: 9.5, bold: true, color: C.white, fontFace: "Calibri", margin: 0,
    });
  });
  rows.forEach((r, i) => {
    const y = y0 + 0.34 + i * rh;
    const isOurs = r[1].startsWith("本方");
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: rh,
      fill: { color: i % 2 ? C.white : C.cream }, line: { color: C.bg2, width: 0.5 },
    });
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 0.05, h: rh,
      fill: { color: isOurs ? C.accent : C.grayLt },
    });
    s.addText(r[0], {
      x: 0.78, y: y + 0.16, w: 1.9, h: 0.28,
      fontSize: 9.8, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(r[1], {
      x: 2.75, y: y + 0.1, w: 2.05, h: 0.42,
      fontSize: 8, bold: isOurs, color: isOurs ? C.accentDk : C.grayLt,
      fontFace: "Calibri", lineSpacing: 10, margin: 0, valign: "middle",
    });
    s.addText(r[2], {
      x: 4.9, y: y + 0.16, w: 4.6, h: 0.3,
      fontSize: 8.4, color: C.gray, fontFace: "Calibri", margin: 0,
    });
    s.addText(r[3], {
      x: 9.6, y: y + 0.16, w: 2.95, h: 0.3,
      fontSize: 8.2, color: C.redAccent, fontFace: "Calibri", margin: 0,
    });
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 6.62, w: 12.1, h: 0.5,
    fill: { color: "FDF3F2" }, line: { color: C.redAccent, width: 1 }, rectRadius: 0.04,
  });
  s.addText(
    "★ 建议：把本页的点位表模板、命名标准与数据质量验收标准，作为附件写入产线设备与系统集成商的采购合同 —— 这是成本最低的风险控制手段",
    {
      x: 0.9, y: 6.73, w: 11.5, h: 0.3,
      fontSize: 9, color: C.redAccent, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P8 · 研 · 研发数据管理与工艺知识库
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "研发数据管理与工艺知识库", "PART 3 · R&D DATA", C.rd, "研");
  subTitle(s, "链主要向下游输出技术 —— 前提是自己的技术先变成可检索、可受控输出的资产");

  scenarioPage(s, {
    problem: "实验记录散在个人电脑与纸质本；配方版本混乱，不确定哪一版是当前生效版；" +
      "老师傅的调参经验没有沉淀，离岗即断层；下游企业问技术问题只能找人口头答。",
    steps: [
      ["实验记录结构化", "原料特性 / 配方 / 工艺条件 / 测试结果四段式统一模板，替代自由格式文档"],
      ["配方版本管理", "版本链、变更原因、审批记录；明确当前生效版本，避免用错版本试制"],
      ["检测数据归集", "理化 / 降解性 / 安全性检测报告与具体配方版本绑定，不再散落"],
      ["知识检索", "按原料特性、目标性能、应用场景反查历史方案与相似工况处置记录"],
      ["受控输出", "向下游开放的技术资料按权限分级 —— 给参数区间与应用指导，核心配方不外流"],
    ],
    needs: [
      "研发团队改变记录习惯 —— 这是最大阻力，需管理层推动而非技术手段解决",
      "既有实验记录与配方文档的整理归集（纸质需数字化）",
      "配方保密分级由管理层确定：哪些可对下游开放、开放到什么颗粒度",
      "与检测中心的数据对接，避免报告二次录入",
    ],
    phase: ["PHASE 1 · 可先行", "不依赖产线投产，可与产线建设并行启动；越早开始沉淀的历史数据越多"],
    phaseColor: C.rd,
  });
  footer(s);
}

// ═══════════════════════════════ P9 · 研 · 新品开发协同
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "新品开发协同", "PART 3 · NPD COLLABORATION", C.rd, "研");
  subTitle(s, "支撑「研」闭环的三段分工：链主做材料底层、下游做应用开发、中试线共享验证");

  scenarioPage(s, {
    problem: "下游企业的应用需求靠口头与邮件传递，条件不全反复确认；" +
      "需求→配方→试制→量产链路断裂，进度不透明；" +
      "失败的尝试没有归档，不同项目重复踩同样的坑。",
    steps: [
      ["需求结构化受理", "应用场景 / 性能要求 / 成本约束 / 认证要求统一表单，缺项无法提交"],
      ["阶段门管理", "需求评审 → 配方设计 → 小试 → 中试 → 量产验证，每关有交付物与判定标准"],
      ["中试数据回流", "中试线采集数据自动关联到开发项目，不靠人工填表；数据质量由此保证"],
      ["量产转移", "中试确认的参数直接生成生产工艺卡下发 MES，减少转产抄录偏差"],
      ["复盘归档", "成功与失败案例一并入库，与 P8 知识库打通供后续检索"],
    ],
    needs: [
      "开发流程需先梳理定义 —— 有没有阶段门是管理决策，系统只是承载",
      "中试线具备数据采集条件（见 P7 现场条件）",
      "下游企业配合使用需求表单；可在入驻协议中约定",
      "研发与生产的工艺卡格式需统一，否则转移仍要人工翻译",
    ],
    phase: ["PHASE 2 · 中试线就绪后", "可与 P8 知识库同期建设；需求受理部分可更早上线"],
    phaseColor: C.rd,
  });
  footer(s);
}

// ═══════════════════════════════ P10 · 产 · 生产执行与全流程追溯
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "生产执行与全流程追溯", "PART 4 · MES & TRACEABILITY", C.mfg, "产");
  subTitle(s, "批次链是整个方案的地基 —— 质量分析、碳足迹、客户审厂都建立在它之上");

  scenarioPage(s, {
    problem: "计划与现场脱节，靠纸质工单与口头协调；批次异常时无法定位到原料批次与当时参数；" +
      "品牌客户审厂要求提供追溯能力，缺失会影响供应商准入。",
    steps: [
      ["工单与排产", "订单拆解为工单，按设备产能与模具约束排产；插单可重排并给出影响评估"],
      ["批次编码规则", "原料入库 / 混配 / 成型 / 成品四级编码；规则由生产与质量部门先定义再建系统"],
      ["工序间绑定", "每道工序开工扫码绑定上道批次形成父子链；系统强制校验，不允许跳过"],
      ["参数快照", "工序完成时将当时工艺参数、设备、班组、时间一并写入批次记录"],
      ["双向查询与对外", "正查原料流向、反查成品来源；向客户与园区平台提供受限查询接口"],
    ],
    needs: [
      "批次编码规则是业务决策不是技术决策，需生产与质量部门拍板",
      "各工序具备扫码或自动识别条件（工位终端 / 读码器）",
      "现场作业规范配套调整并纳入考核，否则会出现绕过系统操作",
      "设备联网与数采先行（见 P7），否则参数快照拿不到数据",
    ],
    phase: ["PHASE 1 · 最优先", "技术难度不高但涉及全流程改造；晚做则历史数据断层无法补，后续场景全部受阻"],
    phaseColor: C.mfg,
  });
  footer(s);
}

// ═══════════════════════════════ P11 · 产 · 质量与工艺闭环
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "质量与工艺闭环", "PART 4 · QUALITY & PROCESS", C.mfg, "产");
  subTitle(s, "发现缺陷 → 定位原因 → 调整参数 —— 三件事本来就是一个闭环，分开做只能治标");

  scenarioPage(s, {
    problem: "抽检存在漏检，判定标准因人而异；缺陷发现在成品阶段已消耗全部加工成本；" +
      "稻壳含水率与粒径随批次波动需人工调参，依赖老师傅经验，缺陷与参数的关联无法量化。",
    steps: [
      ["缺陷定义与标注规范", "与质量部门共同定义缺陷类型与判定标准 —— 这一步不做，后面全部返工"],
      ["成像与边缘推理", "相机位置、光源、节拍按产品形态确定；模型部署边缘节点本地推理，避免网络时延"],
      ["判定联动拦截", "不合格自动触发拦截或分拣并回传 MES，从事后判废转向事中拦截"],
      ["缺陷—参数关联分析", "把缺陷数据与该批次工艺参数、原料特性关联，先做统计分析找出真正相关的变量"],
      ["参数推荐（人机协同）", "数据量支撑后建立推荐模型，输入当批原料特性输出建议参数区间；人工确认后执行"],
    ],
    needs: [
      "产线稳定运行以采集足量样本 —— 投产初期缺陷分布不代表常态；样本量按缺陷类型数与发生频次评估",
      "原料入厂检测标准化：含水率、粒径不测则模型没有输入变量",
      "质量部门参与标注与验收；工艺工程师参与建模，需机理知识约束",
      "批次链已建立（P10），否则缺陷无法关联到参数",
    ],
    phase: ["PHASE 2-3 · 分步", "视觉质检待样本积累后上线（先做高频缺陷）；参数寻优待数据积累到位，不承诺短期见效"],
    phaseColor: C.redAccent,
  });
  footer(s);
}

// ═══════════════════════════════ P12 · 销 · 认证合规与碳足迹
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "认证合规与碳足迹", "PART 5 · COMPLIANCE & CARBON", C.sales, "销");
  subTitle(s, "「共享认证服务」要落地，先得让链主自己的认证资产结构化、可复用");

  scenarioPage(s, {
    problem: "认证证书、检测报告、体系文件散落各处，客户审厂时临时翻找；证书到期靠人记；" +
      "入驻企业各自做出口认证成本高、周期长；产品碳足迹拿不出核算依据，影响出口合规要求。",
    steps: [
      ["认证档案结构化", "证书 / 检测报告 / 体系文件按「产品 — 市场 — 标准」三维归档，可组合检索"],
      ["有效期与换证预警", "证书到期前自动提醒并关联换证所需材料清单，避免过期断供"],
      ["审厂材料组包", "按客户要求的清单一键调取并生成材料包，替代每次人工翻找拼凑"],
      ["碳足迹核算", "先定核算边界与方法学，再用分项能耗 + 批次数据分摊到单位产品"],
      ["向入驻企业复用", "认证路径、送检模板、常见问题沉淀为可复用资料，降低其出海门槛"],
    ],
    needs: [
      "分表分项计量在产线建设期同步预留（见 P7）—— 没有分表就没有碳足迹",
      "批次链已建立（P10），否则能耗无法分摊到产品",
      "核算边界与方法学标准需先确定 —— 这是合规决策，不同标准边界与因子不同",
      "既有认证材料的归集整理；排放因子优先采用官方发布值，自建部分需可溯源",
    ],
    phase: ["PHASE 1-2 · 分段", "认证档案可先行（不依赖产线）；碳足迹核算依赖计量装置与批次链，随产线同步"],
    phaseColor: C.sales,
  });
  footer(s);
}

// ═══════════════════════════════ P13 · 销 · 客户订单与交付协同
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "客户订单与交付协同", "PART 5 · ORDER & DELIVERY", C.sales, "销");
  subTitle(s, "品牌客户渠道是园区最难复制的资产 —— 但要带上下游一起用，得先让它可管理");

  scenarioPage(s, {
    problem: "品牌客户的准入要求、质量条款、包装标识规范散在个别人手里，人走即失；" +
      "客户问订单进度只能打电话查；园区内多家协同交付一个大单时靠人工协调，易出错漏。",
    steps: [
      ["客户要求结构化", "审厂标准 / 质量条款 / 包装标识 / 交付要求逐客户入库，下单时自动带出"],
      ["订单进度可视", "关联 MES 工单进度，按授权范围向客户开放受限查询，减少人工问答"],
      ["交付文档自动生成", "质检报告、追溯报告、碳足迹说明随货生成，不再每单手工整理"],
      ["园区拆单协同", "大单按产能与能力拆给入驻企业，各方进度回传汇总，链主统一对客户"],
      ["投诉闭环", "客诉关联到批次、工艺参数与配方版本，回流研发（P8）形成改进闭环"],
    ],
    needs: [
      "MES 与批次链已建成（P10），否则进度与追溯数据无来源",
      "对客户的开放范围由商务协议约定 —— 哪些可见、哪些不可见是商务决策",
      "入驻企业接入园区平台（P14），拆单协同才有对象",
      "客户要求的整理需销售与质量部门配合，历史信息多在个人手上",
    ],
    phase: ["PHASE 2-3", "客户要求结构化可较早做；拆单协同依赖入驻企业到位"],
    phaseColor: C.sales,
  });
  footer(s);
}

// ═══════════════════════════════ P14 · 园区协同平台
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "园区协同平台  ·  把研产销向入驻企业开放", "PART 6 · PARK PLATFORM");
  subTitle(s, "这是园区级方案区别于单厂系统的地方 —— 单厂 MES 做不到跨企业协同");

  // 上 · 三段各开放什么
  const opens = [
    ["研", C.rd, "技术资料受控查询（参数区间与应用指导）\n中试线预约与试制申请\n新品需求提交与进度跟踪"],
    ["产", C.mfg, "共享产能查询与代工下单\n材料到货与批次信息\n质量标准与检测能力调用"],
    ["销", C.sales, "协同订单接单与进度回传\n认证服务申请与材料复用\n园区统一对外的交付文档"],
  ];
  opens.forEach((o, i) => {
    const x = 0.6 + i * 4.13;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 1.72, w: 3.9, h: 1.85,
      fill: { color: C.white }, line: { color: o[1], width: 1.5 }, rectRadius: 0.06,
    });
    s.addShape(p.ShapeType.rect, { x, y: 1.72, w: 3.9, h: 0.4, fill: { color: o[1] } });
    s.addText(o[0], {
      x: x + 0.18, y: 1.78, w: 0.4, h: 0.28,
      fontSize: 13, bold: true, color: C.white, fontFace: "Cambria", margin: 0,
    });
    s.addText("向入驻企业开放", {
      x: x + 0.7, y: 1.81, w: 2.6, h: 0.24,
      fontSize: 9, color: C.white, fontFace: "Calibri", margin: 0,
    });
    s.addText(o[2], {
      x: x + 0.22, y: 2.22, w: 3.5, h: 1.25,
      fontSize: 9, color: C.gray, fontFace: "Calibri",
      lineSpacing: 15, margin: 0, valign: "top",
    });
  });

  // 下 · 平台侧设计要点
  s.addText("平台侧设计要点", {
    x: 0.6, y: 3.75, w: 5, h: 0.3,
    fontSize: 12, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const pts = [
    ["企业接入分级", "有系统的走接口，无系统的用轻量 Web 端录入 —— 入驻企业信息化水平差异大，不强求统一"],
    ["权限与数据隔离", "企业间数据默认不可见，按入驻协议逐项授权；平台方不得挪用企业经营数据"],
    ["采购结算数据归集", "区内交易的合同与发票数据归集，直接支撑协同采购补贴申报（见 P18）"],
    ["开放边界受协议约束", "技术资料开放到什么颗粒度、客户资源如何导入，是商务与法律问题，系统只执行"],
  ];
  pts.forEach((pt, i) => {
    const x = 0.6 + (i % 2) * 6.15;
    const y = 4.12 + Math.floor(i / 2) * 1.05;
    s.addShape(p.ShapeType.roundRect, {
      x, y, w: 5.95, h: 0.95,
      fill: { color: C.cream }, line: { color: C.bg2, width: 1 }, rectRadius: 0.05,
    });
    s.addText(pt[0], {
      x: x + 0.22, y: y + 0.13, w: 5.5, h: 0.28,
      fontSize: 11.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(pt[1], {
      x: x + 0.22, y: y + 0.44, w: 5.5, h: 0.45,
      fontSize: 8.8, color: C.gray, fontFace: "Calibri",
      lineSpacing: 12, margin: 0, valign: "top",
    });
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 6.3, w: 12.1, h: 0.62,
    fill: { color: C.white }, line: { color: C.accent, width: 1.5 }, rectRadius: 0.05,
  });
  s.addText("PHASE 3 · 招商到位后", {
    x: 0.85, y: 6.43, w: 2.6, h: 0.3,
    fontSize: 10.5, bold: true, color: C.accentDk, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "依赖首批企业入驻与链主侧系统建成 · 建议先做共享看板等轻量功能，协同派工待业务量起来再做 · 平台接入应在招商阶段写入入驻协议",
    {
      x: 3.6, y: 6.45, w: 8.9, h: 0.3,
      fontSize: 8.8, color: C.gray, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P15 · 集成边界
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "系统集成边界  ·  和谁接、接什么", "PART 6 · INTEGRATION");
  subTitle(s, "把接口关系写清楚，避免交付时出现「我以为对方负责」");

  const rows = [
    ["产线设备 / PLC", "产", "工艺参数、设备状态、产量计数、报警", "集成商实施 · 设备商供协议文档"],
    ["检测仪器 / 中试线", "研 产", "在线检测值、中试试制数据", "集成商实施 · 仪器需数字输出"],
    ["计量装置", "产 销", "电 / 气 / 水分项读数（碳足迹底数）", "集成商实施 · 表具需支持远传"],
    ["视觉质检设备", "产", "图像采集与判定结果", "硬件集成商装 · 算法由本方"],
    ["ERP / 财务", "产 销", "物料主数据、订单、成本科目 / 完工与耗用回写", "本方对接 · 需客户 IT 配合"],
    ["检测机构 / 认证机构", "研 销", "检测报告、证书与有效期", "本方建设 · 需业务侧归集资料"],
    ["入驻企业系统", "研 产 销", "需求、订单、库存、产能（按授权范围）", "本方建设 · 接入方式分级"],
    ["品牌客户", "销", "订单进度与质量追溯（受限查询）", "本方建设 · 范围由商务协议定"],
    ["政府 / 监管报送", "销", "能耗、碳排、安全等合规数据", "本方建设 · 格式随主管部门"],
  ];

  const y0 = 1.72, rh = 0.53;
  s.addShape(p.ShapeType.rect, { x: 0.6, y: y0, w: 12.1, h: 0.34, fill: { color: C.primary } });
  ["集成对象", "涉及", "交换内容", "责任方与前提"].forEach((h, i) => {
    const xs = [0.78, 3.15, 4.35, 8.85];
    s.addText(h, {
      x: xs[i], y: y0 + 0.06, w: 3, h: 0.24,
      fontSize: 9.5, bold: true, color: C.white, fontFace: "Calibri", margin: 0,
    });
  });
  rows.forEach((r, i) => {
    const y = y0 + 0.34 + i * rh;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: rh,
      fill: { color: i % 2 ? C.white : C.cream }, line: { color: C.bg2, width: 0.5 },
    });
    s.addText(r[0], {
      x: 0.78, y: y + 0.14, w: 2.3, h: 0.26,
      fontSize: 9.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    // 涉及段 · 用色块
    r[1].split(" ").forEach((seg, j) => {
      const col = seg === "研" ? C.rd : seg === "产" ? C.mfg : C.sales;
      s.addShape(p.ShapeType.rect, {
        x: 3.15 + j * 0.36, y: y + 0.15, w: 0.28, h: 0.24, fill: { color: col },
      });
      s.addText(seg, {
        x: 3.15 + j * 0.36, y: y + 0.16, w: 0.28, h: 0.22,
        fontSize: 8, bold: true, color: C.white, fontFace: "Calibri",
        align: "center", margin: 0,
      });
    });
    s.addText(r[2], {
      x: 4.35, y: y + 0.14, w: 4.4, h: 0.28,
      fontSize: 8.4, color: C.gray, fontFace: "Calibri", margin: 0,
    });
    s.addText(r[3], {
      x: 8.85, y: y + 0.14, w: 3.7, h: 0.28,
      fontSize: 8.4, color: C.grayLt, fontFace: "Calibri", margin: 0,
    });
  });

  s.addText(
    "注 · 设备与仪器侧由系统集成商实施（见 P4 分工）· ERP 接口开通需客户方推动供应商配合，与设备接入同为排期风险最大的两项",
    {
      x: 0.6, y: 6.65, w: 12.1, h: 0.3,
      fontSize: 9, color: C.accentDk, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P16 · 建设内容清单
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "建设内容清单  ·  数字化侧", "PART 6 · SCOPE OF WORK");
  subTitle(s, "仅列数字化范围 · 产线设备与厂房不在此表 · 投资金额待选型询价后概算");

  const rows = [
    ["01", "边缘采集与联网", "产", "协议网关、边缘节点、采集代理、工位终端", "集成商", "技改（智能化）"],
    ["02", "传感与计量装置", "产 销", "补充传感器、分项计量表具及远传模块", "集成商", "技改 · 绿色制造"],
    ["03", "工业网络与安全", "产", "工业以太网、网络隔离、数据安全与权限", "集成商", "技改"],
    ["04", "数据平台", "研 产 销", "时序库、关系库、对象存储、数据服务 API", "本方", "技改（软件）"],
    ["05", "MES 与批次追溯", "产", "工单、排产、追溯、质量、设备与模具管理", "本方", "技改（软件）"],
    ["06", "视觉质检系统", "产", "标注平台、模型训练与推理（成像硬件由集成商）", "本方*", "技改 · 关键技术"],
    ["07", "研发数据管理", "研", "实验记录、配方版本、检测数据归集", "本方", "研发总部 / 功能性平台"],
    ["08", "工艺知识库与寻优", "研 产", "参数结构化、知识检索、关联分析与推荐模型", "本方", "关键技术攻关"],
    ["09", "新品开发协同", "研", "需求受理、阶段门、中试回流、量产转移", "本方", "研发总部 / 功能性平台"],
    ["10", "认证合规档案", "销", "证书与报告归档、有效期预警、审厂组包", "本方", "技改（软件）"],
    ["11", "能碳与碳足迹", "产 销", "核算模型、报告生成与报送（采集端由集成商）", "本方*", "绿色制造 / 技改"],
    ["12", "客户订单与交付", "销", "客户要求库、进度可视、交付文档生成", "本方", "技改（软件）"],
    ["13", "园区协同平台", "研 产 销", "共享看板、协同订单、企业接入端、结算归集", "本方", "区内协同采购（配套）"],
    ["14", "集成与实施服务", "—", "接口开发、数据迁移、部署调试、培训试运行", "本方", "技改（服务）"],
  ];

  const y0 = 1.68, rh = 0.365;
  s.addShape(p.ShapeType.rect, { x: 0.6, y: y0, w: 12.1, h: 0.3, fill: { color: C.primary } });
  ["", "建设内容", "涉及", "主要构成", "实施方", "可对应政策"].forEach((h, i) => {
    const xs = [0.75, 1.2, 3.6, 4.85, 9.35, 10.5];
    if (!h) return;
    s.addText(h, {
      x: xs[i], y: y0 + 0.04, w: 3, h: 0.22,
      fontSize: 9, bold: true, color: C.white, fontFace: "Calibri", margin: 0,
    });
  });
  rows.forEach((r, i) => {
    const y = y0 + 0.3 + i * rh;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: rh,
      fill: { color: i % 2 ? C.white : C.cream }, line: { color: C.bg2, width: 0.5 },
    });
    s.addText(r[0], {
      x: 0.75, y: y + 0.07, w: 0.4, h: 0.24,
      fontSize: 8.5, bold: true, color: C.accent, fontFace: "Cambria", margin: 0,
    });
    s.addText(r[1], {
      x: 1.2, y: y + 0.06, w: 2.35, h: 0.26,
      fontSize: 9, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    if (r[2] !== "—") {
      r[2].split(" ").forEach((seg, j) => {
        const col = seg === "研" ? C.rd : seg === "产" ? C.mfg : C.sales;
        s.addShape(p.ShapeType.rect, {
          x: 3.6 + j * 0.32, y: y + 0.08, w: 0.25, h: 0.21, fill: { color: col },
        });
        s.addText(seg, {
          x: 3.6 + j * 0.32, y: y + 0.085, w: 0.25, h: 0.2,
          fontSize: 7.5, bold: true, color: C.white, fontFace: "Calibri",
          align: "center", margin: 0,
        });
      });
    }
    s.addText(r[3], {
      x: 4.85, y: y + 0.07, w: 4.4, h: 0.24,
      fontSize: 7.8, color: C.gray, fontFace: "Calibri", margin: 0,
    });
    const isOurs = r[4].startsWith("本方");
    s.addShape(p.ShapeType.rect, {
      x: 9.35, y: y + 0.07, w: 0.98, h: 0.23,
      fill: { color: isOurs ? C.primary : C.grayLt },
    });
    s.addText(r[4], {
      x: 9.35, y: y + 0.085, w: 0.98, h: 0.21,
      fontSize: 7.2, bold: true, color: C.white, fontFace: "Calibri",
      align: "center", margin: 0,
    });
    s.addText(r[5], {
      x: 10.5, y: y + 0.07, w: 2.15, h: 0.24,
      fontSize: 7.4, bold: true, color: C.accentDk, fontFace: "Calibri", margin: 0,
    });
  });

  s.addText(
    "本方* = 软件与算法由本方，配套硬件（相机 / 光源 / 计量表具）由集成商   ·   注：02 项不装表就没有能耗数据，建议与产线同期实施",
    {
      x: 0.6, y: 6.9, w: 12.1, h: 0.28,
      fontSize: 8, color: C.grayLt, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P17 · 技术指标框架
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "技术指标框架", "PART 6 · KPI FRAMEWORK");
  subTitle(s, "申报评审按指标打分、验收按指标核查 —— 所有基线必须实测，本页不填估计值", C.redAccent);

  s.addText("效益类指标  ·  申报通用五项", {
    x: 0.6, y: 1.7, w: 5.95, h: 0.3,
    fontSize: 11.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const core = [
    ["生产效率", "人均产值 / 单位时间产出"],
    ["运营成本", "单位产品制造成本"],
    ["产品不良品率", "一次合格率 / 废品率"],
    ["单位产值能耗", "综合能耗 / 产值"],
    ["产品研制周期", "新品立项到量产时长"],
  ];
  s.addShape(p.ShapeType.rect, { x: 0.6, y: 2.04, w: 5.95, h: 0.3, fill: { color: C.primary } });
  ["指标", "基线", "目标"].forEach((h, i) => {
    s.addText(h, {
      x: [0.75, 3.6, 5.1][i], y: 2.08, w: 1.6, h: 0.24,
      fontSize: 8.5, bold: true, color: C.white, fontFace: "Calibri", margin: 0,
    });
  });
  core.forEach((c, i) => {
    const y = 2.34 + i * 0.62;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 5.95, h: 0.62,
      fill: { color: i % 2 ? C.white : C.cream }, line: { color: C.bg2, width: 0.5 },
    });
    s.addText(c[0], {
      x: 0.75, y: y + 0.07, w: 2.7, h: 0.26,
      fontSize: 10, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(c[1], {
      x: 0.75, y: y + 0.32, w: 2.7, h: 0.24,
      fontSize: 7.5, color: C.grayLt, fontFace: "Calibri", margin: 0,
    });
    s.addText("[待实测]", {
      x: 3.6, y: y + 0.18, w: 1.4, h: 0.26,
      fontSize: 8.5, bold: true, color: C.redAccent, fontFace: "Calibri", margin: 0,
    });
    s.addText("[待定]", {
      x: 5.1, y: y + 0.18, w: 1.3, h: 0.26,
      fontSize: 8.5, bold: true, color: C.grayLt, fontFace: "Calibri", margin: 0,
    });
  });

  s.addText("能力类指标  ·  数字化覆盖度", {
    x: 6.75, y: 1.7, w: 5.95, h: 0.3,
    fontSize: 11.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const sup = [
    ["关键工序数控化率", "产", "数控工序 / 关键工序总数"],
    ["设备联网率", "产", "已联网 / 应联网设备数"],
    ["质量追溯覆盖率", "产", "可追溯批次 / 总批次"],
    ["研发数据电子化率", "研", "结构化记录 / 实验总数"],
    ["认证档案完整率", "销", "已归档证书 / 有效证书总数"],
  ];
  s.addShape(p.ShapeType.rect, { x: 6.75, y: 2.04, w: 5.95, h: 0.3, fill: { color: C.steel } });
  ["指标", "基线", "目标"].forEach((h, i) => {
    s.addText(h, {
      x: [6.9, 10.05, 11.45][i], y: 2.08, w: 1.6, h: 0.24,
      fontSize: 8.5, bold: true, color: C.white, fontFace: "Calibri", margin: 0,
    });
  });
  sup.forEach((c, i) => {
    const y = 2.34 + i * 0.62;
    s.addShape(p.ShapeType.rect, {
      x: 6.75, y, w: 5.95, h: 0.62,
      fill: { color: i % 2 ? C.white : C.cream }, line: { color: C.bg2, width: 0.5 },
    });
    const col = c[1] === "研" ? C.rd : c[1] === "产" ? C.mfg : C.sales;
    s.addShape(p.ShapeType.rect, { x: 6.9, y: y + 0.1, w: 0.24, h: 0.21, fill: { color: col } });
    s.addText(c[1], {
      x: 6.9, y: y + 0.105, w: 0.24, h: 0.2,
      fontSize: 7.5, bold: true, color: C.white, fontFace: "Calibri",
      align: "center", margin: 0,
    });
    s.addText(c[0], {
      x: 7.24, y: y + 0.07, w: 2.7, h: 0.26,
      fontSize: 9.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(c[2], {
      x: 7.24, y: y + 0.32, w: 2.7, h: 0.24,
      fontSize: 7.2, color: C.grayLt, fontFace: "Calibri", margin: 0,
    });
    s.addText("[待测]", {
      x: 10.05, y: y + 0.18, w: 1.3, h: 0.26,
      fontSize: 8.5, bold: true, color: C.redAccent, fontFace: "Calibri", margin: 0,
    });
    s.addText("[待定]", {
      x: 11.45, y: y + 0.18, w: 1.2, h: 0.26,
      fontSize: 8.5, bold: true, color: C.grayLt, fontFace: "Calibri", margin: 0,
    });
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.6, w: 12.1, h: 1.32,
    fill: { color: "FDF3F2" }, line: { color: C.redAccent, width: 1.5 }, rectRadius: 0.05,
  });
  s.addText("为什么本页不填估计值", {
    x: 0.9, y: 5.72, w: 4, h: 0.28,
    fontSize: 11, bold: true, color: C.redAccent, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "申报指标是验收依据 —— 报上去的数字将来要按它核查，兑现不了的后果是资金追回与失信记录。现状基线从未实测，" +
    "任何估计值都会变成无法兑现的承诺。\n" +
    "建立基线需采集：近 12 个月产量与工时台账 · 批次合格率与废品记录 · 分项能耗（现为整厂电表，需先装分表）· " +
    "新品开发周期记录 · 实验记录与认证档案清点。  目标值的门槛要求以当年申报指南为准。",
    {
      x: 0.9, y: 6.04, w: 11.5, h: 0.8,
      fontSize: 9, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    }
  );
  footer(s);
}

// ═══════════════════════════════ P18 · 政策对接
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "临港政策对接  ·  按建设内容匹配", "PART 7 · POLICY MAP");
  subTitle(s, "下列为政策公示上限 · 实际额度取决于投资额与认定结果 · 本页不做加总测算", C.redAccent);

  const rows = [
    ["01-06  采集 / 网络 / 平台 / MES / 质检", "企业技术改造和智能化升级", "重点 5000 万 / 一般 1000 万", "固投比例 ≥ 60%"],
    ["02  计量与能碳硬件", "技术改造（绿色制造方向）", "并入技改项目", "同上"],
    ["06 08  质检与工艺模型", "支持关键核心技术突破", "新增投资 10-30% · 重点 3000 万", "填补国内空白"],
    ["07 09  研发数据与新品协同", "设立研发总部 / 功能性平台", "项目总投资 50% · 最高 1000 万", "重点实验室等"],
    ["13  园区协同平台", "区内协同采购（平台提供数据支撑）", "采购发票额 10% · 1000 万 / 年", "双方无股权关联"],
    ["整体资金成本", "贷款贴息", "固投贷款利息 50% · 1000 万 / 年", "按实际支付利息"],
    ["整体税负", "企业所得税优惠", "减按 15% · 自设立起 5 年", "需实质性生产研发"],
  ];
  const y0 = 1.78, rh = 0.63;
  s.addShape(p.ShapeType.rect, { x: 0.6, y: y0, w: 12.1, h: 0.38, fill: { color: C.primary } });
  ["对应建设内容（P16 编号）", "政策名称", "支持上限", "主要条件"].forEach((h, i) => {
    const xs = [0.78, 4.4, 7.6, 10.6];
    s.addText(h, {
      x: xs[i], y: y0 + 0.08, w: 3.4, h: 0.24,
      fontSize: 9.5, bold: true, color: C.white, fontFace: "Calibri", margin: 0,
    });
  });
  rows.forEach((r, i) => {
    const y = y0 + 0.38 + i * rh;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: rh,
      fill: { color: i % 2 ? C.white : C.cream }, line: { color: C.bg2, width: 0.5 },
    });
    const xs = [0.78, 4.4, 7.6, 10.6];
    const ws = [3.5, 3.1, 2.9, 2.0];
    r.forEach((cell, j) => {
      s.addText(cell, {
        x: xs[j], y: y + 0.1, w: ws[j], h: 0.44,
        fontSize: 8.6, bold: j === 0 || j === 2,
        color: j === 2 ? C.accentDk : (j === 0 ? C.primary : (j === 3 ? C.grayLt : C.dark)),
        fontFace: "Calibri", margin: 0, valign: "middle", lineSpacing: 11,
      });
    });
  });

  s.addText(
    "逐条摘自《上海临港新片区政策情况》p7-p12 · 以临港新片区管委会发布最新版本为准 · 申报主体为稻生万物",
    {
      x: 0.6, y: 6.9, w: 12, h: 0.28,
      fontSize: 8, color: C.grayLt, fontFace: "Calibri", italic: true, margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P19 · 申报要件对照
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "申报要件对照  ·  谁准备什么", "PART 7 · FILING CHECKLIST");
  subTitle(s, "申报主体为稻生万物 · 下列为通用要件，具体以当年申报指南为准", C.redAccent);

  const rows = [
    ["技术改造和智能化升级", "可研报告 · 投资明细 · 软硬件合同 · 技改前后指标对比",
      "技术方案 · 架构与集成设计 · 建设清单（P16）· 指标框架（P17）", "财务报表 · 固投证明 · 合同与发票"],
    ["关键核心技术突破", "技术先进性说明 · 填补空白的证据",
      "质检与工艺模型技术路线说明", "科技查新报告 · 专利证书"],
    ["研发总部 / 功能性平台", "平台功能说明 · 研发人员名册 · 投入强度",
      "研发数据管理与新品协同的功能与建设方案", "人员社保 · 研发费用归集 · 场地证明"],
    ["区内协同采购", "采购合同与发票 · 无股权关联声明",
      "协同平台的交易数据归集与导出能力", "合同 · 发票 · 关联关系声明"],
    ["贷款贴息", "贷款合同 · 利息支付凭证", "—", "银行流水与凭证"],
  ];
  const y0 = 1.78, rh = 0.86;
  s.addShape(p.ShapeType.rect, { x: 0.6, y: y0, w: 12.1, h: 0.4, fill: { color: C.primary } });
  ["政策项", "通用申报要件", "本方案可直接提供", "稻生万物需自行准备"].forEach((h, i) => {
    const xs = [0.78, 3.15, 6.55, 9.95];
    s.addText(h, {
      x: xs[i], y: y0 + 0.09, w: 3.2, h: 0.24,
      fontSize: 9.5, bold: true, color: C.white, fontFace: "Calibri", margin: 0,
    });
  });
  rows.forEach((r, i) => {
    const y = y0 + 0.4 + i * rh;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: rh,
      fill: { color: i % 2 ? C.white : C.cream }, line: { color: C.bg2, width: 0.5 },
    });
    const xs = [0.78, 3.15, 6.55, 9.95];
    const ws = [2.25, 3.3, 3.3, 2.5];
    r.forEach((cell, j) => {
      s.addText(cell, {
        x: xs[j], y: y + 0.08, w: ws[j], h: rh - 0.16,
        fontSize: j === 0 ? 9.5 : 8.5, bold: j === 0,
        color: j === 0 ? C.primary : (j === 2 ? C.accentDk : C.gray),
        fontFace: j === 0 ? "Cambria" : "Calibri",
        margin: 0, valign: "middle", lineSpacing: 12,
      });
    });
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 6.42, w: 12.1, h: 0.55,
    fill: { color: "FDF3F2" }, line: { color: C.redAccent, width: 1 }, rectRadius: 0.04,
  });
  s.addText(
    "⚠ 本表为通用要件梳理，未获取临港当年申报指南 —— 正式申报前须以管委会发布的指南核对格式与必填项",
    {
      x: 0.9, y: 6.54, w: 11.5, h: 0.35,
      fontSize: 9, color: C.redAccent, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P20 · 实施路径
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "实施路径  ·  按技术依赖排序", "PART 7 · ROADMAP");
  subTitle(s, "不按价值大小排，按依赖关系排 —— 地基没打好，上面的场景做了也是返工");

  const phases = [
    ["PHASE 1", "地基", [
      ["产", "点位表与接口规范定义（本方）", C.mfg],
      ["产", "MES 与批次追溯（设备联网由集成商并行）", C.mfg],
      ["研", "研发数据结构化 · 知识库起步", C.rd],
      ["销", "认证档案归集与结构化", C.sales],
    ], "研 / 销 侧不依赖产线；设备侧由集成商并行，不占本方排期"],
    ["PHASE 2", "见效", [
      ["产", "在线视觉质检（先做高频缺陷）", C.mfg],
      ["销", "碳足迹核算 · 交付文档自动生成", C.sales],
      ["研", "新品开发协同 · 中试数据回流", C.rd],
      ["销", "客户要求结构化 · 进度可视", C.sales],
    ], "依赖 PHASE 1 数据基础与产线投产后的样本积累"],
    ["PHASE 3", "优化与协同", [
      ["产", "工艺参数寻优模型", C.mfg],
      ["研", "技术资料受控输出给下游", C.rd],
      ["销", "园区拆单协同交付", C.sales],
      ["跨", "园区协同平台全量开放", C.primary],
    ], "依赖数据积累与首批企业入驻"],
  ];
  phases.forEach((ph, i) => {
    const x = 0.6 + i * 4.13;
    const isFirst = i === 0;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 1.72, w: 3.9, h: 3.95,
      fill: { color: isFirst ? C.primary : C.white },
      line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
    });
    s.addText(ph[0], {
      x: x + 0.25, y: 1.92, w: 3.4, h: 0.28,
      fontSize: 10, bold: true, color: isFirst ? C.accent : C.accentDk,
      fontFace: "Calibri", charSpacing: 2, margin: 0,
    });
    s.addText(ph[1], {
      x: x + 0.25, y: 2.25, w: 3.4, h: 0.35,
      fontSize: 16, bold: true, color: isFirst ? C.white : C.primary,
      fontFace: "Cambria", margin: 0,
    });
    ph[2].forEach((item, j) => {
      const iy = 2.78 + j * 0.6;
      s.addShape(p.ShapeType.rect, {
        x: x + 0.25, y: iy, w: 0.26, h: 0.22, fill: { color: item[2] },
      });
      s.addText(item[0], {
        x: x + 0.25, y: iy + 0.005, w: 0.26, h: 0.21,
        fontSize: 7.5, bold: true, color: C.white, fontFace: "Calibri",
        align: "center", margin: 0,
      });
      s.addText(item[1], {
        x: x + 0.58, y: iy - 0.02, w: 3.1, h: 0.52,
        fontSize: 8.5, color: isFirst ? C.cream : C.gray, fontFace: "Calibri",
        lineSpacing: 11, margin: 0, valign: "top",
      });
    });
    s.addShape(p.ShapeType.rect, {
      x: x + 0.25, y: 5.2, w: 3.4, h: 0.01,
      fill: { color: isFirst ? C.primaryDk : C.bg2 },
    });
    s.addText(ph[3], {
      x: x + 0.25, y: 5.3, w: 3.4, h: 0.32,
      fontSize: 8, color: isFirst ? C.secondary : C.accentDk,
      fontFace: "Calibri", lineSpacing: 11, margin: 0, valign: "top",
    });
    if (i < 2) {
      s.addText("▶", {
        x: x + 3.94, y: 3.5, w: 0.28, h: 0.3,
        fontSize: 13, color: C.accent, fontFace: "Calibri", margin: 0,
      });
    }
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.85, w: 12.1, h: 1.05,
    fill: { color: C.cream }, line: { color: C.accent, width: 1 }, rectRadius: 0.05,
  });
  s.addText("两条硬约束", {
    x: 0.9, y: 5.97, w: 3, h: 0.28,
    fontSize: 11, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "① 传感器与计量装置必须在产线建设期同步安装 —— 事后加装需停线改造，成本与风险显著上升（见 P7 / P16-02）\n" +
    "② 设备采购合同须写入数据开放条款 —— 这一步错过，后面所有阶段都要付代价，部分封闭系统无法补救",
    {
      x: 0.9, y: 6.28, w: 11.5, h: 0.58,
      fontSize: 8.8, color: C.gray, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "top",
    }
  );
  s.addText("注 · 各阶段时长需结合产线建设周期确定，本页不列具体月份", {
    x: 0.6, y: 6.95, w: 12.1, h: 0.28,
    fontSize: 8, color: C.grayLt, fontFace: "Calibri", margin: 0,
  });
  footer(s);
}

// ═══════════════════════════════ P21 · 风险
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "关键风险与应对", "PART 7 · RISK");
  subTitle(s, "智能制造与产业园数字化的典型失败模式 —— 多数不是技术问题");

  const risks = [
    ["设备数据拿不到", "产", "采购合同未约定数据开放，厂商以商业机密为由拒绝或另行收费；封闭系统无法接入",
      "数据接口条款写入设备采购合同 · 本方提供条款模板与验收标准，风险由集成商合同承接"],
    ["集成商交付不达标", "产", "数据接进来但缺点位、频率不足或时间戳不同步，应用层无法使用",
      "P7 的验收标准作为付款节点前置条件 · 联调阶段本方参与，不到最后才对接"],
    ["系统建了没人用", "产", "MES 与现场作业习惯脱节，工人绕过系统，数据失真，追溯链断裂",
      "关键用户全程参与设计 · 分阶段试点 · 作业规范同步调整并纳入考核"],
    ["研发不愿改记录方式", "研", "实验记录结构化触动研发人员既有习惯，推行阻力大，系统沦为空壳",
      "管理层推动而非技术手段 · 先做减负功能（自动带出、报告归集）建立正反馈"],
    ["AI 期望过高", "产", "数据不足时强上高难度场景，效果不及预期，损伤后续投入意愿",
      "按依赖排序 · 先做视觉质检等确定性高的 · 工艺寻优不承诺短期见效"],
    ["基线缺失无法验收", "—", "指标基线未实测就申报，验收时拿不出改造前数据",
      "PHASE 1 首要任务即基线测评 · 采集方法见 P17"],
    ["开放边界失守", "销", "技术资料与客户资源开放给入驻企业后被绕开自立门户",
      "开放颗粒度按协议分级 · 给参数区间不给核心配方 · 系统按权限执行"],
    ["入驻企业不接入", "跨", "协同平台建成但企业不愿共享数据，平台空转",
      "招商阶段即把接入写入入驻协议 · 提供低门槛接入方式"],
  ];
  risks.forEach((r, i) => {
    const y = 1.72 + i * 0.74;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: 0.66,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 },
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 0.05, h: 0.66, fill: { color: C.redAccent } });
    const col = r[1] === "研" ? C.rd : r[1] === "产" ? C.mfg
      : r[1] === "销" ? C.sales : C.grayLt;
    s.addShape(p.ShapeType.rect, { x: 0.8, y: y + 0.22, w: 0.24, h: 0.21, fill: { color: col } });
    s.addText(r[1] === "—" ? "·" : r[1], {
      x: 0.8, y: y + 0.225, w: 0.24, h: 0.2,
      fontSize: 7.5, bold: true, color: C.white, fontFace: "Calibri",
      align: "center", margin: 0,
    });
    s.addText(r[0], {
      x: 1.16, y: y + 0.08, w: 2.1, h: 0.5,
      fontSize: 10, bold: true, color: C.primary, fontFace: "Cambria",
      margin: 0, valign: "middle",
    });
    s.addText(r[2], {
      x: 3.4, y: y + 0.06, w: 4.9, h: 0.54,
      fontSize: 8.2, color: C.gray, fontFace: "Calibri",
      lineSpacing: 11, margin: 0, valign: "middle",
    });
    s.addText(r[3], {
      x: 8.45, y: y + 0.06, w: 4.05, h: 0.54,
      fontSize: 8.2, color: C.accentDk, fontFace: "Calibri",
      lineSpacing: 11, margin: 0, valign: "middle",
    });
  });

  s.addText(
    "前两项是智能制造项目最常见的失败原因 —— 都不是技术问题，而是合同条款与组织配套的问题",
    {
      x: 0.6, y: 6.95, w: 12.1, h: 0.3,
      fontSize: 8.5, color: C.redAccent, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P22 · 下一步
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.primary };
  s.addShape(p.ShapeType.rect, { x: 0, y: 0, w: W, h: 0.12, fill: { color: C.accent } });

  s.addText("下一步", {
    x: 1.1, y: 1.15, w: 11, h: 0.7,
    fontSize: 34, bold: true, color: C.white, fontFace: "Cambria", margin: 0,
  });
  s.addText("数字化技术方案  ·  稻生万物研产销一体化产业园", {
    x: 1.1, y: 1.92, w: 11, h: 0.4,
    fontSize: 14, color: C.secondary, fontFace: "Calibri", margin: 0,
  });

  const steps = [
    ["01", "现状调研与基线测评", "走访产线、研发与销售三侧 · 采集近 12 个月产量 / 质检 / 能耗数据，清点实验记录与认证档案\n没有基线，P17 的指标填不了，申报也报不了"],
    ["02", "点位表梳理", "按工序列出设备 / 参数名 / 数据类型 / 采样频率 / 精度要求（含中试线）\n这是数字化的第一份交付物，也是概算的输入"],
    ["03", "设备招标条款支持", "协助在产线与中试设备招标文件中写入数据开放与接口条款\n这一步错过后面无法补救（P20 硬约束 ②）"],
    ["04", "方案深化与概算", "按点位表与选型结果细化架构、出投资概算\n形成可用于申报的技术方案与投资明细"],
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
      x: 2.15, y: y + 0.16, w: 3.2, h: 0.32,
      fontSize: 13, bold: true, color: C.white, fontFace: "Cambria", margin: 0,
    });
    s.addText(st[2], {
      x: 5.5, y: y + 0.14, w: 6.5, h: 0.7,
      fontSize: 9, color: C.cream, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "top",
    });
  });
}

p.writeFile({ fileName: "daosheng-v19-digital-scoped.pptx" })
  .then(() => console.log("✓ daosheng-v19-digital-scoped.pptx  ·  22 页"));
