// 稻生万物研产销一体化产业园 · 数字化技术方案 · v17 · 19 页
//
// ═══ v17 vs v16 (7/28 鸿波第五轮 · 定位重定) ═══
//
// 鸿波两句话:
//   "我觉得这版没有具体的实现" + "我们要不要提那么多的产线内容？"
//   定位确认: **数字化技术方案**（不是园区商业方案）
//
// v16 的两个毛病:
//   ① 通篇是"建什么"的清单, 没有"怎么建" —— 没有架构、没有数据流、
//      没有场景怎么落地. 清单谁都能列, 实现才是我们该写的.
//   ② 产线内容占了小半篇幅. 但产线不是我们交付的 —— 设备选型与工艺路线
//      是稻生万物和设备厂商的事, 他们比我们懂. 写浅了反而露怯.
//
// ═══ v17 的两个结构性调整 ═══
//
// ① 产线：从"我们的建设内容" → "我们的接入前提" (P6)
//    产线怎么建是客户和设备商定; 但**必须满足这些数据条件, 否则数字化建不起来**
//    —— PLC 开放读取 / 协议文档 / 关键工序采集点 / 分表预留 / 既有系统接口.
//    这是真话, 也是更专业的姿态: 划清边界, 讲清依赖.
//    （需新增的传感器与采集装置仍留在建设清单 P13 —— 那部分和数字化直接绑定）
//
// ② 商业逻辑：v15/v16 的 P4-P9 六页（链主赋能/研产销闭环/招商）压成 P2 一页背景.
//    定位清晰后读者知道这是谁写的、写的是哪一层. 完整商业版本见 v15/v16.
//
// ═══ 篇幅重分配 ═══
//   背景 2 页  ·  总体设计 3 页  ·  场景实现 5 页  ·  集成与建设 3 页
//   ·  政策申报 2 页  ·  实施风险 3 页   = 19 页
//   (v16: 商业 9 页 + 技术 6 页 + 申报 4 页)
//
// ═══ 军规 ═══
//   - 不点"鲶鱼"品牌名 · **不出现"自主研发/自研"** (我方非申报主体, 但不能
//     主动造成客户自研的印象 —— 被核查时麻烦落在稻生万物身上)
//   - 不写人月工期
//   - 指标基线 [待实测] / 目标 [待定] —— 申报按指标验收, 编了是给自己挖坑
//   - 样本量 / 数据量 / 投资额 不编具体数字, 写清**评估方法**代替
//   - 政策数字标出处页, 不做加总
//   - 申报要件标明"以当年申报指南为准" —— 我方未获取指南
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
  steel: "34495E", steelLt: "5D6D7E",
  edge: "8B6F47", plat: "5B6C8F", app: "6B8E5A",
};
const W = 13.3;
const TOTAL = 19;

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

/** 场景页统一四段式：问题 / 实现 / 前提 / 分期 —— 保证每页都答"怎么做" */
function scenarioPage(s, { problem, steps, needs, phase, phaseColor }) {
  // 左上 · 要解决什么
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

  // 左下 · 前提条件
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

  // 右 · 实现步骤
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
      x: 6.75, y: y + 0.1, w: 2.3, h: 0.28,
      fontSize: 10.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(st[1], {
      x: 6.75, y: y + 0.38, w: 5.7, h: 0.4,
      fontSize: 8.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 11, margin: 0, valign: "top",
    });
  });

  // 底 · 分期
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.85, w: 12.1, h: 0.62,
    fill: { color: C.white }, line: { color: phaseColor || C.secondary, width: 1.5 },
    rectRadius: 0.05,
  });
  s.addText(phase[0], {
    x: 0.85, y: 5.98, w: 1.8, h: 0.3,
    fontSize: 10.5, bold: true, color: phaseColor || C.primary,
    fontFace: "Cambria", margin: 0,
  });
  s.addText(phase[1], {
    x: 2.75, y: 6.0, w: 9.7, h: 0.3,
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
    x: 1.1, y: 2.0, w: 11, h: 1.0,
    fontSize: 44, bold: true, color: C.white,
    fontFace: "Cambria", charSpacing: 6, margin: 0,
  });

  s.addShape(p.ShapeType.rect, { x: 1.15, y: 3.15, w: 1.6, h: 0.04, fill: { color: C.accent } });

  s.addText("稻生万物研产销一体化产业园", {
    x: 1.1, y: 3.45, w: 11, h: 0.45,
    fontSize: 19, color: C.cream, fontFace: "Cambria", charSpacing: 2, margin: 0,
  });
  s.addText("上海临港新片区", {
    x: 1.1, y: 3.95, w: 11, h: 0.35,
    fontSize: 13, color: C.secondary, fontFace: "Calibri", charSpacing: 1, margin: 0,
  });

  s.addText(
    "本方案覆盖数字化系统的架构、数据流与场景实现\n" +
    "产线设备选型与工艺路线由建设方与设备供应商确定，本方案给出接入条件要求",
    {
      x: 1.1, y: 5.35, w: 11, h: 0.7,
      fontSize: 10, color: C.secondary, fontFace: "Calibri",
      lineSpacing: 16, margin: 0, valign: "top",
    }
  );
  s.addText("2026 年 7 月", {
    x: 1.1, y: 6.15, w: 11, h: 0.3,
    fontSize: 11, color: C.grayLt, fontFace: "Calibri", margin: 0,
  });
}

