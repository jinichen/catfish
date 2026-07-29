// 稻生万物智能制造建设方案 · v30 · 24 页
//
// ═══ v30 vs v29 (7/28 鸿波第十八轮) ═══
//
// 鸿波: "你应该写出来的东西让人一眼就知道什么意思，而不要去多做解释"
//
// 起因: 鸿波问 P4 的「人在回路确认」是什么意思。
// 我的第一反应是加注解、加分级表 —— 错了。**要靠注解才说得清, 本身就是没写好。**
//
// ═══ 这一版只做一件事: 把行话换成一眼能懂的话 ═══
//
// 原则: 标签本身要携带意思, 不靠旁边的说明文字兜底。
// 特别是 P4 架构页 —— 它是纯方框图, 没有解释位, 标签写不清就真的看不懂。
//
//   调用能力池        → 调用工具与技能
//   人在回路确认      → 有后果的动作先交人确认
//   结果交付与回写    → 给出结果并写回系统
//   意图理解          → 听懂要做什么
//   任务分解与规划    → 拆解步骤与顺序
//   既有系统内嵌调用  → 在现有系统里直接用
//   统一语义模型      → 全域统一定义
//   时间序列错配      → 该现在启动的被拖住      (P16)
//   尚无物理载体      → 厂房与机房都还没有      (P16)
//   底座与冷启动      → 打底座 · 把知识灌进去    (P24)
//   接入与见效        → 接数据 · 出结果          (P24)
//   优化与对外开放    → 上模型 · 对下游开放      (P24)
//
// ═══ 没有做的事 ═══
//   没有加注解、没有加分级表、没有加脚注 —— 那是"多做解释"。
//   页数不变, 结构不变, 只换措辞。
//
// ═══ 沿用的检查项 ═══
//   build 后扫: 西里尔字母 / 全角空格 / 残留 Markdown `**`
//
// ═══ 延续的结构性决定 ═══
//   ① AI Native 骨架 ② 四个 Agent ③ 交付边界在数据接入层, 现场零部署
//   ④ 开源模型自部署 + 租赁算力 ⑤ 云优先是 PHASE 1 决策
//   ⑥ 不提申报 / 政策 / 补贴 ⑦ 不设「风险与应对」「下一步」
//   ⑧ 计算化学由外部机构承担, 本方负责结果入库与关联验证
//
// ═══ 军规 ═══
//   - 不点"鲶鱼"品牌名 · 不出现"自主研发/自研"
//   - **标签自带意思, 不靠注解兜底**
//   - 不把外部专业能力写成我方能力
//   - 不出现申报 / 政策 / 补贴 / 固投 / 管委会
//   - 不点具体开源模型名, 不给参数量 / 显存 / 准确率
//   - 配置 [待核定] · 指标基线 [待实测] / 目标 [待定]
//
// 数据来源:
//   背景 → 稻壳纤维产业集群介绍0126.pptx p6 p8-p11 p18

const pptxgen = require("pptxgenjs");

const p = new pptxgen();
p.layout = "LAYOUT_WIDE";
p.title = "稻生万物智能制造建设方案";
p.author = "稻生万物";

const C = {
  primary: "2C5F2D", primaryDk: "1F4220",
  secondary: "97BC62", accent: "D4A574", accentDk: "A87F51",
  cream: "F5F1E8", white: "FFFFFF", bg: "FAFAFA", bg2: "F0EDE4",
  dark: "1A1A1A", gray: "5C5C5C", grayLt: "999999",
  redAccent: "B85042",
  steel: "34495E",
  rd: "5B6C8F", mfg: "8B6F47", sales: "6B8E5A",
  ai: "6C4F8C",           // Agent 层专用色
};
const W = 13.3;
const TOTAL = 24;

let PN = 0;
function nextP() { PN++; return PN; }

/** 把 "**加粗**" 标记转成 pptxgenjs 富文本 runs —— 否则星号会原样渲染出来。
 *  v25 发现 P2/P16/P17 三处遗留了 Markdown 星号, 统一由此处理。 */
function md(text) {
  if (typeof text !== "string") return text;
  return text.split(/(\*\*[^*]+\*\*)/).filter(Boolean).map((seg) =>
    seg.startsWith("**") && seg.endsWith("**")
      ? { text: seg.slice(2, -2), options: { bold: true } }
      : { text: seg }
  );
}

function footer(s) {
  s.addText("稻生万物智能制造建设方案  ·  研产销一体化产业园  ·  上海临港新片区", {
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

/** Agent 页统一版式：面向谁+输入 / 能做什么 / 人在哪里 / 前提与分期 */
function agentPage(s, { serves, inputs, tasks, human, prereq, phase, color }) {
  // 左上 · 面向谁 · 输入
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 1.72, w: 4.35, h: 1.62,
    fill: { color: C.white }, line: { color: color, width: 1.5 }, rectRadius: 0.05,
  });
  s.addText("面向谁", {
    x: 0.82, y: 1.83, w: 3.9, h: 0.24,
    fontSize: 9, bold: true, color: color, fontFace: "Calibri", charSpacing: 1, margin: 0,
  });
  s.addText(serves, {
    x: 0.82, y: 2.08, w: 3.9, h: 0.3,
    fontSize: 10.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText("接收什么输入", {
    x: 0.82, y: 2.46, w: 3.9, h: 0.24,
    fontSize: 9, bold: true, color: color, fontFace: "Calibri", charSpacing: 1, margin: 0,
  });
  s.addText(inputs, {
    x: 0.82, y: 2.7, w: 3.9, h: 0.56,
    fontSize: 8.5, color: C.gray, fontFace: "Calibri",
    lineSpacing: 12, margin: 0, valign: "top",
  });

  // 左下 · 人在哪里
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 3.48, w: 4.35, h: 1.55,
    fill: { color: "FDF3F2" }, line: { color: C.redAccent, width: 1 }, rectRadius: 0.05,
  });
  s.addText("人在哪里  ·  Agent 不替代什么", {
    x: 0.82, y: 3.6, w: 3.9, h: 0.24,
    fontSize: 9, bold: true, color: C.redAccent, fontFace: "Calibri", margin: 0,
  });
  human.forEach((h, i) => {
    s.addText("—", {
      x: 0.82, y: 3.9 + i * 0.36, w: 0.22, h: 0.22,
      fontSize: 9, color: C.redAccent, fontFace: "Calibri", margin: 0,
    });
    s.addText(h, {
      x: 1.1, y: 3.88 + i * 0.36, w: 3.65, h: 0.34,
      fontSize: 8.3, color: C.gray, fontFace: "Calibri",
      lineSpacing: 11, margin: 0, valign: "top",
    });
  });

  // 左底 · 前提
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.17, w: 4.35, h: 1.28,
    fill: { color: C.cream }, line: { color: C.bg2, width: 1 }, rectRadius: 0.05,
  });
  s.addText("落地前提", {
    x: 0.82, y: 5.28, w: 3.9, h: 0.24,
    fontSize: 9, bold: true, color: C.accentDk, fontFace: "Calibri", margin: 0,
  });
  prereq.forEach((q, i) => {
    s.addText("·", {
      x: 0.82, y: 5.56 + i * 0.29, w: 0.14, h: 0.22,
      fontSize: 10, color: C.accent, fontFace: "Calibri", margin: 0,
    });
    s.addText(q, {
      x: 1.02, y: 5.54 + i * 0.29, w: 3.75, h: 0.28,
      fontSize: 8.2, color: C.gray, fontFace: "Calibri", margin: 0, valign: "top",
    });
  });

  // 右 · 能做什么
  s.addText("Agent 承担的任务", {
    x: 5.2, y: 1.75, w: 4, h: 0.28,
    fontSize: 11, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  tasks.forEach((t, i) => {
    const y = 2.1 + i * 0.86;
    s.addShape(p.ShapeType.rect, {
      x: 5.2, y, w: 7.5, h: 0.76,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 },
    });
    s.addShape(p.ShapeType.rect, { x: 5.2, y, w: 0.05, h: 0.76, fill: { color: color } });
    s.addText(t[0], {
      x: 5.42, y: y + 0.09, w: 2.9, h: 0.26,
      fontSize: 10.2, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(t[1], {
      x: 5.42, y: y + 0.36, w: 7.05, h: 0.36,
      fontSize: 8.3, color: C.gray, fontFace: "Calibri",
      lineSpacing: 11, margin: 0, valign: "top",
    });
    if (t[2]) {
      const dc = t[2] === "低" ? C.secondary : t[2] === "高" ? C.redAccent : C.accent;
      s.addShape(p.ShapeType.rect, { x: 12.0, y: y + 0.09, w: 0.55, h: 0.22, fill: { color: dc } });
      s.addText(t[2], {
        x: 12.0, y: y + 0.095, w: 0.55, h: 0.21,
        fontSize: 7.2, bold: true, color: C.white, fontFace: "Calibri",
        align: "center", margin: 0,
      });
    }
  });

  // 底 · 分期
  s.addShape(p.ShapeType.roundRect, {
    x: 5.2, y: 5.85, w: 7.5, h: 0.6,
    fill: { color: C.white }, line: { color: color, width: 1.5 }, rectRadius: 0.05,
  });
  s.addText(phase[0], {
    x: 5.42, y: 5.97, w: 1.9, h: 0.28,
    fontSize: 10, bold: true, color: color, fontFace: "Cambria", margin: 0,
  });
  s.addText(phase[1], {
    x: 7.4, y: 5.99, w: 5.1, h: 0.28,
    fontSize: 8.3, color: C.gray, fontFace: "Calibri", margin: 0,
  });
}

// ═══════════════════════════════ P1 · 封面
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.primary };
  s.addShape(p.ShapeType.rect, { x: 0, y: 0, w: W, h: 0.12, fill: { color: C.accent } });

  s.addText("A I   N A T I V E", {
    x: 1.1, y: 1.62, w: 11, h: 0.6,
    fontSize: 20, bold: true, color: C.accent,
    fontFace: "Calibri", charSpacing: 8, margin: 0,
  });
  s.addText("稻 生 万 物", {
    x: 1.1, y: 2.12, w: 11, h: 0.7,
    fontSize: 30, bold: true, color: C.cream,
    fontFace: "Cambria", charSpacing: 6, margin: 0,
  });
  s.addText("智 能 制 造 建 设 方 案", {
    x: 1.1, y: 2.76, w: 11, h: 0.9,
    fontSize: 40, bold: true, color: C.white,
    fontFace: "Cambria", charSpacing: 5, margin: 0,
  });
  s.addShape(p.ShapeType.rect, { x: 1.15, y: 3.82, w: 1.6, h: 0.04, fill: { color: C.accent } });
  s.addText("研产销一体化产业园  ·  上海临港新片区", {
    x: 1.1, y: 4.1, w: 11, h: 0.4,
    fontSize: 15, color: C.secondary, fontFace: "Calibri", charSpacing: 1, margin: 0,
  });

  const tags = [["研发 Agent", C.rd], ["生产 Agent", C.mfg],
                ["合规与客户 Agent", C.sales], ["园区 Agent", C.ai]];
  tags.forEach((t, i) => {
    const x = 1.1 + i * 2.82;
    s.addShape(p.ShapeType.rect, { x, y: 4.85, w: 2.6, h: 0.5, fill: { color: t[1] } });
    s.addText(t[0], {
      x, y: 4.96, w: 2.6, h: 0.3,
      fontSize: 10, bold: true, color: C.white, fontFace: "Calibri",
      align: "center", margin: 0,
    });
  });

  s.addText(
    "本方案覆盖智能制造的数字化与 AI 部分  ·  产线设备选型与工艺路线由建设方与设备供应商确定\n" +
    "本方案提供接入规范、数据标准与验收要求，现场零部署（交付边界见 P19）",
    {
      x: 1.1, y: 5.75, w: 11, h: 0.6,
      fontSize: 9.5, color: C.secondary, fontFace: "Calibri",
      lineSpacing: 15, margin: 0, valign: "top",
    }
  );
  s.addText("2026 年 7 月", {
    x: 1.1, y: 6.45, w: 11, h: 0.3,
    fontSize: 11, color: C.grayLt, fontFace: "Calibri", margin: 0,
  });
}

