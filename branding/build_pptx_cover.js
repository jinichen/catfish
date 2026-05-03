// Demo PPT 封面 — 鲶鱼 brand 深青底 + 极简 mark + 大字标题
// 五一 sprint 5/3 BL-D11. 用法: node build_pptx_cover.js
const pptxgen = require("pptxgenjs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const pres = new pptxgen();
pres.layout = "LAYOUT_16x9"; // 10" × 5.625"
pres.author = "鲶鱼 Catfish";
pres.company = "Catfish";
pres.title = "鲶鱼 Companion · 企业级 AI 助理";

// ── 配色 (跟 BRAND.md 一致) ──
const C = {
  cyan: "0E5F66",      // 墨青 (主色)
  cyanDim: "0A464C",   // 深青 (背景渐变下端)
  cyanBright: "1A8A95",// 亮青 (装饰光点)
  orange: "F47B3D",    // 暖橙 (锚点 / 强调)
  cream: "FAF1E4",     // 暖米 (主文字)
  creamSoft: "E8DCC4", // 暖米淡 (副文字)
};

// ── 封面 slide ──
const cover = pres.addSlide();
cover.background = { color: C.cyanDim };

// 上方深青带 (顶 0~3.5") 作为视觉重心区
cover.addShape(pres.shapes.RECTANGLE, {
  x: 0, y: 0, w: 10, h: 3.5,
  fill: { color: C.cyan },
  line: { color: C.cyan, width: 0 },
});

// 装饰: 右上角微光 (亮青小圆, 半透明, 暗示水底气泡)
cover.addShape(pres.shapes.OVAL, {
  x: 8.5, y: 0.3, w: 0.18, h: 0.18,
  fill: { color: C.cyanBright, transparency: 30 },
  line: { color: C.cyanBright, width: 0 },
});
cover.addShape(pres.shapes.OVAL, {
  x: 9.1, y: 0.55, w: 0.12, h: 0.12,
  fill: { color: C.cyanBright, transparency: 50 },
  line: { color: C.cyanBright, width: 0 },
});
cover.addShape(pres.shapes.OVAL, {
  x: 8.85, y: 0.9, w: 0.08, h: 0.08,
  fill: { color: C.cyanBright, transparency: 60 },
  line: { color: C.cyanBright, width: 0 },
});

// ── 极简 mark (左侧, 居中竖向, 1.6"×1.6") ──
cover.addImage({
  path: path.join(ROOT, "branding", "logo-mark-1200.png"),
  x: 0.8, y: 1.0, w: 1.6, h: 1.6,
});

// ── 主标题 "鲶鱼" (大字, 暖米色) ──
cover.addText("鲶鱼", {
  x: 2.8, y: 0.95, w: 6.5, h: 1.2,
  fontSize: 72, fontFace: "PingFang SC",
  bold: true, color: C.cream,
  align: "left", valign: "middle",
  margin: 0,
});

// 英文副标 "Catfish" (亮青, 跟 鲶鱼 同行右侧)
cover.addText("Catfish", {
  x: 2.8, y: 2.0, w: 6.5, h: 0.5,
  fontSize: 24, fontFace: "Helvetica Neue",
  italic: true, color: C.cyanBright,
  align: "left", valign: "top",
  charSpacing: 4,
  margin: 0,
});

// 暖橙锚点 + 副标语 (中部)
cover.addShape(pres.shapes.OVAL, {
  x: 2.8, y: 2.78, w: 0.14, h: 0.14,
  fill: { color: C.orange },
  line: { color: C.orange, width: 0 },
});
cover.addText("企业级 AI 助理 · Companion for SOE", {
  x: 3.05, y: 2.62, w: 6.5, h: 0.5,
  fontSize: 18, fontFace: "PingFang SC",
  color: C.cream,
  align: "left", valign: "middle",
  margin: 0,
});

// ── 下半部 (3.5" 以下, 深青底) ──

// 左下: slogan (3 行, 小字, 暖米淡色)
cover.addText([
  { text: "贴近员工 · ", options: { color: C.creamSoft } },
  { text: "本机算力", options: { bold: true, color: C.cream } },
  { text: "，", options: { color: C.creamSoft, breakLine: true } },
  { text: "听懂业务 · ", options: { color: C.creamSoft } },
  { text: "公文 / 报表 / 审批", options: { bold: true, color: C.cream } },
  { text: "，", options: { color: C.creamSoft, breakLine: true } },
  { text: "全程留痕 · ", options: { color: C.creamSoft } },
  { text: "审计合规", options: { bold: true, color: C.cream } },
  { text: "。", options: { color: C.creamSoft } },
], {
  x: 0.8, y: 3.85, w: 8.5, h: 1.2,
  fontSize: 16, fontFace: "PingFang SC",
  align: "left", valign: "top",
  margin: 0,
  paraSpaceAfter: 4,
});

// 底部右下: 版本 + 日期 (极小字, 暖米淡色)
cover.addText("v0.1.0 · 2026.05", {
  x: 7.5, y: 5.15, w: 2.0, h: 0.3,
  fontSize: 11, fontFace: "Helvetica Neue",
  color: C.creamSoft,
  align: "right", valign: "middle",
  margin: 0,
});

// 底部左下: 公司角标
cover.addText("Catfish · Enterprise AI", {
  x: 0.5, y: 5.15, w: 4.0, h: 0.3,
  fontSize: 11, fontFace: "Helvetica Neue",
  color: C.creamSoft,
  align: "left", valign: "middle",
  charSpacing: 2,
  margin: 0,
});

// 写出
const out = path.join(ROOT, "branding", "demo-cover.pptx");
pres.writeFile({ fileName: out }).then(() => {
  console.log(`✓ 封面已写出: ${out}`);
});