// ═══════════════════════════════ P2 · 项目背景与数字化定位
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "项目背景与数字化定位", "CONTEXT");
  subTitle(s, "园区商业模式与招商规划见《产业园建设方案》· 本页只交代数字化要支撑什么");

  // 上 · 园区三闭环 (压缩版)
  s.addText("园区形态  ·  研产销三闭环", {
    x: 0.6, y: 1.72, w: 5, h: 0.3,
    fontSize: 12, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const loops = [
    ["研", "材料研发（链主）→ 应用开发（下游）→ 中试验证（共享）", C.plat],
    ["产", "原料预处理（上游）→ 材料制造（链主）→ 制品成型（下游）", C.edge],
    ["销", "品牌客户（链主既有）→ 认证出海（共享）→ 国内渠道（下游）", C.app],
  ];
  loops.forEach((l, i) => {
    const y = 2.1 + i * 0.62;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: 0.54,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 },
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 0.42, h: 0.54, fill: { color: l[2] } });
    s.addText(l[0], {
      x: 0.6, y: y + 0.12, w: 0.42, h: 0.3,
      fontSize: 14, bold: true, color: C.white, fontFace: "Cambria",
      align: "center", margin: 0,
    });
    s.addText(l[1], {
      x: 1.2, y: y + 0.14, w: 11.3, h: 0.3,
      fontSize: 10, color: C.gray, fontFace: "Calibri", margin: 0,
    });
  });

  // 下 · 数字化要解决的四件事
  s.addText("数字化在其中承担什么", {
    x: 0.6, y: 4.15, w: 5, h: 0.3,
    fontSize: 12, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const roles = [
    ["把工艺变成资产", "配方与参数从老师傅经验变成可复用、可迁移的数字模型 —— 支撑「研」闭环向下游输出技术"],
    ["把质量变成证据", "从原料批次到成品条码全程可追溯 —— 支撑「销」闭环的客户审厂与出口合规"],
    ["把园区连成一张网", "入驻企业订单/库存/产能可见、协同订单可拆分 —— 这是单厂系统做不到的园区级能力"],
    ["把能碳变成可计量", "分项计量与产品级碳足迹核算 —— 支撑零碳认证与出口碳关税应对"],
  ];
  roles.forEach((r, i) => {
    const x = 0.6 + (i % 2) * 6.15;
    const y = 4.52 + Math.floor(i / 2) * 1.15;
    s.addShape(p.ShapeType.roundRect, {
      x, y, w: 5.95, h: 1.05,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 }, rectRadius: 0.05,
    });
    s.addText(r[0], {
      x: x + 0.22, y: y + 0.16, w: 5.5, h: 0.3,
      fontSize: 12, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(r[1], {
      x: x + 0.22, y: y + 0.5, w: 5.5, h: 0.48,
      fontSize: 9, color: C.gray, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "top",
    });
  });

  s.addText(
    "边界声明 · 产线设备选型、工艺路线、厂房与土建不在本方案范围 —— 本方案给出对产线的数据接入要求（见 P6）",
    {
      x: 0.6, y: 6.88, w: 12.1, h: 0.3,
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
    ["PART 1", "总体设计  ·  架构 / 数据流 / 接入前提", "P4 - P6"],
    ["PART 2", "场景实现  ·  五个场景怎么落地", "P7 - P11"],
    ["PART 3", "集成与建设内容", "P12 - P14"],
    ["PART 4", "政策与申报对接", "P15 - P16"],
    ["PART 5", "实施路径  ·  风险  ·  下一步", "P17 - P19"],
  ];
  parts.forEach((pt, i) => {
    const y = 1.95 + i * 0.98;
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

// ═══════════════════════════════ P4 · 总体架构 ★
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "总体架构  ·  组件与部署", "PART 1 · ARCHITECTURE");
  subTitle(s, "按部署位置分三层 —— 边缘侧就近处理、平台侧统一存算、应用侧面向使用者");

  const layers = [
    ["应用层", C.app, "厂区机房 / 云",
      ["生产执行 MES", "质量追溯", "在线质检", "工艺知识库", "园区协同", "能碳管理"]],
    ["平台层", C.plat, "厂区机房 / 云",
      ["时序数据库\n(工艺参数/能耗)", "关系数据库\n(工单/批次/质检)", "对象存储\n(图像/报告)",
       "消息队列\n(采集数据缓冲)", "数据服务 API\n(统一取数)", "身份与权限"]],
    ["边缘层", C.edge, "车间现场",
      ["协议网关\n(OPC UA/Modbus/私有)", "边缘计算节点\n(视觉推理/本地缓存)", "采集代理\n(点位轮询/事件上报)"]],
  ];

  let y = 1.75;
  layers.forEach((L) => {
    const h = L[3].length > 3 ? 1.62 : 1.28;
    s.addShape(p.ShapeType.roundRect, {
      x: 0.6, y, w: 12.1, h,
      fill: { color: C.white }, line: { color: L[1], width: 1.5 }, rectRadius: 0.06,
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 1.45, h, fill: { color: L[1] } });
    s.addText(L[0], {
      x: 0.6, y: y + h / 2 - 0.26, w: 1.45, h: 0.3,
      fontSize: 13, bold: true, color: C.white, fontFace: "Cambria",
      align: "center", margin: 0,
    });
    s.addText(L[2], {
      x: 0.6, y: y + h / 2 + 0.04, w: 1.45, h: 0.24,
      fontSize: 7.5, color: C.cream, fontFace: "Calibri", align: "center", margin: 0,
    });

    const cols = L[3].length > 3 ? 3 : 3;
    L[3].forEach((item, i) => {
      const cw = 3.4, ch = L[3].length > 3 ? 0.66 : 0.82;
      const cx = 2.2 + (i % cols) * 3.5;
      const cy = y + 0.18 + Math.floor(i / cols) * (ch + 0.12);
      s.addShape(p.ShapeType.roundRect, {
        x: cx, y: cy, w: cw, h: ch,
        fill: { color: C.cream }, line: { color: C.bg2, width: 0.5 }, rectRadius: 0.04,
      });
      s.addText(item, {
        x: cx + 0.12, y: cy + 0.08, w: cw - 0.24, h: ch - 0.16,
        fontSize: 8.5, color: C.dark, fontFace: "Calibri",
        align: "center", valign: "middle", lineSpacing: 11, margin: 0,
      });
    });
    y += h + 0.16;
  });

  // 底部 · 两条设计原则
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 6.32, w: 5.95, h: 0.62,
    fill: { color: C.cream }, line: { color: C.bg2, width: 1 }, rectRadius: 0.04,
  });
  s.addText("边缘自治", {
    x: 0.85, y: 6.44, w: 1.5, h: 0.28,
    fontSize: 10, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText("断网时采集与质检不停，恢复后补传", {
    x: 2.4, y: 6.46, w: 4.0, h: 0.26,
    fontSize: 8.5, color: C.gray, fontFace: "Calibri", margin: 0,
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 6.75, y: 6.32, w: 5.95, h: 0.62,
    fill: { color: C.cream }, line: { color: C.bg2, width: 1 }, rectRadius: 0.04,
  });
  s.addText("单一数据源", {
    x: 7.0, y: 6.44, w: 1.6, h: 0.28,
    fontSize: 10, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText("应用不直连设备，统一走数据服务 API 取数", {
    x: 8.65, y: 6.46, w: 4.0, h: 0.26,
    fontSize: 8.5, color: C.gray, fontFace: "Calibri", margin: 0,
  });

  footer(s);
}

// ═══════════════════════════════ P5 · 数据流 ★
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "数据流  ·  从设备到决策", "PART 1 · DATA FLOW");
  subTitle(s, "每一环写清：采什么、多快、存哪、谁用 —— 这决定了后面所有场景能不能做");

  const flow = [
    ["采集", C.edge, "工艺参数：温度/压力/转速/时间\n设备状态：运行/停机/报警\n产量计数、能耗读数、质检图像",
      "关键工序秒级\n一般工序分钟级\n图像按节拍触发"],
    ["传输", C.steel, "边缘网关协议转换后统一格式\n经消息队列缓冲上传\n断网本地落盘，恢复补传",
      "工业以太网\n与办公网隔离"],
    ["存储", C.plat, "时序库：参数与能耗\n关系库：工单/批次/质检/追溯\n对象存储：图像与报告",
      "保留期按追溯\n与审计要求定"],
    ["消费", C.app, "MES 排产与执行\n质检模型训练与推理\n追溯查询、能碳核算\n园区协同与对外接口",
      "统一走数据服务\n不直连设备"],
  ];

  flow.forEach((f, i) => {
    const x = 0.6 + i * 3.12;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 1.78, w: 2.9, h: 3.5,
      fill: { color: C.white }, line: { color: f[1], width: 1.5 }, rectRadius: 0.06,
    });
    s.addShape(p.ShapeType.rect, { x, y: 1.78, w: 2.9, h: 0.48, fill: { color: f[1] } });
    s.addText(f[0], {
      x: x + 0.2, y: 1.88, w: 2.5, h: 0.3,
      fontSize: 14, bold: true, color: C.white, fontFace: "Cambria", margin: 0,
    });
    s.addText(f[2], {
      x: x + 0.2, y: 2.42, w: 2.5, h: 1.75,
      fontSize: 8.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    });
    s.addShape(p.ShapeType.rect, {
      x: x + 0.2, y: 4.28, w: 2.5, h: 0.01, fill: { color: C.bg2 },
    });
    s.addText(f[3], {
      x: x + 0.2, y: 4.4, w: 2.5, h: 0.75,
      fontSize: 8, color: C.accentDk, fontFace: "Calibri",
      lineSpacing: 12, margin: 0, valign: "top",
    });
    if (i < 3) {
      s.addText("▶", {
        x: x + 2.96, y: 3.35, w: 0.28, h: 0.3,
        fontSize: 13, color: C.accent, fontFace: "Calibri", margin: 0,
      });
    }
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.5, w: 12.1, h: 1.4,
    fill: { color: "FDF3F2" }, line: { color: C.redAccent, width: 1.5 }, rectRadius: 0.05,
  });
  s.addText("数据量与频率需现场核定", {
    x: 0.9, y: 5.64, w: 5, h: 0.3,
    fontSize: 11, bold: true, color: C.redAccent, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "采集点位数、采样频率、图像分辨率与节拍直接决定存储容量与网络带宽的选型，进而决定投资额。这些数值取决于实际设备型号与产线节拍，" +
    "本方案阶段无法给出 —— 需在设备选型确定后，按工序梳理点位表（设备/参数名/类型/频率/精度）再行核算。\n" +
    "点位表是数字化建设的第一份交付物，也是后续所有工作的输入。",
    {
      x: 0.9, y: 5.98, w: 11.5, h: 0.85,
      fontSize: 9, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    }
  );
  footer(s);
}

// ═══════════════════════════════ P6 · 接入前提 ★ 产线在这里出现
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "接入前提  ·  产线与既有系统需满足的条件", "PART 1 · PREREQUISITES");
  subTitle(s, "产线怎么建由建设方与设备商定 —— 但不满足下列条件，数字化无法落地", C.redAccent);

  const groups = [
    ["设备侧", C.edge, [
      ["PLC / 控制器开放读取", "采购合同中约定开放数据读取权限与点位地址表，避免交付后被厂商锁定"],
      ["通信协议文档", "OPC UA / Modbus TCP 优先；私有协议须提供协议说明或厂商网关"],
      ["关键工序采集点位", "温度 / 压力 / 转速 / 时间等按工艺要求预留传感器安装位与信号输出"],
      ["设备唯一标识", "每台设备有固定编码，与台账、点位表、MES 中的资产一致"],
    ]],
    ["现场条件", C.steel, [
      ["工业网络与机房", "车间工业以太网布线、边缘节点机柜位置、供电与散热"],
      ["分表分项计量", "电 / 气 / 水计量装置到主要设备或工段，且具备远传接口"],
    ]],
    ["既有系统", C.plat, [
      ["ERP / 财务系统接口", "物料主数据、订单、成本科目的读取接口或数据库视图"],
      ["历史数据", "近 12 个月产量、质检、能耗记录 —— 用于建立指标基线（见 P14）"],
    ]],
  ];

  let cy = 1.75;
  groups.forEach((g) => {
    s.addShape(p.ShapeType.rect, { x: 0.6, y: cy, w: 12.1, h: 0.32, fill: { color: g[1] } });
    s.addText(g[0], {
      x: 0.78, y: cy + 0.04, w: 3, h: 0.24,
      fontSize: 10, bold: true, color: C.white, fontFace: "Cambria", margin: 0,
    });
    cy += 0.32;
    g[2].forEach((r, i) => {
      s.addShape(p.ShapeType.rect, {
        x: 0.6, y: cy, w: 12.1, h: 0.55,
        fill: { color: i % 2 ? C.white : C.cream }, line: { color: C.bg2, width: 0.5 },
      });
      s.addText(r[0], {
        x: 0.85, y: cy + 0.14, w: 3.3, h: 0.3,
        fontSize: 10, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
      });
      s.addText(r[1], {
        x: 4.3, y: cy + 0.15, w: 8.2, h: 0.3,
        fontSize: 8.8, color: C.gray, fontFace: "Calibri", margin: 0,
      });
      cy += 0.55;
    });
    cy += 0.14;
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 6.35, w: 12.1, h: 0.6,
    fill: { color: "FDF3F2" }, line: { color: C.redAccent, width: 1 }, rectRadius: 0.04,
  });
  s.addText(
    "★ 第一项最容易被忽略也最难补救 —— 设备采购合同签订时若未约定数据开放，后期加装采集会大幅增加成本，" +
    "部分封闭系统甚至无法接入。建议产线设备招标文件中写入数据接口条款。",
    {
      x: 0.9, y: 6.46, w: 11.5, h: 0.42,
      fontSize: 9, color: C.redAccent, fontFace: "Calibri",
      lineSpacing: 12, margin: 0, valign: "top",
    }
  );
  footer(s);
}

// ═══════════════════════════════ P7 · 场景一 · 全流程追溯
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "场景一  ·  生产全流程追溯", "PART 2 · TRACEABILITY");
  subTitle(s, "追溯是其它场景的地基 —— 质量分析、碳足迹、客户审厂都建立在批次链上");

  scenarioPage(s, {
    problem: "客户投诉或批次异常时，无法快速定位到具体原料批次与当时的工艺参数；" +
      "品牌客户审厂要求提供批次追溯能力，缺失会影响准入。",
    steps: [
      ["批次编码规则", "原料入库批次 / 混配批次 / 成型批次 / 成品条码四级编码，规则先定义再建系统"],
      ["工序间绑定", "每道工序开工时扫码绑定上道批次，形成父子关系链；系统强制校验，不允许跳过"],
      ["参数快照", "工序完成时将当时工艺参数、设备、班组、时间一并写入批次记录"],
      ["双向查询", "正查：这批原料流向了哪些成品；反查：这个成品用了哪批料、哪套参数"],
      ["对外接口", "向客户开放受限查询；向园区协同平台提供入驻企业所需的上游批次信息"],
    ],
    needs: [
      "批次编码规则由生产与质量部门确定 —— 这是业务决策不是技术决策",
      "各工序具备扫码或自动识别条件（工位终端 / 读码器）",
      "MES 工单与工序模型先行建立",
      "现场作业规范配套调整，否则会出现绕过系统操作",
    ],
    phase: ["PHASE 1 · 优先", "技术难度不高但涉及全流程改造，越早做后续场景成本越低；晚做则历史数据断层无法补"],
    phaseColor: C.app,
  });
  footer(s);
}