// ═══════════════════════════════ P2 · 为什么是 AI Native ★ 核心论证
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "为什么这个项目该是 AI Native", "WHY AI NATIVE");
  subTitle(s, "不是技术选型偏好 —— 是这个园区的商业模式决定的", C.redAccent);

  // 上 · 论证四步
  const logic = [
    ["1", "这家企业的核心资产是隐性知识",
      "配方经验 · 老师傅的调参手感 · 国际认证踩过的坑 · 品牌客户的对接经验\n—— 全部在人的脑子里，不在系统里"],
    ["2", "传统信息化管不住隐性知识",
      "它管的是流程、单据与报表，前提是**人先把知识填成表单**。\n但人不愿填、也填不完整 —— 这是所有知识管理系统失败的共同原因"],
    ["3", "而园区模式恰恰要求经验能规模化输出",
      "「链主赋能上下游」如果只能靠派工程师去教，赋能就无法规模化，\n园区相对于普通工厂的价值也就不成立"],
    ["4", "AI Native 把这件事反过来",
      "人照常工作，系统从过程中提取知识；下游用自然语言调用能力，不必学系统。\n入驻企业多为中小企业没有 IT 团队 —— 给系统账号用不起来，给 Agent 才用得动"],
  ];
  logic.forEach((l, i) => {
    const y = 1.72 + i * 1.02;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: 0.92,
      fill: { color: i === 3 ? C.cream : C.white },
      line: { color: i === 3 ? C.accent : C.bg2, width: i === 3 ? 1.5 : 1 },
    });
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 0.05, h: 0.92,
      fill: { color: i === 3 ? C.accent : C.grayLt },
    });
    s.addText(l[0], {
      x: 0.85, y: y + 0.28, w: 0.4, h: 0.34,
      fontSize: 17, bold: true, color: i === 3 ? C.accent : C.grayLt,
      fontFace: "Cambria", margin: 0,
    });
    s.addText(l[1], {
      x: 1.4, y: y + 0.12, w: 4.3, h: 0.7,
      fontSize: 11.5, bold: true, color: C.primary, fontFace: "Cambria",
      margin: 0, valign: "middle",
    });
    s.addText(md(l[2]), {
      x: 6.0, y: y + 0.12, w: 6.5, h: 0.7,
      fontSize: 8.8, color: C.gray, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "middle",
    });
  });

  // 下 · 一句话结论
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.9, w: 12.1, h: 1.02,
    fill: { color: C.primary }, line: { width: 0 }, rectRadius: 0.06,
  });
  s.addText("结论", {
    x: 0.9, y: 6.05, w: 1.2, h: 0.3,
    fontSize: 12, bold: true, color: C.accent, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "对稻生万物而言，AI Native 不是更时髦的技术路线，而是让「赋能」可规模化的唯一路径。\n" +
    "传统信息化能把这家工厂管好，但管不出一个能对外输出能力的园区。",
    {
      x: 2.2, y: 6.05, w: 10.2, h: 0.72,
      fontSize: 10, color: C.cream, fontFace: "Calibri",
      lineSpacing: 15, margin: 0, valign: "top",
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
    ["PART 1", "范式  ·  AI Native 与传统信息化的差别", "P4 - P6", C.ai],
    ["PART 2", "四个 Agent  ·  研 / 产 / 销 / 园区", "P7 - P10", null],
    ["PART 3", "底座  ·  模型 · 算力 · 机房 · 安全", "P11 - P18", null],
    ["PART 4", "边界  ·  交付分工 · 接入规范 · 治理", "P19 - P21", null],
    ["PART 5", "建设内容  ·  验收指标  ·  实施路径", "P22 - P24", null],
  ];
  parts.forEach((pt, i) => {
    const y = 2.0 + i * 0.95;
    if (pt[3]) {
      s.addShape(p.ShapeType.rect, { x: 0.62, y: y - 0.02, w: 0.14, h: 0.44, fill: { color: pt[3] } });
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
      x: 1.0, y: y + 0.54, w: 11.6, h: 0.01, fill: { color: C.bg2 },
    });
  });
  footer(s);
}

// ═══════════════════════════════ P4 · AI Native 总体架构 ★
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "总体架构", "PART 1 · ARCHITECTURE");
  subTitle(s, "以 Agent 编排为中心 —— 不是「应用模块 + 数据库」，而是「意图 → 能力 → 交付」");

  const layers = [
    ["交互层", C.secondary, C.dark,
      ["自然语言对话", "现场多模态输入\n(语音 / 拍照 / 扫码)", "主动推送与提醒", "在现有系统里直接用"]],
    ["Agent 编排层", C.ai, C.white,
      ["听懂要做什么", "拆解步骤与顺序", "调用工具与技能", "有后果的动作\n先交人确认", "给出结果\n并写回系统"]],
    ["能力与知识层", C.rd, C.white,
      ["自部署模型\n推理 / 嵌入 / 视觉", "工具\n查询 / 计算 / 调外部系统", "技能\n沉淀下来的作业流程", "知识与记忆\n行业 + 企业 + 案例"]],
    ["数据语义层", C.mfg, C.white,
      ["全域统一定义\n设备 / 批次 / 配方 / 客户", "数据存放\n时序 / 关系 / 文档", "统一取数口\n按权限放行"]],
    ["接入层", C.steel, C.white,
      ["设备与视觉数据\n(集成商交付)", "既有业务系统\n(ERP / 财务)", "文档与外部数据\n(标准/证书/客户来函)"]],
  ];

  let y = 1.7;
  layers.forEach((L, li) => {
    const h = li === 1 ? 1.0 : (li === 2 ? 1.08 : 0.94);
    const isAgent = li === 1;
    s.addShape(p.ShapeType.roundRect, {
      x: 0.6, y, w: 12.1, h,
      fill: { color: C.white }, line: { color: L[1], width: isAgent ? 2.5 : 1.2 },
      rectRadius: 0.06,
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 1.85, h, fill: { color: L[1] } });
    s.addText(L[0], {
      x: 0.6, y: y + h / 2 - 0.16, w: 1.85, h: 0.3,
      fontSize: isAgent ? 12 : 11, bold: true, color: L[2] === C.dark ? C.dark : C.white,
      fontFace: "Cambria", align: "center", margin: 0,
    });
    const n = L[3].length;
    const cw = (10.5 - (n - 1) * 0.12) / n;
    L[3].forEach((it, i) => {
      const cx = 2.6 + i * (cw + 0.12);
      s.addShape(p.ShapeType.roundRect, {
        x: cx, y: y + 0.13, w: cw, h: h - 0.26,
        fill: { color: isAgent ? C.cream : C.bg }, line: { color: C.bg2, width: 0.5 },
        rectRadius: 0.04,
      });
      s.addText(it, {
        x: cx + 0.06, y: y + 0.16, w: cw - 0.12, h: h - 0.32,
        fontSize: 8, color: C.dark, fontFace: "Calibri",
        align: "center", valign: "middle", lineSpacing: 10, margin: 0,
      });
    });
    y += h + 0.11;
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 6.5, w: 12.1, h: 0.5,
    fill: { color: C.cream }, line: { color: C.accent, width: 1 }, rectRadius: 0.04,
  });
  s.addText(
    "关键差别 · 传统架构里应用是「功能模块」，需求变了要开发；这里应用是「Agent + 能力组合」，新任务用配置技能实现（详见 P5）",
    {
      x: 0.9, y: 6.61, w: 11.5, h: 0.3,
      fontSize: 8.8, color: C.accentDk, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P5 · 与传统信息化的三个关键差别
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "三个关键差别", "PART 1 · WHAT CHANGES");
  subTitle(s, "这三条决定了它不是「传统系统 + AI 功能」，而是另一种建法");

  const diffs = [
    ["人不填表", "系统从过程中提取",
      "传统：先设计表单 → 培训 → 要求人按格式录入 → 数据质量取决于人的配合度",
      "本方案：人按习惯工作（语音记录 / 拍照 / 随手写），系统理解并结构化；\n人只做确认与修正，不承担录入负担"],
    ["能力可组合", "新需求不必开发",
      "传统：新场景 = 新功能 = 需求→开发→测试→上线，周期以月计",
      "本方案：新任务由既有工具与技能组合完成；确有价值的作业流程沉淀为技能后可复用，\n业务人员参与定义而非全靠开发"],
    ["知识自沉淀", "用得越久越懂这家企业",
      "传统：系统能力出厂即固定，知识留在文档与人脑，不进系统",
      "本方案：每次处置、每次判断、每个案例都进入知识与记忆层；\n新人与下游企业查得到「上次遇到这情况是怎么处理的」"],
  ];
  diffs.forEach((d, i) => {
    const y = 1.72 + i * 1.72;
    s.addShape(p.ShapeType.roundRect, {
      x: 0.6, y, w: 12.1, h: 1.6,
      fill: { color: C.white }, line: { color: C.ai, width: 1.5 }, rectRadius: 0.06,
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 2.9, h: 1.6, fill: { color: C.ai } });
    s.addText(d[0], {
      x: 0.75, y: y + 0.42, w: 2.6, h: 0.36,
      fontSize: 16, bold: true, color: C.white, fontFace: "Cambria",
      align: "center", margin: 0,
    });
    s.addText(d[1], {
      x: 0.75, y: y + 0.82, w: 2.6, h: 0.32,
      fontSize: 9, color: C.cream, fontFace: "Calibri",
      align: "center", margin: 0,
    });
    s.addText("传统", {
      x: 3.7, y: y + 0.2, w: 0.6, h: 0.24,
      fontSize: 8.5, bold: true, color: C.grayLt, fontFace: "Calibri", margin: 0,
    });
    s.addText(d[2], {
      x: 4.4, y: y + 0.18, w: 8.05, h: 0.42,
      fontSize: 8.5, color: C.grayLt, fontFace: "Calibri",
      lineSpacing: 12, margin: 0, valign: "top",
    });
    s.addShape(p.ShapeType.rect, {
      x: 3.7, y: y + 0.66, w: 8.75, h: 0.01, fill: { color: C.bg2 },
    });
    s.addText("本方案", {
      x: 3.7, y: y + 0.78, w: 0.8, h: 0.24,
      fontSize: 8.5, bold: true, color: C.ai, fontFace: "Calibri", margin: 0,
    });
    s.addText(d[3], {
      x: 4.6, y: y + 0.76, w: 7.85, h: 0.72,
      fontSize: 8.8, color: C.gray, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "top",
    });
  });

  s.addText(
    "注 · 第一条直接化解了知识管理最大的落地阻力：不再需要说服研发与一线「配合填写」",
    {
      x: 0.6, y: 6.92, w: 12.1, h: 0.3,
      fontSize: 8.8, color: C.accentDk, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P6 · 上下文工程 ★ 真正的差异化
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "上下文工程  ·  让通用模型变成懂这一行的助手", "PART 1 · CONTEXT ENGINEERING");
  subTitle(s, "通用大模型不懂稻壳改性、不知道这家客户的审厂标准 —— 上下文层是把通用能力变成专用能力的关键", C.redAccent);

  const ctx = [
    ["行业上下文", C.rd, "生物基复合材料的工艺机理与常见失效模式\n国内外认证标准体系与合规要求\n禁塑政策与市场应用场景",
      "来源：公开标准、技术文献、行业资料\n可先行构建，不依赖客户数据"],
    ["企业上下文", C.mfg, "这家的配方体系与工艺卡\n历史批次与质量档案\n客户要求、审厂标准与认证档案\n设备台账与产线约束",
      "来源：客户既有资料 + 运行中持续积累\n是差异化的核心，无法被通用模型替代"],
    ["实时上下文", C.sales, "当前订单与交付节点\n设备状态与在制批次\n原料入厂检测值\n入驻企业产能与协同状态",
      "来源：数据语义层实时供给\n决定 Agent 的建议是否切合当下情况"],
  ];
  ctx.forEach((c, i) => {
    const x = 0.6 + i * 4.13;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 1.72, w: 3.9, h: 3.4,
      fill: { color: C.white }, line: { color: c[1], width: 1.5 }, rectRadius: 0.06,
    });
    s.addShape(p.ShapeType.rect, { x, y: 1.72, w: 3.9, h: 0.44, fill: { color: c[1] } });
    s.addText(c[0], {
      x: x + 0.2, y: 1.81, w: 3.5, h: 0.3,
      fontSize: 13, bold: true, color: C.white, fontFace: "Cambria", margin: 0,
    });
    s.addText(c[2], {
      x: x + 0.22, y: 2.28, w: 3.5, h: 1.75,
      fontSize: 8.8, color: C.gray, fontFace: "Calibri",
      lineSpacing: 15, margin: 0, valign: "top",
    });
    s.addShape(p.ShapeType.rect, {
      x: x + 0.22, y: 4.15, w: 3.5, h: 0.01, fill: { color: C.bg2 },
    });
    s.addText(c[3], {
      x: x + 0.22, y: 4.26, w: 3.5, h: 0.75,
      fontSize: 8, color: C.accentDk, fontFace: "Calibri",
      lineSpacing: 12, margin: 0, valign: "top",
    });
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.32, w: 12.1, h: 1.6,
    fill: { color: C.cream }, line: { color: C.accent, width: 1.5 }, rectRadius: 0.06,
  });
  s.addText("为什么这一层是护城河而不是配置工作", {
    x: 0.9, y: 5.46, w: 6, h: 0.3,
    fontSize: 12, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "同一个通用模型，接上这三层上下文之后回答的是「这批含水率偏高的稻壳，按上次那批的处理方式应该把混炼温度往下调，" +
    "但注意当时出现过色差」；不接上下文只能回答教科书答案。\n\n" +
    "企业上下文随运行持续增厚 —— 用得越久越难被替代。这是数字化投入真正沉淀为资产的部分，" +
    "也是链主向下游输出技术时，实际交付出去的东西。",
    {
      x: 0.9, y: 5.78, w: 11.5, h: 1.05,
      fontSize: 9, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    }
  );
  footer(s);
}

