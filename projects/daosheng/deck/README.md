# deck/ — 稻生万物 PPT

## ⚠️ 先看这条：pptx 现在是手工维护的，**没有能重生成它的脚本**

这份 README 以前写着：

> `build.js` 是 source of truth。**不要直接改 pptx** — 下次 build 会覆盖。

**这条已经反过来了，照做会毁掉当前交付物。** 现在是 pptx 是真源，脚本全是历史。

`build-nlzx-v2.js` 里停留在**区间值时代**的数字（静态投资 4,657–8,422、中值
7,200 万、GPU 260–300 万/台），而 pptx 早就改成点值、又经过 8/13 的
6 台 → 12 台改版。重跑那个脚本连旧版都还原不出来，更不会产出当前版本。

25 个历史 builder 已移到 `archive/builders/`（本地留档 · gitignore ·
git 历史里仍可 `git log --all -- projects/daosheng/deck/build-vN.js` 找回）。
真要从头重建一份新 deck，`archive/builders/build-nlzx-v2.js` 是最接近的模板。

## 当前版本

| 文件 | 口径 |
|---|---|
| `daosheng-nlzx-v2.pptx` | 单节点 · 每地 12 台 GPU · 控制价 **11,942 万** |
| `daosheng-nlzx-v2.budget-appendix.pptx` | 同上，预算明细 |
| `daosheng-nlzx-v2.architecture-draft/-final.pptx` | 与主 deck 内容相同（同一份文档的三个名字） |
| `../docs/daosheng-nlzx-v2-3nodes.pptx` | 三节点 · 控制价 **25,946 万**（2.59 亿） |
| `../docs/daosheng-nlzx-v2.budget-appendix-3nodes.pptx` | 同上，预算明细 |

`archive/` 里全是旧口径快照（每地 6 台、写着算力主机品牌、含运营期成本）。
**不要拿 archive 里的文件对外。**

## 怎么改

直接用 python-pptx 改 pptx，改完两件事必做：

1. **两份 deck 一起改。** 主 deck 和预算附件里有同样的句子，而且**主 deck 用
   全角逗号、附件用半角** —— 同一句话差一个字符，批量替换会在其中一份静默漏掉。
2. **算术对账。** 改了任何一个数就把静态投资 / 预备费 / 控制价 / 分阶段四组
   数字重新加一遍。历史上出过 appendix p8 填了三节点的数（那页是单节点表）。

其它已经踩过的坑：

- pptx 备注不解析 markdown，写 `**加粗**` 会原样显示成星号
- 多段落单元格只替换 `paragraph[0]` 会留下自相矛盾的残句
- 合计行的文字不一定在同一列（有一行的说明在第 4 列，其余在第 5 列）

## 版本演进（历史）

`build.js` v1–v12 是"顾乾禾"线，`build-v13.js`–`build-v34.js` 是临港 / 智能制造线，
`build-nlzx-v1/v2.js` 是能力中心线。每个 builder 对应一个同名 pptx，
一一对应关系见 `archive/builders/` 与 `archive/` 下的文件名。

## 军规（改 PPT 时必守）

见 `../README.md` 的"关键约束"段。核心 6 条：

1. 不主推鲶鱼（用"自研 AI 底座"描述）
2. 老规划口径不引用
3. 产业发展高度（不是卖软件）
4. 双原料平权
5. 不编数据（"待定"或"与新规划详本同步"）
6. Agent 只出草稿（合规场景人工签字）

加一条 8/13 的：**不写算力主机品牌型号**，显存规格一并模糊，
只留"按 2.4T 总参数 / 95B 激活、FP8 精度测算"这类口径。