// ═══════════════════════════════ P8 · 场景二 · 在线视觉质检
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "场景二  ·  在线视觉质检", "PART 2 · VISUAL INSPECTION");
  subTitle(s, "从抽检升级到全检 · 生物基材料的外观缺陷（色差 / 气泡 / 裂纹 / 杂质）人工判定一致性差");

  scenarioPage(s, {
    problem: "抽检存在漏检风险，且判定标准因人而异；缺陷发现在成品阶段，" +
      "已经消耗了全部加工成本；缺陷与工艺参数的关联无法量化。",
    steps: [
      ["缺陷分类定义", "与质量部门共同定义缺陷类型与判定标准，形成标注规范 —— 这一步不做后面全是返工"],
      ["样本采集与标注", "产线实际生产中采集图像并标注；样本量按缺陷类型数与发生频次评估，需覆盖低频缺陷"],
      ["成像方案设计", "相机位置、光源、节拍与分辨率按产品形态与产线速度确定；成像不稳定则模型再好也无用"],
      ["边缘推理部署", "模型部署在边缘节点，本地推理避免网络时延；判定结果实时回传 MES"],
      ["联动与闭环", "判定不合格自动触发拦截或分拣；缺陷数据与该批次工艺参数关联，反哺场景三"],
    ],
    needs: [
      "产线稳定运行一段时间以采集足量样本 —— 新线投产初期缺陷分布不代表常态",
      "光照与安装条件满足成像要求（现场需预留相机与光源位置）",
      "质量部门参与标注与验收，模型判定标准需与人工判定对齐",
      "低频缺陷样本不足时，先做高频缺陷，不追求一次覆盖全部类型",
    ],
    phase: ["PHASE 2 · 数据就绪后", "依赖产线投产与样本积累；建议先上高频缺陷类型跑通闭环，再逐步扩展"],
    phaseColor: C.accent,
  });
  footer(s);
}