// ═══════════════════════════════ P7 · 研发 Agent
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "研发 Agent", "PART 2 · R&D AGENT", C.rd, "研");
  subTitle(s, "不要求研发改变记录习惯 —— 从他们本来就在做的事里把知识提取出来");

  agentPage(s, {
    color: C.rd,
    serves: "研发与工艺人员 · 下游应用开发方",
    inputs: "实验过程记录（语音 / 拍照 / 随手写）· 检测报告 PDF\n中试与试制数据 · 下游企业的应用需求来函\n外部计算化学 / 分子模拟结果（如有合作方产出）",
    tasks: [
      ["实验记录自动结构化", "研发按习惯记录，Agent 抽取原料特性 / 配方 / 工艺条件 / 测试结果并入库，人只做确认", "低"],
      ["相似案例主动提示", "录入时自动检索历史相似工况：「三年前类似配方出现过色差，原因是…」", "中低"],
      ["配方版本与谱系维护", "自动维护版本链与变更原因，标明当前生效版本，避免用错版本试制", "低"],
      ["新品需求受理与拆解", "理解下游来函（邮件/文档）中的性能、成本、认证要求，转为结构化需求并列出待验证点", "中低"],
      ["候选方向建议", "按目标性能与约束从历史配方空间给出候选方向与依据；外部计算模拟结果（如界面相容性筛选）可一并纳入比对", "高"],
    ],
    human: [
      "配方决策与工艺定型 —— Agent 给依据，不做决定",
      "对下游开放的技术颗粒度由管理层定，Agent 按权限执行",
      "试制放行与量产转移的签字确认",
    ],
    prereq: [
      "既有实验记录与配方文档的归集（纸质需数字化）",
      "配方保密分级：哪些可对下游开放、开到什么程度",
      "检测报告可电子化获取，避免二次录入",
    ],
    phase: ["PHASE 1 · 可先行", "不依赖产线投产，可与产线建设并行；越早启动沉淀越厚"],
  });
  s.addText(
    "注 · 计算化学模拟（界面相容性、偶联剂与助剂筛选等）由高校院所或专业机构承担，不在本方案范围；" +
    "本方案负责其结果的结构化入库，并与实验、中试、产线数据关联验证 —— 使算过的东西沉淀为可复用资产，而非一次性报告",
    {
      x: 0.6, y: 6.6, w: 12.1, h: 0.44,
      fontSize: 8, color: C.accentDk, fontFace: "Calibri",
      lineSpacing: 11, margin: 0, valign: "top",
    }
  );
  footer(s);
}

// ═══════════════════════════════ P8 · 生产 Agent
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "生产 Agent", "PART 2 · PRODUCTION AGENT", C.mfg, "产");
  subTitle(s, "不是让人去查报表 —— 而是持续观察、发现异常、给出归因与处置建议");

  agentPage(s, {
    color: C.mfg,
    serves: "生产、工艺与质量人员",
    inputs: "设备参数与状态流（集成商采集）· 视觉质检输出的缺陷数据\n批次与工单信息 · 原料入厂检测值 · 订单变化",
    tasks: [
      ["批次链自动串联", "工序数据自动关联成批次父子链，追溯不靠人工查表：问「这批货怎么了」直接给结论", "低"],
      ["异常发现与归因", "持续比对参数、质检与历史基线，发现偏离后串联相关数据给出可能原因与证据", "中"],
      ["缺陷—参数关联", "把视觉厂商输出的缺陷与该批参数、原料特性关联，找出真正相关的变量", "中"],
      ["参数建议", "按当批原料特性给出建议参数区间并附依据（相似历史批次），工艺人员确认后执行", "高"],
      ["排产与插单影响评估", "理解交期、模具与产能约束，插单时给出重排方案与影响说明", "中"],
    ],
    human: [
      "参数调整的执行决定 —— 不做无人干预的自动调参",
      "质检争议批次的最终判定，标准解释权在质量部门",
      "停机、放行、报废等有成本后果的动作",
    ],
    prereq: [
      "设备数据已接入（集成商交付，见 P19 / P20）",
      "缺陷数据须含批次号与时间戳，否则无法与参数对齐",
      "原料入厂检测标准化 —— 不测则模型没有输入变量",
    ],
    phase: ["PHASE 1-3 · 分步", "追溯与异常发现随产线投产即可用；参数建议需批次积累，不承诺短期见效"],
  });
  footer(s);
}

// ═══════════════════════════════ P9 · 合规与客户 Agent
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "合规与客户 Agent", "PART 2 · COMPLIANCE & CUSTOMER AGENT", C.sales, "销");
  subTitle(s, "认证与客户要求本质上是「读文件、比对、组材料」—— 这正是语言模型最擅长的事");

  agentPage(s, {
    color: C.sales,
    serves: "销售、质量与合规人员 · 出海的入驻企业",
    inputs: "客户来函与审厂清单（邮件 / PDF）· 认证标准与法规文件\n既有证书与检测报告 · 订单与批次数据 · 客诉记录",
    tasks: [
      ["审厂清单解析与组包", "读懂客户清单，从档案库调取对应材料生成材料包，并列出缺失项与补齐建议", "低"],
      ["认证要求比对", "多市场标准差异比对；新市场准入或法规更新时主动提示对现有产品的影响", "低"],
      ["证书有效期跟踪", "到期前提醒并带出换证所需材料清单，避免过期断供", "低"],
      ["碳足迹核算与报告", "按既定方法学，用批次数据与分项能耗算到单位产品并生成可核查报告", "中"],
      ["客诉归因", "把投诉关联到批次、工艺参数与配方版本，定位根因并回流研发", "中"],
    ],
    human: [
      "对客户与监管的正式承诺与签字 —— Agent 只出草稿",
      "碳足迹核算边界与方法学的选定属合规决策",
      "客户资源向下游开放的范围由商务协议决定",
    ],
    prereq: [
      "既有认证材料归集入库（可先行，不依赖产线）",
      "批次链已建立，否则碳足迹无法分摊到产品",
      "分项计量装置随产线安装（见 P20）",
    ],
    phase: ["PHASE 1-2", "文档类任务可立即启动；碳足迹依赖计量与批次链，随产线同步"],
  });
  footer(s);
}