// ═══════════════════════════════ P9 · 场景三 · 工艺参数数字化与寻优
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "场景三  ·  工艺参数数字化与寻优", "PART 2 · PROCESS OPTIMIZATION");
  subTitle(s, "这是最有价值也最难的一项 —— 把老师傅的经验变成可复用、可向下游输出的数字资产");

  scenarioPage(s, {
    problem: "稻壳含水率与粒径随产地批次波动，需人工调参；调参依赖老师傅经验，" +
      "口口相传、离岗即断层；新品试制反复试错，周期长成本高。",
    steps: [
      ["参数结构化", "把现有配方与工艺卡从纸质/表格转为结构化数据：原料特性、参数组合、结果指标三段式"],
      ["历史数据回填", "整理既有批次记录建立初始数据集；数据质量决定后续一切，宁可少而准"],
      ["关联分析", "先做统计分析而非直接上模型 —— 找出哪些参数真正影响结果，剔除无关变量"],
      ["寻优模型", "在数据量支撑的前提下建立参数推荐模型，输入当批原料特性输出建议参数区间"],
      ["人机协同", "模型给建议、人工确认执行，执行结果回写形成闭环；不做无人干预的自动调参"],
    ],
    needs: [
      "原料入厂检测标准化 —— 含水率/粒径不测，模型就没有输入变量",
      "工艺参数与质量结果的配对数据；需场景一的批次链先建立",
      "工艺工程师深度参与 —— 这不是纯数据问题，需要机理知识约束",
      "数据积累周期较长，短期内先做知识库沉淀，模型后置",
    ],
    phase: ["PHASE 2-3 · 分步推进", "先做参数结构化与知识库（可较早启动），寻优模型待数据积累到位再上，不承诺短期见效"],
    phaseColor: C.redAccent,
  });
  footer(s);
}

// ═══════════════════════════════ P10 · 场景四 · 园区协同平台
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "场景四  ·  园区协同平台", "PART 2 · PARK COLLABORATION");
  subTitle(s, "这是园区级方案区别于单厂系统的地方 —— 单厂 MES 做不到跨企业协同");

  scenarioPage(s, {
    problem: "入驻企业各自为政：链主不知道下游产能余量，下游不知道材料何时到货；" +
      "大单需多家协同交付时靠人工协调；区内协同采购的政策申报缺少数据支撑。",
    steps: [
      ["企业接入分级", "按企业信息化程度分级接入：有系统的走接口，无系统的用轻量 Web 端录入，不强求统一"],
      ["共享看板", "订单进度、材料库存、产能余量按授权范围可见 —— 数据边界由入驻协议约定"],
      ["协同订单拆分", "大单按能力与产能拆分派工，各方进度回传汇总，链主侧统一对客户"],
      ["采购结算数据", "区内交易的合同与发票数据归集，直接支撑协同采购补贴申报（见 P15）"],
      ["权限与隔离", "企业间数据默认不可见，按协议逐项授权；平台方不得挪用企业经营数据"],
    ],
    needs: [
      "入驻协议中明确数据共享范围与边界 —— 这是法律前提不是技术问题",
      "入驻企业有意愿接入；招商阶段即应把平台接入写入协议",
      "链主侧 MES 先建成，否则协同没有数据源",
      "企业信息化水平差异大，需准备低门槛接入方式",
    ],
    phase: ["PHASE 3 · 招商到位后", "依赖首批企业入驻；建议先做共享看板等轻量功能，协同派工待业务量起来再做"],
    phaseColor: C.accent,
  });
  footer(s);
}

// ═══════════════════════════════ P11 · 场景五 · 能碳与碳足迹
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "场景五  ·  能碳计量与碳足迹核算", "PART 2 · CARBON ACCOUNTING");
  subTitle(s, "生物基材料的核心卖点是环境属性 —— 但卖点需要可核查的数据支撑，否则只是宣传语");

  scenarioPage(s, {
    problem: "能耗按整厂电表分摊，无法核算单位产品能耗；产品碳足迹拿不出核算依据，" +
      "影响零碳认证与欧盟碳边境调节机制（CBAM）等出口合规要求。",
    steps: [
      ["分项计量落地", "电/气/水计量到主要设备或工段并远传 —— 没有分表，后面全部无从谈起"],
      ["核算边界与方法学", "确定核算边界（从摇篮到大门 or 到坟墓）与采用的方法学标准；这是合规问题需先定"],
      ["因子库建立", "电力排放因子、原料与运输因子等；优先采用官方发布因子，自建部分需可溯源"],
      ["产品级分摊", "按批次实际能耗与产量分摊到单位产品，与场景一的批次链绑定"],
      ["报告与核查", "生成可供第三方核查的核算报告；数据留痕满足审计要求"],
    ],
    needs: [
      "分表分项计量装置在产线建设期同步预留（见 P6）—— 事后加装成本高",
      "核算方法学与认证目标需先确定，不同标准的边界和因子不同",
      "批次链已建立（场景一），否则无法分摊到产品",
      "第三方核查机构介入时点需提前规划",
    ],
    phase: ["PHASE 2 · 与产线同步", "计量装置必须在产线建设期一并考虑；核算系统可稍后，但硬件不能等"],
    phaseColor: C.app,
  });
  footer(s);
}