// ═══════════════════════════════ P10 · 园区 Agent ★ 赋能规模化的载体
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "园区 Agent  ·  把能力而不是账号开放给下游", "PART 2 · PARK AGENT", C.ai, "园");
  subTitle(s, "这一页是整个方案与园区商业模式的接点 —— 「链主赋能」在这里变成可规模化的产品", C.redAccent);

  // 上 · 三段能力如何对下游开放
  const opens = [
    ["研", C.rd, "技术咨询（受控）",
      "入驻企业用自然语言问「我这个应用场景该用什么牌号、注意什么」，\nAgent 按授权层级答：给参数区间与应用指导，不给核心配方"],
    ["产", C.mfg, "产能与代工协同",
      "查询共享产能余量、发起代工需求、跟踪材料到货与批次信息；\n协同订单的拆分与进度回传"],
    ["销", C.sales, "认证与出海支持",
      "复用链主的认证路径与送检模板；解析目标市场准入要求；\n园区统一交付文档的自动生成"],
  ];
  opens.forEach((o, i) => {
    const y = 1.72 + i * 1.12;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: 1.02,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 },
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 0.55, h: 1.02, fill: { color: o[1] } });
    s.addText(o[0], {
      x: 0.6, y: y + 0.34, w: 0.55, h: 0.34,
      fontSize: 16, bold: true, color: C.white, fontFace: "Cambria",
      align: "center", margin: 0,
    });
    s.addText(o[2], {
      x: 1.35, y: y + 0.16, w: 2.6, h: 0.32,
      fontSize: 11.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(o[3], {
      x: 4.2, y: y + 0.14, w: 8.3, h: 0.76,
      fontSize: 8.8, color: C.gray, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "middle",
    });
  });

  // 下 · 为什么这才叫可规模化
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.15, w: 5.95, h: 1.75,
    fill: { color: C.cream }, line: { color: C.accent, width: 1.5 }, rectRadius: 0.06,
  });
  s.addText("为什么这才叫可规模化", {
    x: 0.85, y: 5.28, w: 5.4, h: 0.28,
    fontSize: 11, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "派工程师去教：一次服务一家，人手即上限。\n" +
    "给系统账号：中小企业没有 IT 团队，给了也用不起来。\n" +
    "给 Agent：自然语言即可用，服务量不受人手约束，\n且每次问答都沉淀进企业上下文（P6）。",
    {
      x: 0.85, y: 5.6, w: 5.4, h: 1.2,
      fontSize: 8.8, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    }
  );

  s.addShape(p.ShapeType.roundRect, {
    x: 6.75, y: 5.15, w: 5.95, h: 1.75,
    fill: { color: "FDF3F2" }, line: { color: C.redAccent, width: 1.5 }, rectRadius: 0.06,
  });
  s.addText("开放边界必须先由协议约定", {
    x: 7.0, y: 5.28, w: 5.4, h: 0.28,
    fontSize: 11, bold: true, color: C.redAccent, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "Agent 按权限执行，但权限怎么设是商务与法律决策：\n" +
    "哪些技术资料可开放、开到什么颗粒度、客户资源如何导入、\n企业间数据如何隔离 —— 须在入驻协议中逐项约定。\n" +
    "「赋能变成失血」的防线在协议里，不在系统里。",
    {
      x: 7.0, y: 5.6, w: 5.4, h: 1.2,
      fontSize: 8.8, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    }
  );
  footer(s);
}

// ═══════════════════════════════ P11 · 底座 · 数据与知识
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "底座  ·  数据与知识层", "PART 3 · DATA & KNOWLEDGE");
  subTitle(s, "定位变了：不是给人查询的数据库，而是给 Agent 用的语义与知识供给");

  const left = [
    ["全域统一定义", "设备、批次、配方、客户、订单在全域用同一套定义 —— Agent 跨领域推理的前提。传统系统各管各的即可，这里必须统一"],
    ["多形态存储", "时序（参数与能耗）· 关系（工单/批次/订单）· 文档与对象（报告/证书/图像）—— 按数据形态选型，不强求单一库"],
    ["数据服务与权限", "Agent 通过统一服务取数，不直连底层；跨企业数据按授权隔离，权限在服务层强制"],
  ];
  const right = [
    ["知识层", "行业知识（标准/机理）+ 企业知识（配方/客户要求/案例）· 支持检索增强，回答附出处便于核对"],
    ["记忆层", "项目与会话上下文、处置经验、人对建议的采纳与否 —— 让系统持续贴近这家企业的实际"],
    ["技能沉淀", "被验证有效的作业流程固化为可复用技能，业务人员参与定义；这是「用得越久越好用」的机制"],
  ];
  [left, right].forEach((col, ci) => {
    s.addText(ci === 0 ? "数据侧  ·  供给事实" : "知识侧  ·  供给经验", {
      x: 0.6 + ci * 6.15, y: 1.75, w: 5.95, h: 0.3,
      fontSize: 12, bold: true, color: ci === 0 ? C.mfg : C.rd, fontFace: "Cambria", margin: 0,
    });
    col.forEach((item, i) => {
      const x = 0.6 + ci * 6.15;
      const y = 2.15 + i * 1.5;
      s.addShape(p.ShapeType.roundRect, {
        x, y, w: 5.95, h: 1.35,
        fill: { color: ci === 0 ? C.white : C.cream },
        line: { color: ci === 0 ? C.mfg : C.rd, width: 1 }, rectRadius: 0.05,
      });
      s.addText(item[0], {
        x: x + 0.24, y: y + 0.18, w: 5.5, h: 0.3,
        fontSize: 12, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
      });
      s.addText(item[1], {
        x: x + 0.24, y: y + 0.54, w: 5.5, h: 0.7,
        fontSize: 9, color: C.gray, fontFace: "Calibri",
        lineSpacing: 13, margin: 0, valign: "top",
      });
    });
  });

  s.addText(
    "注 · 数据治理在 AI Native 下不是可选项 —— 语义不统一、出处不可溯，Agent 的回答就不可信，也无法用于对外承诺",
    {
      x: 0.6, y: 6.72, w: 12.1, h: 0.3,
      fontSize: 8.8, color: C.accentDk, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P12 · 传统系统在 AI Native 里的位置 ★ 消除误解
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "MES 等传统系统还建不建？", "PART 3 · LEGACY SYSTEMS");
  subTitle(s, "建 —— 但定位变了：从「人操作的系统」变成「Agent 调用的工具与数据源」", C.redAccent);

  const rows = [
    ["MES 生产执行", "工单、排产、报工、追溯记录",
      "仍需建设：它是生产事实的记录者", "Agent 调它下发指令、取进度、写回结果；人不必天天进系统查"],
    ["质量管理", "检验规则、判定记录、不合格处置",
      "仍需建设：判定规则需可审计", "Agent 用它的规则做初判与拦截，争议交人工"],
    ["ERP / 财务", "物料、订单、成本科目",
      "沿用既有系统，不重复建", "作为数据源与写回目标接入"],
    ["视觉质检系统", "成像与缺陷判定",
      "由专业厂商成套交付", "输出缺陷数据供 Agent 归因（见 P19）"],
    ["报表与看板", "各类统计报表",
      "大幅减少：多数问题直接问即可", "保留合规报送与固定周期报表；探索性查询由对话完成"],
  ];

  const y0 = 1.78, rh = 0.86;
  s.addShape(p.ShapeType.rect, { x: 0.6, y: y0, w: 12.1, h: 0.4, fill: { color: C.primary } });
  ["系统", "承担什么", "还建不建", "在 AI Native 里的角色"].forEach((h, i) => {
    const xs = [0.78, 2.9, 5.6, 8.6];
    s.addText(h, {
      x: xs[i], y: y0 + 0.09, w: 3, h: 0.24,
      fontSize: 9.5, bold: true, color: C.white, fontFace: "Calibri", margin: 0,
    });
  });
  rows.forEach((r, i) => {
    const y = y0 + 0.4 + i * rh;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: rh,
      fill: { color: i % 2 ? C.white : C.cream }, line: { color: C.bg2, width: 0.5 },
    });
    const xs = [0.78, 2.9, 5.6, 8.6];
    const ws = [2.0, 2.6, 2.9, 3.9];
    r.forEach((cell, j) => {
      s.addText(cell, {
        x: xs[j], y: y + 0.1, w: ws[j], h: rh - 0.2,
        fontSize: j === 0 ? 10 : 8.5, bold: j === 0,
        color: j === 0 ? C.primary : (j === 2 ? C.accentDk : C.gray),
        fontFace: j === 0 ? "Cambria" : "Calibri",
        margin: 0, valign: "middle", lineSpacing: 12,
      });
    });
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 6.3, w: 12.1, h: 0.62,
    fill: { color: "FDF3F2" }, line: { color: C.redAccent, width: 1 }, rectRadius: 0.04,
  });
  s.addText(
    "★ AI Native 不等于「不要传统系统」—— 记录、规则、审计这些职责仍需系统承担。变的是人机界面：" +
    "人从「操作系统」转为「审阅与决策」，系统从「面向人的界面」转为「面向 Agent 的能力」。",
    {
      x: 0.9, y: 6.42, w: 11.5, h: 0.42,
      fontSize: 8.8, color: C.redAccent, fontFace: "Calibri",
      lineSpacing: 12, margin: 0, valign: "top",
    }
  );
  footer(s);
}