// ═══════════════════════════════ P12 · 集成边界
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "系统集成边界  ·  和谁接、接什么", "PART 3 · INTEGRATION");
  subTitle(s, "把接口关系写清楚，避免交付时出现「我以为对方负责」");

  const rows = [
    ["产线设备 / PLC", "设备侧 →", "工艺参数、设备状态、产量计数、报警",
      "设备供应商提供协议文档与点位表", "读取"],
    ["检测仪器", "设备侧 →", "在线检测值（含水率、粒径、厚度等）",
      "仪器需具备数字输出接口", "读取"],
    ["计量装置", "设备侧 →", "电 / 气 / 水分项读数",
      "需支持远传（RS485 / 网口）", "读取"],
    ["ERP / 财务", "双向", "物料主数据、订单、成本科目 / 生产完工与耗用回写",
      "客户 IT 或 ERP 供应商配合开接口", "双向"],
    ["视觉质检设备", "边缘侧", "图像采集与判定结果",
      "相机与光源随产线安装", "双向"],
    ["入驻企业系统", "园区侧", "订单、库存、产能（按授权范围）",
      "有系统走接口 / 无系统用 Web 端", "双向"],
    ["政府 / 监管报送", "对外 →", "能耗、碳排、安全等合规数据",
      "按主管部门要求的格式与频率", "上报"],
    ["品牌客户", "对外 →", "订单进度与质量追溯（受限查询）",
      "开放范围由商务协议约定", "查询"],
  ];

  const y0 = 1.75, rh = 0.6;
  s.addShape(p.ShapeType.rect, { x: 0.6, y: y0, w: 12.1, h: 0.36, fill: { color: C.primary } });
  ["集成对象", "方向", "交换内容", "责任方与前提"].forEach((h, i) => {
    const xs = [0.78, 3.05, 4.15, 8.75];
    s.addText(h, {
      x: xs[i], y: y0 + 0.07, w: 3, h: 0.24,
      fontSize: 9.5, bold: true, color: C.white, fontFace: "Calibri", margin: 0,
    });
  });
  rows.forEach((r, i) => {
    const y = y0 + 0.36 + i * rh;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: rh,
      fill: { color: i % 2 ? C.white : C.cream }, line: { color: C.bg2, width: 0.5 },
    });
    s.addText(r[0], {
      x: 0.78, y: y + 0.17, w: 2.2, h: 0.28,
      fontSize: 9.8, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(r[1], {
      x: 3.05, y: y + 0.18, w: 1.05, h: 0.26,
      fontSize: 8, color: C.accentDk, fontFace: "Calibri", margin: 0,
    });
    s.addText(r[2], {
      x: 4.15, y: y + 0.17, w: 4.5, h: 0.3,
      fontSize: 8.6, color: C.gray, fontFace: "Calibri", margin: 0,
    });
    s.addText(r[3], {
      x: 8.75, y: y + 0.17, w: 3.8, h: 0.3,
      fontSize: 8.6, color: C.grayLt, fontFace: "Calibri", margin: 0,
    });
  });

  s.addText(
    "注 · ERP 与设备两侧的接口开通需客户方推动供应商配合，通常是项目排期风险最大的两项",
    {
      x: 0.6, y: 6.68, w: 12.1, h: 0.3,
      fontSize: 9, color: C.accentDk, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P13 · 建设内容清单 (数字化侧)
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "建设内容清单  ·  数字化侧", "PART 3 · SCOPE OF WORK");
  subTitle(s, "仅列数字化范围 · 产线设备与厂房不在此表 · 投资金额待选型询价后概算");

  const rows = [
    ["01", "边缘采集与联网", "协议网关、边缘计算节点、采集代理、工位终端", "技改（智能化）"],
    ["02", "传感与计量装置", "关键工序补充传感器、分项计量表具及远传模块", "技改 · 绿色制造"],
    ["03", "工业网络与安全", "车间工业以太网、网络隔离、数据安全与权限管控", "技改"],
    ["04", "数据平台", "时序库、关系库、对象存储、消息队列、数据服务 API", "技改（软件）"],
    ["05", "MES 车间执行", "工单、排产、物料追溯、质量记录、设备与模具管理", "技改（软件）"],
    ["06", "视觉质检系统", "成像单元（相机/光源）、标注平台、模型训练与推理部署", "技改 · 关键技术"],
    ["07", "工艺知识库与寻优", "参数结构化、知识检索、关联分析与推荐模型", "关键技术攻关"],
    ["08", "能碳管理", "能耗采集、碳足迹核算、报告生成与合规报送", "绿色制造 / 技改"],
    ["09", "园区协同平台", "共享看板、协同订单、企业接入端、采购结算数据归集", "区内协同采购（配套）"],
    ["10", "集成与实施服务", "接口开发、数据迁移、部署调试、培训与试运行", "技改（服务）"],
  ];

  const y0 = 1.72, rh = 0.5;
  s.addShape(p.ShapeType.rect, { x: 0.6, y: y0, w: 12.1, h: 0.34, fill: { color: C.primary } });
  ["", "建设内容", "主要构成", "可对应政策"].forEach((h, i) => {
    const xs = [0.78, 1.3, 4.3, 10.3];
    if (!h) return;
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
      x: 0.78, y: y + 0.13, w: 0.45, h: 0.26,
      fontSize: 9.5, bold: true, color: C.accent, fontFace: "Cambria", margin: 0,
    });
    s.addText(r[1], {
      x: 1.3, y: y + 0.13, w: 2.9, h: 0.28,
      fontSize: 10, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(r[2], {
      x: 4.3, y: y + 0.14, w: 5.9, h: 0.28,
      fontSize: 8.6, color: C.gray, fontFace: "Calibri", margin: 0,
    });
    s.addText(r[3], {
      x: 10.3, y: y + 0.14, w: 2.3, h: 0.28,
      fontSize: 8.4, bold: true, color: C.accentDk, fontFace: "Calibri", margin: 0,
    });
  });

  s.addText(
    "注 · 02 项虽属硬件，但与数字化直接绑定（不装表就没有能耗数据）· 建议与产线建设同期实施，事后加装成本显著上升",
    {
      x: 0.6, y: 6.85, w: 12.1, h: 0.3,
      fontSize: 8.5, color: C.grayLt, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P14 · 技术指标框架
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "技术指标框架", "PART 3 · KPI FRAMEWORK");
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
    ["关键工序数控化率", "数控工序 / 关键工序总数"],
    ["设备联网率", "已联网 / 应联网设备数"],
    ["数据采集覆盖率", "已采集点位 / 应采集点位"],
    ["质量追溯覆盖率", "可追溯批次 / 总批次"],
    ["在线检测覆盖率", "在线全检工序 / 应检工序"],
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
    s.addText(c[0], {
      x: 6.9, y: y + 0.07, w: 3.0, h: 0.26,
      fontSize: 10, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(c[1], {
      x: 6.9, y: y + 0.32, w: 3.0, h: 0.24,
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
    "建立基线需采集：近 12 个月产量与工时台账 · 批次合格率与废品记录 · 分项能耗（现为整厂电表，需先装分表）· 新品开发周期记录。" +
    "  目标值的门槛要求以当年申报指南为准。",
    {
      x: 0.9, y: 6.04, w: 11.5, h: 0.8,
      fontSize: 9, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    }
  );
  footer(s);
}

// ═══════════════════════════════ P15 · 政策对接
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "临港政策对接  ·  按建设内容匹配", "PART 4 · POLICY MAP");
  subTitle(s, "下列为政策公示上限 · 实际额度取决于投资额与认定结果 · 本页不做加总测算", C.redAccent);

  const rows = [
    ["01-05  采集 / 网络 / 平台 / MES", "企业技术改造和智能化升级", "重点 5000 万 / 一般 1000 万", "固投比例 ≥ 60%"],
    ["02  计量与能碳硬件", "技术改造（绿色制造方向）", "同上", "并入技改项目"],
    ["06-07  质检与工艺模型", "支持关键核心技术突破", "新增投资 10-30% · 重点 3000 万", "填补国内空白"],
    ["07  工艺知识库（研发属性）", "设立研发总部 / 功能性平台", "项目总投资 50% · 最高 1000 万", "重点实验室等"],
    ["09  园区协同平台", "区内协同采购（平台为其提供数据支撑）", "采购发票额 10% · 1000 万 / 年", "双方无股权关联"],
    ["整体资金成本", "贷款贴息", "固投贷款利息 50% · 1000 万 / 年", "按实际支付利息"],
    ["整体税负", "企业所得税优惠", "减按 15% · 自设立起 5 年", "需实质性生产研发"],
  ];
  const y0 = 1.78, rh = 0.63;
  s.addShape(p.ShapeType.rect, { x: 0.6, y: y0, w: 12.1, h: 0.38, fill: { color: C.primary } });
  ["对应建设内容（P13 编号）", "政策名称", "支持上限", "主要条件"].forEach((h, i) => {
    const xs = [0.78, 4.3, 7.5, 10.6];
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
    const xs = [0.78, 4.3, 7.5, 10.6];
    const ws = [3.4, 3.1, 3.0, 2.0];
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

// ═══════════════════════════════ P16 · 申报要件对照
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "申报要件对照  ·  谁准备什么", "PART 4 · FILING CHECKLIST");
  subTitle(s, "申报主体为稻生万物 · 下列为技改类申报通用要件，具体以当年申报指南为准", C.redAccent);

  const rows = [
    ["技术改造和智能化升级", "可研报告 · 投资明细 · 软硬件合同 · 技改前后指标对比",
      "技术方案 · 架构与集成设计 · 建设清单（P13）· 指标框架（P14）", "财务报表 · 固投证明 · 合同与发票"],
    ["关键核心技术突破", "技术先进性说明 · 填补空白的证据",
      "质检与工艺模型技术路线说明", "科技查新报告 · 专利证书"],
    ["研发总部 / 功能性平台", "平台功能说明 · 研发人员名册 · 投入强度",
      "工艺知识库功能与建设方案", "人员社保 · 研发费用归集 · 场地证明"],
    ["区内协同采购", "采购合同与发票 · 无股权关联声明",
      "协同平台的交易数据归集与导出", "合同 · 发票 · 关联关系声明"],
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

// ═══════════════════════════════ P17 · 实施路径
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "实施路径  ·  按技术依赖排序", "PART 5 · ROADMAP");
  subTitle(s, "不按价值大小排，按依赖关系排 —— 地基没打好，上面的场景做了也是返工");

  const phases = [
    ["PHASE 1", "地基", "点位表梳理\n设备联网与数据采集\n分项计量装置\nMES 与批次追溯（场景一）",
      "此阶段不出炫目效果，但决定后面能不能做"],
    ["PHASE 2", "见效", "在线视觉质检（场景二）\n能碳计量与核算（场景五）\n工艺参数结构化与知识库",
      "依赖 P1 数据基础与产线投产后的样本积累"],
    ["PHASE 3", "优化与协同", "工艺寻优模型（场景三）\n园区协同平台（场景四）\n数据服务对外开放",
      "依赖数据积累与首批企业入驻"],
  ];
  phases.forEach((ph, i) => {
    const x = 0.6 + i * 4.13;
    const isFirst = i === 0;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 1.8, w: 3.9, h: 3.7,
      fill: { color: isFirst ? C.primary : C.white },
      line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
    });
    s.addText(ph[0], {
      x: x + 0.25, y: 2.05, w: 3.4, h: 0.28,
      fontSize: 10, bold: true, color: isFirst ? C.accent : C.accentDk,
      fontFace: "Calibri", charSpacing: 2, margin: 0,
    });
    s.addText(ph[1], {
      x: x + 0.25, y: 2.4, w: 3.4, h: 0.35,
      fontSize: 16, bold: true, color: isFirst ? C.white : C.primary,
      fontFace: "Cambria", margin: 0,
    });
    s.addText(ph[2], {
      x: x + 0.25, y: 2.95, w: 3.4, h: 1.7,
      fontSize: 9.5, color: isFirst ? C.cream : C.gray,
      fontFace: "Calibri", lineSpacing: 17, margin: 0, valign: "top",
    });
    s.addShape(p.ShapeType.rect, {
      x: x + 0.25, y: 4.75, w: 3.4, h: 0.01,
      fill: { color: isFirst ? C.primaryDk : C.bg2 },
    });
    s.addText(ph[3], {
      x: x + 0.25, y: 4.88, w: 3.4, h: 0.5,
      fontSize: 8.5, color: isFirst ? C.secondary : C.accentDk,
      fontFace: "Calibri", lineSpacing: 12, margin: 0, valign: "top",
    });
    if (i < 2) {
      s.addText("▶", {
        x: x + 3.94, y: 3.5, w: 0.28, h: 0.3,
        fontSize: 13, color: C.accent, fontFace: "Calibri", margin: 0,
      });
    }
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.7, w: 12.1, h: 1.2,
    fill: { color: C.cream }, line: { color: C.accent, width: 1 }, rectRadius: 0.05,
  });
  s.addText("两条硬约束", {
    x: 0.9, y: 5.84, w: 3, h: 0.28,
    fontSize: 11, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "① 传感器与计量装置必须在产线建设期同步安装 —— 事后加装需停线改造，成本与风险显著上升（见 P6 / P13-02）\n" +
    "② 设备采购合同须写入数据开放条款 —— 这一步错过，后面所有阶段都要付代价，且部分封闭系统无法补救",
    {
      x: 0.9, y: 6.16, w: 11.5, h: 0.68,
      fontSize: 9, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    }
  );
  s.addText("注 · 各阶段时长需结合产线建设周期确定，本页不列具体月份", {
    x: 0.6, y: 6.95, w: 12.1, h: 0.28,
    fontSize: 8.5, color: C.grayLt, fontFace: "Calibri", margin: 0,
  });
  footer(s);
}

// ═══════════════════════════════ P18 · 风险
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "关键风险与应对", "PART 5 · RISK");
  subTitle(s, "智能制造项目的典型失败模式 —— 多数不是技术问题");

  const risks = [
    ["设备数据拿不到", "采购合同未约定数据开放，厂商以商业机密为由拒绝或另行收费；封闭系统无法接入",
      "招标文件写入数据接口条款 · 选型阶段技术方参与评审"],
    ["系统建了没人用", "MES 与现场作业习惯脱节，工人绕过系统操作，数据失真，追溯链断裂",
      "关键用户全程参与设计 · 分阶段试点 · 作业规范同步调整 · 考核挂钩"],
    ["AI 期望过高", "在数据不足时强上高难度场景，效果不及预期，损伤整体信心与后续投入意愿",
      "按依赖排序（P17）· 先做视觉质检等确定性高的 · 寻优模型不承诺短期见效"],
    ["基线缺失无法验收", "指标基线未实测就申报，验收时拿不出改造前数据，无法证明提升幅度",
      "PHASE 1 首要任务即基线测评 · 采集方法见 P14"],
    ["入驻企业不接入", "协同平台建成但企业不愿共享数据，平台空转",
      "招商阶段即把接入写入入驻协议 · 提供低门槛接入方式 · 数据边界协议约定"],
    ["政策兑现不确定", "政策为上限值，实际获批取决于认定结果与年度资金安排",
      "不按上限做投资测算 · 提前与管委会预沟通申报口径"],
  ];
  risks.forEach((r, i) => {
    const y = 1.75 + i * 0.85;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: 0.75,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 },
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 0.05, h: 0.75, fill: { color: C.redAccent } });
    s.addText(r[0], {
      x: 0.85, y: y + 0.1, w: 2.3, h: 0.55,
      fontSize: 10.5, bold: true, color: C.primary, fontFace: "Cambria",
      margin: 0, valign: "middle",
    });
    s.addText(r[1], {
      x: 3.3, y: y + 0.08, w: 4.9, h: 0.6,
      fontSize: 8.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 12, margin: 0, valign: "middle",
    });
    s.addText(r[2], {
      x: 8.4, y: y + 0.08, w: 4.1, h: 0.6,
      fontSize: 8.5, color: C.accentDk, fontFace: "Calibri",
      lineSpacing: 12, margin: 0, valign: "middle",
    });
  });

  s.addText(
    "前两项是智能制造项目最常见的失败原因 —— 都不是技术问题，而是合同条款与组织配套的问题",
    {
      x: 0.6, y: 6.92, w: 12.1, h: 0.3,
      fontSize: 9, color: C.redAccent, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P19 · 下一步
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
    ["01", "现状调研与基线测评", "实地走访产线与既有系统 · 采集近 12 个月产量 / 质检 / 能耗数据\n没有基线，P14 的指标填不了，申报也报不了"],
    ["02", "点位表梳理", "按工序列出设备 / 参数名 / 数据类型 / 采样频率 / 精度要求\n这是数字化的第一份交付物，也是概算的输入"],
    ["03", "设备招标条款支持", "协助在产线设备招标文件中写入数据开放与接口条款\n这一步错过后面无法补救（P17 硬约束 ②）"],
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
      x: 5.5, y: y + 0.16, w: 6.5, h: 0.65,
      fontSize: 9.5, color: C.cream, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    });
  });
}

p.writeFile({ fileName: "daosheng-v17-digital.pptx" })
  .then(() => console.log("✓ daosheng-v17-digital.pptx  ·  19 页"));