// ═══════════════════════════════ P13 · 部署拓扑 ★
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "部署拓扑  ·  四区划分与网络边界", "PART 3 · DEPLOYMENT TOPOLOGY");
  subTitle(s, "「车间零部署」指的是 OT 侧 —— 平台层仍需算力承载，部署在厂区机房或云，不进车间", C.redAccent);

  const zones = [
    ["OT  ·  车间现场区", C.steel,
      ["PLC / 控制器", "传感与计量装置", "协议网关 · 边缘节点", "视觉质检成套"],
      "集成商 / 视觉厂商交付 · 本方不部署"],
    ["DMZ  ·  数据接入区", C.accent,
      ["采集汇聚服务", "协议与格式转换", "数据质量校验", "断点缓冲与补传"],
      "OT 与 IT 之间的唯一通道 · 单向或受控双向"],
    ["IT  ·  平台承载区（厂区机房 / 私有云）", C.rd,
      ["数据层\n时序 / 关系 / 对象存储", "自部署模型服务\n推理 · 向量嵌入 · 视觉", "Agent 编排与应用\nMES · 业务系统", "运维与监控\n日志 · 备份 · 告警"],
      "本方案主要部署位置 · 算力选型见 P15"],
    ["接入  ·  用户与外部", C.sales,
      ["厂内办公终端", "移动端（现场/出差）", "入驻企业接入", "外部数据源\n（标准/法规更新）"],
      "按角色与企业分级授权 · 外部接入经边界防护"],
  ];

  let y = 1.72;
  zones.forEach((z, zi) => {
    const h = zi === 2 ? 1.42 : 1.12;
    s.addShape(p.ShapeType.roundRect, {
      x: 0.6, y, w: 12.1, h,
      fill: { color: C.white }, line: { color: z[1], width: zi === 2 ? 2.5 : 1.2 },
      rectRadius: 0.06,
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 2.5, h, fill: { color: z[1] } });
    s.addText(z[0], {
      x: 0.68, y: y + h / 2 - 0.34, w: 2.34, h: 0.5,
      fontSize: zi === 2 ? 10 : 11, bold: true, color: C.white,
      fontFace: "Cambria", align: "center", valign: "middle",
      lineSpacing: 13, margin: 0,
    });
    s.addText(z[3], {
      x: 0.68, y: y + h / 2 + 0.16, w: 2.34, h: 0.42,
      fontSize: 7.2, color: C.cream, fontFace: "Calibri",
      align: "center", lineSpacing: 9, margin: 0, valign: "top",
    });
    const n = z[2].length;
    const cw = (9.85 - (n - 1) * 0.12) / n;
    z[2].forEach((it, i) => {
      const cx = 3.25 + i * (cw + 0.12);
      s.addShape(p.ShapeType.roundRect, {
        x: cx, y: y + 0.16, w: cw, h: h - 0.32,
        fill: { color: zi === 2 ? C.cream : C.bg }, line: { color: C.bg2, width: 0.5 },
        rectRadius: 0.04,
      });
      s.addText(it, {
        x: cx + 0.06, y: y + 0.2, w: cw - 0.12, h: h - 0.4,
        fontSize: 8.2, color: C.dark, fontFace: "Calibri",
        align: "center", valign: "middle", lineSpacing: 11, margin: 0,
      });
    });
    // 区间边界标注
    if (zi < 3) {
      const labels = ["工业防火墙 / 网闸  ·  OT-IT 隔离",
                      "内网边界  ·  仅开放必要端口",
                      "外部边界  ·  防火墙 · 认证 · 审计"];
      s.addShape(p.ShapeType.rect, {
        x: 4.4, y: y + h + 0.015, w: 4.5, h: 0.2,
        fill: { color: C.redAccent },
      });
      s.addText(labels[zi], {
        x: 4.4, y: y + h + 0.025, w: 4.5, h: 0.18,
        fontSize: 7.2, bold: true, color: C.white, fontFace: "Calibri",
        align: "center", margin: 0,
      });
    }
    y += h + 0.24;
  });

  s.addText(
    "注 · 平台承载区可选私有化 / 混合 / 公有云三种形态，选型影响弹性、运维负担与成本结构 —— 见 P15",
    {
      x: 0.6, y: 6.95, w: 12.1, h: 0.28,
      fontSize: 8.5, color: C.accentDk, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P14 · 模型策略 · 开源自部署 ★
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "模型策略  ·  开源模型自部署 + 租赁算力", "PART 3 · MODEL STRATEGY");
  subTitle(s, "租算力 ≠ 用别人的模型服务 —— 算力是通用资源，模型自己部署，推理数据不出实例", C.redAccent);

  // 上 · 四条理由
  const why = [
    ["数据主权", "推理数据只在自己控制的实例内，不经过任何模型服务商。云厂商可接触虚拟化层，" +
      "但与「把 prompt 发给模型 API」是完全不同量级的暴露面"],
    ["★ 向量化尤其关键", "知识库需把全部配方文档、工艺记录、检测报告做 embedding。若走商业 API，" +
      "等于最敏感资料一次性全量发给第三方 —— 自部署则此问题不存在"],
    ["成本可控", "Agent 场景上下文长、调用频繁，按 token 计费规模上来后不可控；自部署只付算力，边际成本低"],
    ["可迁移 · 可微调", "开源权重不绑厂商，将来转本地机房直接搬走；且可在企业数据上继续训练，把能力沉淀为资产"],
  ];
  why.forEach((w, i) => {
    const x = 0.6 + (i % 2) * 6.15;
    const y = 1.68 + Math.floor(i / 2) * 1.06;
    const hi = i === 1;
    s.addShape(p.ShapeType.roundRect, {
      x, y, w: 5.95, h: 0.98,
      fill: { color: hi ? C.cream : C.white },
      line: { color: hi ? C.accent : C.bg2, width: hi ? 1.8 : 1 }, rectRadius: 0.05,
    });
    s.addText(w[0], {
      x: x + 0.22, y: y + 0.1, w: 5.5, h: 0.26,
      fontSize: 11, bold: true, color: hi ? C.accentDk : C.primary,
      fontFace: "Cambria", margin: 0,
    });
    s.addText(w[1], {
      x: x + 0.22, y: y + 0.38, w: 5.5, h: 0.52,
      fontSize: 8.2, color: C.gray, fontFace: "Calibri",
      lineSpacing: 11, margin: 0, valign: "top",
    });
  });

  // 中 · 模型分层
  s.addText("模型分层  ·  按任务选型，不是一个模型打天下", {
    x: 0.6, y: 3.88, w: 7, h: 0.28,
    fontSize: 11.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const tiers = [
    ["主力推理", C.ai, "意图理解 · 任务规划 · 归因分析 · 内容生成", "开源通用大模型"],
    ["向量嵌入", C.rd, "知识检索 · 相似案例匹配 —— 数据敏感度最高的一环", "开源 embedding 模型"],
    ["视觉理解", C.mfg, "现场拍照识别 · 文档版面解析 · 证书与报告提取", "开源多模态模型"],
    ["轻量抽取", C.sales, "结构化字段抽取 · 分类 · 规则判定，资源占用低", "小参数模型"],
  ];
  tiers.forEach((t, i) => {
    const y = 4.22 + i * 0.54;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: 0.5,
      fill: { color: i % 2 ? C.white : C.cream }, line: { color: C.bg2, width: 0.5 },
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 0.05, h: 0.5, fill: { color: t[1] } });
    s.addText(t[0], {
      x: 0.85, y: y + 0.13, w: 1.6, h: 0.26,
      fontSize: 10, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(t[2], {
      x: 2.6, y: y + 0.14, w: 6.6, h: 0.24,
      fontSize: 8.4, color: C.gray, fontFace: "Calibri", margin: 0,
    });
    s.addText(t[3], {
      x: 9.4, y: y + 0.14, w: 3.1, h: 0.24,
      fontSize: 8.4, bold: true, color: C.accentDk, fontFace: "Calibri", margin: 0,
    });
  });

  // 下 · 能力边界（诚实）
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 6.5, w: 8.1, h: 0.94,
    fill: { color: "FDF3F2" }, line: { color: C.redAccent, width: 1.5 }, rectRadius: 0.05,
  });
  s.addText("能力边界  ·  不预设「开源等于闭源」", {
    x: 0.85, y: 6.6, w: 5, h: 0.24,
    fontSize: 10, bold: true, color: C.redAccent, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "开源模型在复杂推理与长上下文理解上与顶尖闭源仍有差距。选型须在真实任务上实测（读审厂清单、解析标准差异、缺陷归因），不预设结论。\n" +
    "若某类任务实测确实不足：脱敏后调用商业 API 作为兜底，但须经数据分级审批，且核心配方类内容不得外发。",
    {
      x: 0.85, y: 6.86, w: 7.6, h: 0.54,
      fontSize: 7.8, color: C.gray, fontFace: "Calibri",
      lineSpacing: 10.5, margin: 0, valign: "top",
    }
  );

  s.addShape(p.ShapeType.roundRect, {
    x: 8.9, y: 6.5, w: 3.8, h: 0.94,
    fill: { color: C.cream }, line: { color: C.bg2, width: 1 }, rectRadius: 0.05,
  });
  s.addText("运维与迭代", {
    x: 9.12, y: 6.6, w: 3.4, h: 0.24,
    fontSize: 10, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "推理框架、显存与并发管理带来运维复杂度，高于调用 API。\n" +
    "开源模型迭代快，需建立定期评估与替换机制。",
    {
      x: 9.12, y: 6.86, w: 3.4, h: 0.54,
      fontSize: 7.8, color: C.gray, fontFace: "Calibri",
      lineSpacing: 10.5, margin: 0, valign: "top",
    }
  );
}

// ═══════════════════════════════ P15 · 部署形态与算力 ★ 含固投比例提醒
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "部署形态与算力", "PART 3 · COMPUTE & HOSTING");
  subTitle(s, "三种形态的技术与成本差异 · 当前阶段建议见 P16");

  // 上 · 三方案对比
  const opts = [
    ["全私有化", C.rd,
      "配方等核心数据不出厂\n运维能力要求高\n自有机房 or 托管 → 见 P17\n扩容需重新采购",
      "弹性差\n须按峰值配置"],
    ["混合部署", C.accent,
      "敏感数据与核心模型本地\n通用模型能力调云 API\n按需扩展弹性算力\n架构与运维较复杂",
      "敏感在本地\n弹性用云端"],
    ["全公有云", C.secondary,
      "上线快、按需伸缩\n模型自部署则数据不经模型服务商\n长期为持续性支出\n★ 当前阶段建议 → 见 P16",
      "弹性最好\n持续性支出"],
  ];
  opts.forEach((o, i) => {
    const x = 0.6 + i * 4.13;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 1.7, w: 3.9, h: 2.5,
      fill: { color: C.white }, line: { color: o[1], width: 1.5 }, rectRadius: 0.06,
    });
    s.addShape(p.ShapeType.rect, { x, y: 1.7, w: 3.9, h: 0.42, fill: { color: o[1] } });
    s.addText(o[0], {
      x: x + 0.2, y: 1.78, w: 3.5, h: 0.3,
      fontSize: 13, bold: true, color: C.white, fontFace: "Cambria", margin: 0,
    });
    s.addText(o[2], {
      x: x + 0.22, y: 2.25, w: 3.5, h: 1.15,
      fontSize: 8.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "top",
    });
    s.addShape(p.ShapeType.rect, {
      x: x + 0.22, y: 3.5, w: 3.5, h: 0.01, fill: { color: C.bg2 },
    });
    s.addText(o[3], {
      x: x + 0.22, y: 3.6, w: 3.5, h: 0.5,
      fontSize: 8.5, bold: true, color: C.accentDk, fontFace: "Calibri",
      lineSpacing: 12, margin: 0, valign: "top",
    });
  });

  // 中 · 算力分类
  s.addText("算力与存储分类  ·  配置需核定后填入", {
    x: 0.6, y: 4.32, w: 6, h: 0.3,
    fontSize: 11.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const compute = [
    ["通用计算", "平台服务 · 数据库 · 业务应用", "按并发用户数与数据量核定", "[待核定]"],
    ["AI 推理", "模型服务 · 向量检索 · 嵌入计算", "按调用频次与模型规模核定", "[待核定]"],
    ["存储", "时序数据 · 图像与文档 · 备份", "按点位数×频率×保留期核定", "[待核定]"],
    ["网络", "内网带宽 · 外网出口 · 专线", "按图像回传量与接入企业数核定", "[待核定]"],
  ];
  compute.forEach((c, i) => {
    const y = 4.68 + i * 0.5;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: 0.46,
      fill: { color: i % 2 ? C.white : C.cream }, line: { color: C.bg2, width: 0.5 },
    });
    s.addText(c[0], {
      x: 0.78, y: y + 0.11, w: 1.5, h: 0.26,
      fontSize: 9.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(c[1], {
      x: 2.4, y: y + 0.12, w: 4.3, h: 0.24,
      fontSize: 8.4, color: C.gray, fontFace: "Calibri", margin: 0,
    });
    s.addText(c[2], {
      x: 6.9, y: y + 0.12, w: 4.3, h: 0.24,
      fontSize: 8.4, color: C.accentDk, fontFace: "Calibri", margin: 0,
    });
    s.addText(c[3], {
      x: 11.4, y: y + 0.12, w: 1.2, h: 0.24,
      fontSize: 8.5, bold: true, color: C.redAccent, fontFace: "Calibri", margin: 0,
    });
  });

  // 下 · 固投比例红框
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 6.72, w: 12.1, h: 0.55,
    fill: { color: "FDF3F2" }, line: { color: C.redAccent, width: 1.5 }, rectRadius: 0.04,
  });
  s.addText(
    "★ 配置核定的前置条件 · 上表四类均需先有：① 点位表（决定时序数据量）② 模型调用量预估（决定推理算力）" +
    "③ 图像采集节拍与保留期（决定存储与带宽）。三者缺一，规格就只能拍脑袋。",
    {
      x: 0.9, y: 6.84, w: 11.5, h: 0.38,
      fontSize: 8.5, color: C.redAccent, fontFace: "Calibri",
      lineSpacing: 11, margin: 0, valign: "top",
    }
  );
  footer(s);
}

// ═══════════════════════════════ P16 · 算力策略 · 当前阶段建议 ★
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "算力策略  ·  当前阶段建议云优先", "PART 3 · COMPUTE STRATEGY");
  subTitle(s, "这是 PHASE 1 的决策，不是永久架构决策 —— 用量摸清后再评估形态", C.redAccent);

  // 左 · 为什么现在该用云
  s.addText("为什么当前条件下云优先", {
    x: 0.6, y: 1.68, w: 5.5, h: 0.28,
    fontSize: 11.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const why = [
    ["需求量算不出来", "点位表尚未梳理，服务器与存储按什么规格采购无从判断；自建与托管都要求现在按峰值一次性投入"],
    ["该现在启动的被拖住", "研发数据结构化、认证档案入库不依赖产线，现在即可启动；等机房建成再开工等于推迟整个 PHASE 1"],
    ["厂房与机房都还没有", "用地未定、厂房未建，自建机房需等待土建；托管亦需选商、签约、上架"],
    ["无 7×24 运维能力", "制造企业 IT 团队通常不具备轮班值守（见 P17 对比）"],
    ["AI 算力的特殊性", "自部署开源模型需 GPU 常驻；但采购周期长、贬值快，起步阶段负载极低易闲置，租赁可按需伸缩"],
  ];
  why.forEach((w, i) => {
    const y = 2.02 + i * 0.94;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 5.95, h: 0.86,
      fill: { color: C.white }, line: { color: C.bg2, width: 1 },
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 0.05, h: 0.86, fill: { color: C.secondary } });
    s.addText(w[0], {
      x: 0.85, y: y + 0.1, w: 5.4, h: 0.26,
      fontSize: 10.2, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(w[1], {
      x: 0.85, y: y + 0.36, w: 5.5, h: 0.44,
      fontSize: 8.2, color: C.gray, fontFace: "Calibri",
      lineSpacing: 11, margin: 0, valign: "top",
    });
  });

  // 右上 · 三个代价
  s.addText("必须摆上台面的三个代价", {
    x: 6.75, y: 1.68, w: 5.5, h: 0.28,
    fontSize: 11.5, bold: true, color: C.redAccent, fontFace: "Cambria", margin: 0,
  });
  const costs = [
    ["数据在租赁环境中",
      md("因模型自部署（P14），推理与向量数据**不经模型服务商**，暴露面仅为虚拟化层。\n" +
      "缓解：传输与静态加密、密钥自持、专有网络。\n" +
      "若对外口径含「核心技术自主可控」，仍需在表述上界定清楚")],
    ["长期成本与厂商锁定",
      "规模上来后云总成本可能反超自建。架构上须避免绑定专有服务，保持数据与模型可导出，预留迁移路径"],
  ];
  costs.forEach((c, i) => {
    const h = 1.55;
    const y = 2.02 + i * 1.68;
    s.addShape(p.ShapeType.roundRect, {
      x: 6.75, y, w: 5.95, h,
      fill: { color: "FDF3F2" },
      line: { color: C.redAccent, width: 1.2 }, rectRadius: 0.05,
    });
    s.addText(c[0], {
      x: 6.98, y: y + 0.11, w: 5.5, h: 0.26,
      fontSize: 10.2, bold: true, color: C.redAccent, fontFace: "Cambria", margin: 0,
    });
    s.addText(c[1], {
      x: 6.98, y: y + 0.38, w: 5.5, h: h - 0.5,
      fontSize: 7.8, color: C.gray, fontFace: "Calibri",
      lineSpacing: 10.5, margin: 0, valign: "top",
    });
  });

  // 底 · 分阶段路线 + 现在就要办的三件事
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 6.78, w: 12.1, h: 0.68,
    fill: { color: C.primary }, line: { width: 0 }, rectRadius: 0.05,
  });
  s.addText("分阶段", {
    x: 0.85, y: 6.86, w: 1.1, h: 0.24,
    fontSize: 9, bold: true, color: C.accent, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "PHASE 1 云优先（需求未知、快速启动）  →  PHASE 2 用量摸清后评估：稳定负载可迁本地、弹性负载留云  →  PHASE 3 按数据分级与总体拥有成本定最终形态",
    {
      x: 2.0, y: 6.87, w: 10.5, h: 0.24,
      fontSize: 8, color: C.cream, fontFace: "Calibri", margin: 0,
    }
  );
  s.addText("现在就办", {
    x: 0.85, y: 7.14, w: 1.1, h: 0.24,
    fontSize: 9, bold: true, color: C.accent, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "① 完成数据分级：定清哪些绝对不能出厂区   ② 开源模型在真实任务上实测选型   ③ 预估调用量与数据量，作为算力核定输入   ④ 架构避免厂商锁定，保留迁移路径",
    {
      x: 2.0, y: 7.15, w: 10.5, h: 0.24,
      fontSize: 8, color: C.cream, fontFace: "Calibri", margin: 0,
    }
  );
}

// ═══════════════════════════════ P17 · 算力中心 · 自有机房 vs 租赁托管 ★
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "算力中心  ·  自有机房 vs 租赁托管", "PART 3 · DATA CENTER SITING");
  subTitle(s, "当前阶段建议云优先（见 P16）· 本页为未来负载稳定后若转本地化的选型依据");

  // 概念澄清条
  const clar = [
    ["租赁机房（托管 / Colocation）", "设备自购，放进专业机房的机柜", "一次性投入 · 专业环境保障 · 扩容需再采购", C.secondary],
    ["租用云服务器（IaaS）", "按量订阅算力资源", "无一次性投入 · 弹性伸缩 · 长期为持续支出", C.rd],
  ];
  clar.forEach((c, i) => {
    const x = 0.6 + i * 6.15;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 1.68, w: 5.95, h: 0.72,
      fill: { color: i ? "FDF3F2" : C.cream }, line: { color: c[3], width: 1.5 }, rectRadius: 0.05,
    });
    s.addText(c[0], {
      x: x + 0.2, y: 1.76, w: 5.5, h: 0.26,
      fontSize: 10.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(c[1], {
      x: x + 0.2, y: 2.02, w: 3.2, h: 0.24,
      fontSize: 8.2, color: C.gray, fontFace: "Calibri", margin: 0,
    });
    s.addText(c[2], {
      x: x + 0.2, y: 2.14, w: 5.5, h: 0.24,
      fontSize: 8.5, bold: true, color: c[3], fontFace: "Calibri", margin: 0,
    });
  });
  s.addText("★ 两者常被混为一谈：托管仍是自购设备，只是放在专业机房；云主机则不持有硬件。运维责任与成本结构完全不同", {
    x: 0.6, y: 2.44, w: 12.1, h: 0.24,
    fontSize: 8.5, bold: true, color: C.accentDk, fontFace: "Calibri", margin: 0,
  });

  // 安全维度对比
  const rows = [
    ["物理门禁与人员管控", "自主可控，但执行度依赖内部管理", "多级门禁 · 生物识别 · 7×24 值守", "托管"],
    ["环境保障", "UPS / 柴发 / 精密空调 / 气体消防需自建", "双路市电与上述配套为标配", "托管"],
    ["第三方人员接触", "无外部人员可物理接触设备", "服务商运维理论可接触（笼式机柜+封条缓解）", "自建"],
    ["数据物理位置", "不出厂区，数据主权清晰", "需专线传输，链路须加密", "自建"],
    ["容灾能力", "单点，异地容灾须另行建设", "多机房 / 多可用区可选", "托管"],
    ["等保合规", "机房部分需自行测评与整改", "可继承服务商机房等保资质", "托管"],
    ["7×24 运维", "制造企业 IT 团队通常不具备轮班能力", "由服务商承担", "托管"],
  ];
  const y0 = 2.76, rh = 0.5;
  s.addShape(p.ShapeType.rect, { x: 0.6, y: y0, w: 12.1, h: 0.32, fill: { color: C.primary } });
  ["安全维度", "自有机房", "租赁托管", "占优"].forEach((h, i) => {
    const xs = [0.78, 3.5, 7.4, 11.9];
    s.addText(h, {
      x: xs[i], y: y0 + 0.05, w: 3, h: 0.22,
      fontSize: 9, bold: true, color: C.white, fontFace: "Calibri", margin: 0,
    });
  });
  rows.forEach((r, i) => {
    const y = y0 + 0.32 + i * rh;
    s.addShape(p.ShapeType.rect, {
      x: 0.6, y, w: 12.1, h: rh,
      fill: { color: i % 2 ? C.white : C.cream }, line: { color: C.bg2, width: 0.5 },
    });
    s.addText(r[0], {
      x: 0.78, y: y + 0.13, w: 2.6, h: 0.26,
      fontSize: 9, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(r[1], {
      x: 3.5, y: y + 0.14, w: 3.8, h: 0.24,
      fontSize: 8, color: C.gray, fontFace: "Calibri", margin: 0,
    });
    s.addText(r[2], {
      x: 7.4, y: y + 0.14, w: 4.35, h: 0.24,
      fontSize: 8, color: C.gray, fontFace: "Calibri", margin: 0,
    });
    const win = r[3] === "托管";
    s.addShape(p.ShapeType.rect, {
      x: 11.85, y: y + 0.13, w: 0.78, h: 0.24,
      fill: { color: win ? C.secondary : C.rd },
    });
    s.addText(r[3], {
      x: 11.85, y: y + 0.14, w: 0.78, h: 0.22,
      fontSize: 7.5, bold: true, color: C.white, fontFace: "Calibri",
      align: "center", margin: 0,
    });
  });

  // 结论 + 本项目特有
  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 6.38, w: 6.5, h: 0.82,
    fill: { color: C.cream }, line: { color: C.accent, width: 1.5 }, rectRadius: 0.05,
  });
  s.addText("结论与建议", {
    x: 0.82, y: 6.46, w: 3, h: 0.24,
    fontSize: 9.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    md("纯安全角度：专业 IDC 在物理安防与可用性上通常优于自建厂区机房 —— 自建的「安全」多为心理上的。\n" +
    "建议混合：配方与工艺模型放厂区小型机房物理隔离，一般算力托管。前置动作是**数据分级**：先定清楚哪些绝对不能出厂区。"),
    {
      x: 0.82, y: 6.7, w: 6.1, h: 0.46,
      fontSize: 7.6, color: C.gray, fontFace: "Calibri",
      lineSpacing: 10, margin: 0, valign: "top",
    }
  );

  s.addShape(p.ShapeType.roundRect, {
    x: 7.3, y: 6.38, w: 5.4, h: 0.82,
    fill: { color: "FDF3F2" }, line: { color: C.redAccent, width: 1.5 }, rectRadius: 0.05,
  });
  s.addText("本项目特有的物理风险", {
    x: 7.52, y: 6.46, w: 4, h: 0.24,
    fontSize: 9.5, bold: true, color: C.redAccent, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "① 粉尘：稻壳除杂粉碎工序产尘，厂区自建机房防尘等级不足将影响设备寿命与故障率\n" +
    "② GPU 功率密度：AI 推理算力功耗与散热要求高，按办公场景设计的机房往往不足，需专项核算",
    {
      x: 7.52, y: 6.7, w: 5.0, h: 0.46,
      fontSize: 7.6, color: C.gray, fontFace: "Calibri",
      lineSpacing: 10, margin: 0, valign: "top",
    }
  );

  s.addText("注 · 转本地化的时点建议以「负载稳定且用量可预测」为判据，而非以时间表推动", {
    x: 0.6, y: 7.24, w: 12.1, h: 0.22,
    fontSize: 7.5, color: C.grayLt, fontFace: "Calibri", margin: 0,
  });
}

// ═══════════════════════════════ P18 · 安全与合规体系 ★
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "安全与合规体系", "PART 3 · SECURITY & COMPLIANCE");
  subTitle(s, "等保对制造企业是准入门槛不是加分项 —— 且 AI Native 有传统方案没有的新风险面", C.redAccent);

  const layers = [
    ["物理与网络", C.steel,
      "机房物理安防（自建 or 托管的选型见 P17）· OT / IT 隔离（工业防火墙或网闸）\n区域划分与最小开放 · 入侵检测 · 外部接入边界防护与审计"],
    ["数据安全", C.rd,
      "数据分类分级（配方与客户资料为最高级）· 传输与静态加密\n脱敏与最小授权 · 备份与恢复演练"],
    ["应用与身份", C.mfg,
      "统一身份认证 · 基于角色与企业的权限模型\n全量操作留痕（谁在什么依据下做了什么决定）"],
    ["AI 特有安全", C.ai,
      "模型自部署（P14）：推理与向量数据不出自有实例，不经模型服务商\n上下文准入控制 · 提示注入防护 · 输出可审计与出处留存 · 跨企业上下文严格隔离"],
  ];
  layers.forEach((l, i) => {
    const y = 1.7 + i * 1.05;
    const isAI = i === 3;
    s.addShape(p.ShapeType.roundRect, {
      x: 0.6, y, w: 12.1, h: 0.95,
      fill: { color: isAI ? C.cream : C.white },
      line: { color: l[1], width: isAI ? 2 : 1.2 }, rectRadius: 0.05,
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 2.3, h: 0.95, fill: { color: l[1] } });
    s.addText(l[0], {
      x: 0.6, y: y + 0.32, w: 2.3, h: 0.32,
      fontSize: 12, bold: true, color: C.white, fontFace: "Cambria",
      align: "center", margin: 0,
    });
    s.addText(l[2], {
      x: 3.1, y: y + 0.14, w: 9.4, h: 0.68,
      fontSize: 9, color: C.gray, fontFace: "Calibri",
      lineSpacing: 14, margin: 0, valign: "middle",
    });
    if (isAI) {
      s.addText("AI Native 新增风险面", {
        x: 3.1, y: y - 0.005, w: 4, h: 0.2,
        fontSize: 7, bold: true, color: C.ai, fontFace: "Calibri", margin: 0,
      });
    }
  });

  // 合规要求
  s.addText("合规要求  ·  需在建设前确定", {
    x: 0.6, y: 6.0, w: 6, h: 0.3,
    fontSize: 11.5, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  const comps = [
    ["等保定级与备案", "涉工业控制系统通常需二级或三级 · 定级结果决定安全投入规模"],
    ["数据跨境评估", "若面向海外客户或使用境外模型服务，需评估数据出境合规路径"],
    ["入驻企业数据边界", "多企业共用平台，隔离方案须写入入驻协议并在系统中强制"],
  ];
  comps.forEach((c, i) => {
    const x = 0.6 + i * 4.13;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 6.36, w: 3.9, h: 0.72,
      fill: { color: "FDF3F2" }, line: { color: C.redAccent, width: 1 }, rectRadius: 0.04,
    });
    s.addText(c[0], {
      x: x + 0.18, y: 6.44, w: 3.5, h: 0.24,
      fontSize: 9.5, bold: true, color: C.redAccent, fontFace: "Cambria", margin: 0,
    });
    s.addText(c[1], {
      x: x + 0.18, y: 6.68, w: 3.5, h: 0.36,
      fontSize: 7.6, color: C.gray, fontFace: "Calibri",
      lineSpacing: 10, margin: 0, valign: "top",
    });
  });
  footer(s);
}

// ═══════════════════════════════ P19 · 交付边界与分工
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "交付边界与分工  ·  谁做什么", "PART 4 · SCOPE & RESPONSIBILITY");
  subTitle(s, "在「数据接入层」切一刀 —— 线以下由系统集成商实施，线以上为本方案范围", C.redAccent);

  const bands = [
    ["交互与 Agent 层", C.ai, "对话入口 · 听懂要做什么 · 拆解与编排 · 有后果的动作交人确认 · 给出结果",
      "本方案范围", true],
    ["能力与知识层", C.rd, "工具与技能 · 行业与企业知识 · 记忆 · 上下文工程",
      "本方案范围", true],
    ["数据语义层", C.secondary, "全域统一定义 · 多形态存放 · 统一取数与权限",
      "本方案范围", true],
    ["数据接入层", C.accent, "点位表 · 数据标准 · 接口协议 · 时间同步 · 验收标准",
      "★ 本方定规范，集成商实现", "half"],
    ["边缘与设备层", C.steel, "PLC · 传感器与计量 · 协议网关 · 视觉质检成套 · 布线安装",
      "设备供应商 + 系统集成商", false],
  ];
  bands.forEach((b, i) => {
    const y = 1.72 + i * 0.88;
    const ours = b[4] === true, half = b[4] === "half";
    s.addShape(p.ShapeType.roundRect, {
      x: 0.6, y, w: 12.1, h: 0.8,
      fill: { color: ours ? C.white : (half ? "FDF8F0" : C.bg2) },
      line: { color: b[1], width: ours ? 2 : (half ? 2 : 1) }, rectRadius: 0.05,
    });
    s.addShape(p.ShapeType.rect, { x: 0.6, y, w: 2.15, h: 0.8, fill: { color: b[1] } });
    s.addText(b[0], {
      x: 0.6, y: y + 0.26, w: 2.15, h: 0.3,
      fontSize: 11, bold: true, color: C.white, fontFace: "Cambria",
      align: "center", margin: 0,
    });
    s.addText(b[2], {
      x: 2.95, y: y + 0.27, w: 6.25, h: 0.3,
      fontSize: 8.6, color: ours ? C.gray : C.grayLt, fontFace: "Calibri", margin: 0,
    });
    s.addShape(p.ShapeType.rect, {
      x: 9.35, y: y + 0.17, w: 3.15, h: 0.46,
      fill: { color: ours ? C.primary : (half ? C.accent : C.grayLt) },
    });
    s.addText(b[3], {
      x: 9.4, y: y + 0.25, w: 3.05, h: 0.3,
      fontSize: ours ? 9.5 : 8.2, bold: true, color: C.white,
      fontFace: "Calibri", align: "center", margin: 0,
    });
  });
  s.addShape(p.ShapeType.rect, { x: 0.6, y: 5.2, w: 12.1, h: 0.03, fill: { color: C.redAccent } });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 6.2, w: 5.95, h: 0.78,
    fill: { color: C.cream }, line: { color: C.bg2, width: 1 }, rectRadius: 0.05,
  });
  s.addText("为什么设备侧不由本方实施", {
    x: 0.85, y: 6.3, w: 5.4, h: 0.26,
    fontSize: 10, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "现场施工需驻场与安全资质、须配合停线；设备协议长尾需逐个适配；产线故障责任难切割。\n" +
    "视觉质检亦交专业厂商成套交付 —— 本方在现场零部署。",
    {
      x: 0.85, y: 6.55, w: 5.4, h: 0.4,
      fontSize: 8, color: C.gray, fontFace: "Calibri",
      lineSpacing: 11, margin: 0, valign: "top",
    }
  );

  s.addShape(p.ShapeType.roundRect, {
    x: 6.75, y: 6.2, w: 5.95, h: 0.78,
    fill: { color: "FDF3F2" }, line: { color: C.redAccent, width: 1.5 }, rectRadius: 0.05,
  });
  s.addText("但接口规范必须由本方定义", {
    x: 7.0, y: 6.3, w: 5.4, h: 0.26,
    fontSize: 10, bold: true, color: C.redAccent, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "规范一并交出去的后果是「数据接进来了但不能用」—— 点位缺失、频率不足、时间戳不同步，\n" +
    "返工代价仍由上层承担。边界是「不实施」不是「不介入」。",
    {
      x: 7.0, y: 6.55, w: 5.4, h: 0.4,
      fontSize: 8, color: C.gray, fontFace: "Calibri",
      lineSpacing: 11, margin: 0, valign: "top",
    }
  );
  footer(s);
}

// ═══════════════════════════════ P20 · 数据接入规范
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "数据接入规范  ·  三方责任划分", "PART 4 · DATA INTERFACE SPEC");
  subTitle(s, "本方案定义「接进来的数据要长什么样」· 实施由集成商承担 · 前提条件由建设方保障", C.redAccent);

  const rows = [
    ["点位表", "本方定模板\n集成商填报", "设备 / 参数名 / 类型 / 量程 / 精度 / 频率 / 单位", "缺一项该点位即不可用"],
    ["语义与编码标准", "本方定义", "设备编码、参数命名、批次编码全域一致", "语义不统一 Agent 无法跨域推理"],
    ["时间同步", "本方提要求\n集成商实施", "统一时钟源，采集时间戳误差在可接受范围", "不同步则参数与质检无法对齐"],
    ["数据格式与协议", "本方定接口\n集成商对接", "上传格式、消息结构、断点续传与补传", "决定断网后能否恢复"],
    ["数据质量验收", "本方定标准\n双方联调", "完整性、及时性、准确性可量化验收", "不过则不进入上层建设"],
    ["设备接口开放", "建设方在采购合同中约定", "PLC 读取权限、协议文档、点位地址表", "★ 合同未约定则难以补救"],
    ["视觉数据要求", "本方提要求\n视觉厂商实施", "缺陷数据须含批次号与时间戳、缺陷类型编码统一", "否则无法做缺陷—参数归因"],
    ["既有系统与文档", "建设方推动配合", "ERP 接口；实验记录、认证证书、客户要求等资料归集", "知识层的原料，可先行"],
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
      x: 0.6, y, w: 0.05, h: rh, fill: { color: isOurs ? C.accent : C.grayLt },
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
    "★ 建议把本页的点位表模板、语义标准与验收标准作为附件写入设备、集成商与视觉厂商的采购合同 —— 成本最低的风险控制手段",
    {
      x: 0.9, y: 6.73, w: 11.5, h: 0.3,
      fontSize: 9, color: C.redAccent, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P21 · 人机协同与治理边界
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "人机协同与治理边界", "PART 4 · GOVERNANCE");
  subTitle(s, "明确不做什么，比列举能做什么更重要 —— 这是 AI Native 方案可信度的来源");

  const nots = [
    ["不做无人干预的自动调参", "生物基材料工艺窗口窄，误调的代价不对称。模型给建议区间与依据，工艺人员确认后执行，执行结果再存回系统形成闭环"],
    ["不进产线实时控制回路", "毫秒级控制与安全联锁由 PLC / DCS 承担。Agent 在决策层给建议，不参与实时控制"],
    ["不做质检的最终裁决", "视觉判定用于拦截与分类；争议批次与边界样本由人工复核，判定标准解释权在质量部门"],
    ["不代替人对外承诺", "报价、交期、质量条款、合规声明等对外承诺由人签字，Agent 只出草稿并标注依据"],
    ["不做黑箱回答", "涉及工艺与合规的回答须附出处（历史批次 / 标准条款 / 检测报告），使用者能核对再决定是否采纳"],
    ["不承诺无数据支撑的效果", "准确率与提升幅度需在现场数据上验证后才给数字；本方案阶段只给能力、前提与难度"],
  ];
  nots.forEach((n, i) => {
    const x = 0.6 + (i % 2) * 6.15;
    const y = 1.75 + Math.floor(i / 2) * 1.62;
    s.addShape(p.ShapeType.roundRect, {
      x, y, w: 5.95, h: 1.48,
      fill: { color: "FDF3F2" }, line: { color: C.redAccent, width: 1 }, rectRadius: 0.05,
    });
    s.addShape(p.ShapeType.rect, { x, y, w: 0.05, h: 1.48, fill: { color: C.redAccent } });
    s.addText(n[0], {
      x: x + 0.24, y: y + 0.16, w: 5.5, h: 0.3,
      fontSize: 11.5, bold: true, color: C.redAccent, fontFace: "Cambria", margin: 0,
    });
    s.addText(n[1], {
      x: x + 0.24, y: y + 0.52, w: 5.5, h: 0.85,
      fontSize: 8.8, color: C.gray, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "top",
    });
  });

  s.addText(
    "配套机制 · 操作留痕与可追溯（谁在什么依据下做了什么决定）· 权限分级（跨企业数据默认隔离）· 建议采纳率跟踪（用于评估与迭代）",
    {
      x: 0.6, y: 6.72, w: 12.1, h: 0.3,
      fontSize: 8.8, color: C.accentDk, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P22 · 建设内容清单
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "建设内容清单", "PART 5 · SCOPE OF WORK");
  subTitle(s, "按 AI Native 架构分层列出 · 产线设备与厂房不在此表 · 投资金额待选型询价后概算");

  const rows = [
    ["01", "边缘采集与联网", "协议网关、边缘节点、采集代理、工位终端", "集成商", "P2"],
    ["02", "传感与计量装置", "补充传感器、分项计量表具及远传模块", "集成商", "P2"],
    ["03", "视觉质检成套", "相机、光源、推理硬件与基础判定", "视觉厂商", "P2"],
    ["04", "工业网络与安全", "工业以太网、网络隔离、OT/IT 边界防护", "集成商", "P2"],
    ["05", "算力与存储", "PHASE 1 租赁 GPU 与通用算力；后续如转自购则为服务器", "选型本方", "P1"],
    ["05c", "模型部署与调优", "开源推理/嵌入/视觉模型选型、部署、评测与迭代机制", "本方", "P1-P3"],
    ["05b", "机房或托管（后续）", "转本地化时：自建土建配套 或 托管机柜与专线", "建设方", "P3"],
    ["06", "安全设备与等保建设", "边界防护、身份认证、审计、等保测评整改", "选型本方", "P1"],
    ["07", "数据语义层", "全域统一定义、多形态存放、统一取数与权限", "本方", "P1-P2"],
    ["08", "知识与记忆层", "行业知识库、企业知识库、案例与记忆、检索增强", "本方", "P1"],
    ["09", "上下文工程", "三层上下文构建与持续维护机制", "本方", "P1-P3"],
    ["10", "Agent 编排平台", "听懂要做什么、拆解编排、调用工具、交人确认、留痕审计", "本方", "P1-P3"],
    ["11", "工具与技能库", "各业务工具封装、技能定义与沉淀机制", "本方", "P1-P2"],
    ["12", "研发 Agent", "记录结构化、案例检索、需求受理、方向建议", "本方", "P1"],
    ["13", "生产 Agent", "批次串联、异常归因、参数建议、排产评估", "本方", "P2-P3"],
    ["14", "合规与客户 Agent", "清单解析组包、标准比对、碳足迹、客诉归因", "本方", "P1-P2"],
    ["15", "园区 Agent", "受控技术咨询、产能协同、认证支持、企业接入", "本方", "P3"],
    ["16", "MES 与质量系统", "工单、排产、追溯记录、检验规则与判定", "本方", "P1-P2"],
    ["17", "集成与实施服务", "接口开发、数据迁移、先把已有资料灌进知识层、培训试运行", "本方", "P1-P3"],
  ];

  const y0 = 1.66, rh = 0.305;
  s.addShape(p.ShapeType.rect, { x: 0.6, y: y0, w: 12.1, h: 0.3, fill: { color: C.primary } });
  ["", "建设内容", "主要构成", "实施方", "阶段"].forEach((h, i) => {
    const xs = [0.75, 1.2, 3.9, 9.1, 10.6];
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
      x: 0.75, y: y + 0.06, w: 0.4, h: 0.23,
      fontSize: 8.2, bold: true, color: C.accent, fontFace: "Cambria", margin: 0,
    });
    s.addText(r[1], {
      x: 1.2, y: y + 0.055, w: 2.6, h: 0.25,
      fontSize: 8.8, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(r[2], {
      x: 3.9, y: y + 0.06, w: 5.1, h: 0.23,
      fontSize: 7.6, color: C.gray, fontFace: "Calibri", margin: 0,
    });
    const isOurs = r[3] === "本方";
    s.addShape(p.ShapeType.rect, {
      x: 9.1, y: y + 0.06, w: 1.05, h: 0.22,
      fill: { color: isOurs ? C.primary : C.grayLt },
    });
    s.addText(r[3], {
      x: 9.1, y: y + 0.075, w: 1.05, h: 0.2,
      fontSize: 7, bold: true, color: C.white, fontFace: "Calibri",
      align: "center", margin: 0,
    });
    s.addText(r[4], {
      x: 10.6, y: y + 0.06, w: 2.0, h: 0.23,
      fontSize: 7.4, bold: true, color: C.accentDk, fontFace: "Calibri", margin: 0,
    });
  });

  s.addText(
    "阶段对应实施路径（P24）· 08-11 是 AI Native 架构特有的建设内容，传统信息化方案中没有对应项 · 投资金额待选型询价后概算",
    {
      x: 0.6, y: 6.92, w: 12.1, h: 0.28,
      fontSize: 8, color: C.grayLt, fontFace: "Calibri", margin: 0,
    }
  );
  footer(s);
}

// ═══════════════════════════════ P23 · 技术指标框架
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "建设目标与验收指标", "PART 5 · KPI FRAMEWORK");
  subTitle(s, "指标既是建设目标也是验收依据 —— 所有基线必须实测，本页不填估计值", C.redAccent);

  s.addText("效益类  ·  制造业通用五项", {
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

  s.addText("能力类  ·  AI Native 特有", {
    x: 6.75, y: 1.7, w: 5.95, h: 0.3,
    fontSize: 11.5, bold: true, color: C.ai, fontFace: "Cambria", margin: 0,
  });
  const sup = [
    ["知识资产化率", "已结构化沉淀 / 应沉淀的工艺与案例"],
    ["Agent 任务覆盖率", "由 Agent 承担 / 可自动化的业务任务"],
    ["建议采纳率", "人工采纳 / Agent 给出的建议总数"],
    ["数据可追溯率", "可溯源到出处的回答 / 全部回答"],
    ["下游自助解决率", "入驻企业自助完成 / 全部服务请求"],
  ];
  s.addShape(p.ShapeType.rect, { x: 6.75, y: 2.04, w: 5.95, h: 0.3, fill: { color: C.ai } });
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
      x: 6.9, y: y + 0.07, w: 3.1, h: 0.26,
      fontSize: 10, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
    });
    s.addText(c[1], {
      x: 6.9, y: y + 0.32, w: 3.1, h: 0.24,
      fontSize: 7, color: C.grayLt, fontFace: "Calibri", margin: 0,
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
    "指标一旦写进合同就是验收依据 —— 将来要按它交差。现状基线从未实测，任何估计值都会变成无法兑现的承诺。\n" +
    "建立基线需采集：近 12 个月产量与工时台账 · 批次合格率与废品记录 · 分项能耗（现为整厂电表，需先装分表）· 新品开发周期记录 · 实验记录与认证档案清点。" +
    "  右侧能力类指标为 AI Native 方案特有，目标值需与建设范围一并商定。",
    {
      x: 0.9, y: 6.04, w: 11.5, h: 0.8,
      fontSize: 8.8, color: C.gray, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "top",
    }
  );
  footer(s);
}

// ═══════════════════════════════ P24 · 实施路径
{
  nextP();
  const s = p.addSlide();
  s.background = { color: C.bg };
  pageTitle(s, "实施路径  ·  能力逐步开放，不是模块逐个上线", "PART 5 · ROADMAP");
  subTitle(s, "AI Native 的推进方式与传统项目不同：底座一次建好，能力持续增长");

  const phases = [
    ["PHASE 1", "打底座 · 灌知识", [
      ["研", "归集既有资料，先把知识灌进去", C.rd],
      ["销", "认证档案入库，合规 Agent 先用起来", C.sales],
      ["产", "点位表与语义标准定义（集成商并行施工）", C.mfg],
      ["跨", "Agent 编排平台与上下文层搭建", C.ai],
    ], "研 / 销 侧不依赖产线，可与产线建设并行；此阶段先让 Agent 在文档类任务上跑起来"],
    ["PHASE 2", "接数据 · 出结果", [
      ["产", "设备数据接入验收，批次链贯通", C.mfg],
      ["产", "异常发现与缺陷归因上线", C.mfg],
      ["研", "实验记录结构化，案例检索可用", C.rd],
      ["销", "碳足迹核算，交付文档自动生成", C.sales],
    ], "依赖 PHASE 1 底座与设备侧交付；此阶段业务收益开始显现"],
    ["PHASE 3", "上模型 · 开放下游", [
      ["产", "参数建议、排产影响评估", C.mfg],
      ["研", "候选方向建议、技术受控输出", C.rd],
      ["园", "园区 Agent 面向入驻企业开放", C.ai],
    ], "依赖数据积累与首批企业入驻；赋能能力对外开放"],
  ];
  phases.forEach((ph, i) => {
    const x = 0.6 + i * 4.13;
    const isFirst = i === 0;
    s.addShape(p.ShapeType.roundRect, {
      x, y: 1.72, w: 3.9, h: 4.05,
      fill: { color: isFirst ? C.primary : C.white },
      line: { color: C.bg2, width: 1 }, rectRadius: 0.06,
    });
    s.addText(ph[0], {
      x: x + 0.25, y: 1.9, w: 3.4, h: 0.28,
      fontSize: 10, bold: true, color: isFirst ? C.accent : C.accentDk,
      fontFace: "Calibri", charSpacing: 2, margin: 0,
    });
    s.addText(ph[1], {
      x: x + 0.25, y: 2.22, w: 3.4, h: 0.35,
      fontSize: 15, bold: true, color: isFirst ? C.white : C.primary,
      fontFace: "Cambria", margin: 0,
    });
    ph[2].forEach((item, j) => {
      const iy = 2.75 + j * 0.62;
      s.addShape(p.ShapeType.rect, { x: x + 0.25, y: iy, w: 0.26, h: 0.22, fill: { color: item[2] } });
      s.addText(item[0], {
        x: x + 0.25, y: iy + 0.005, w: 0.26, h: 0.21,
        fontSize: 7.5, bold: true, color: C.white, fontFace: "Calibri",
        align: "center", margin: 0,
      });
      s.addText(item[1], {
        x: x + 0.58, y: iy - 0.02, w: 3.1, h: 0.56,
        fontSize: 8.2, color: isFirst ? C.cream : C.gray, fontFace: "Calibri",
        lineSpacing: 11, margin: 0, valign: "top",
      });
    });
    s.addShape(p.ShapeType.rect, {
      x: x + 0.25, y: 5.2, w: 3.4, h: 0.01,
      fill: { color: isFirst ? C.primaryDk : C.bg2 },
    });
    s.addText(ph[3], {
      x: x + 0.25, y: 5.3, w: 3.4, h: 0.42,
      fontSize: 7.8, color: isFirst ? C.secondary : C.accentDk,
      fontFace: "Calibri", lineSpacing: 11, margin: 0, valign: "top",
    });
    if (i < 2) {
      s.addText("▶", {
        x: x + 3.94, y: 3.6, w: 0.28, h: 0.3,
        fontSize: 13, color: C.accent, fontFace: "Calibri", margin: 0,
      });
    }
  });

  s.addShape(p.ShapeType.roundRect, {
    x: 0.6, y: 5.92, w: 12.1, h: 0.98,
    fill: { color: C.cream }, line: { color: C.accent, width: 1 }, rectRadius: 0.05,
  });
  s.addText("两条硬约束", {
    x: 0.9, y: 6.03, w: 3, h: 0.28,
    fontSize: 11, bold: true, color: C.primary, fontFace: "Cambria", margin: 0,
  });
  s.addText(
    "① 传感器与计量装置必须在产线建设期同步安装 —— 事后加装需停线改造（见 P20 / P22-02）\n" +
    "② 设备与视觉厂商的采购合同须写入数据开放与格式条款 —— 这一步错过，后面所有阶段都要付代价",
    {
      x: 0.9, y: 6.32, w: 11.5, h: 0.55,
      fontSize: 8.5, color: C.gray, fontFace: "Calibri",
      lineSpacing: 13, margin: 0, valign: "top",
    }
  );
  s.addText("注 · 各阶段时长需结合产线建设周期确定，本页不列具体月份", {
    x: 0.6, y: 6.95, w: 12.1, h: 0.28,
    fontSize: 8, color: C.grayLt, fontFace: "Calibri", margin: 0,
  });
  footer(s);
}

p.writeFile({ fileName: "daosheng-v30-smart-mfg.pptx" })
  .then(() => console.log("✓ daosheng-v30-smart-mfg.pptx  ·  24 页"));
