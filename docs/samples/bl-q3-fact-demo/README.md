# BL-Q3-FACT P0 MVP — 5/14 Demo 资产

> **用途**: 给鸿波 5/14 演示 BL-Q3-FACT (事实补丁系统) 用. 包含 mock 政策文件 + 3 个对应 skill + 7 步演示脚本.

## 文件清单

| 文件 | 角色 |
|---|---|
| `policy-purchase-revision.md` | mock "公司采购流程修订 v3.2" — 上传到 catfish-web `/admin/facts` |
| `skill-send-purchase-request.md` | mock skill #1 (会被命中, confidence ~95%) |
| `skill-quarterly-budget-review.md` | mock skill #2 (会被命中, confidence ~75%) |
| `skill-vendor-onboarding.md` | mock skill #3 (不会被命中 ⇒ 演 FACT 不误报) |
| `DEMO-SCRIPT.md` | 5/14 现场演示 7 步剧本, 你照念 |

## 演示前置准备 (建议 5/13 晚跑一遍)

```bash
# 1. 起 catfish-identity + skills-hub + gateway + web (一键)
cd ~/person_task/catfish
bash scripts/start-all-services.sh   # 假设有, 没有就分别 cd 起

# 2. 把 3 个 sample skill publish 到 SkillsHub (admin 操作)
#    走 catfish-web /admin 后台手动 publish, 或者 curl:
curl -X POST http://127.0.0.1:8999/v1/hub/skills/department \
  -H "Authorization: Bearer $YOUR_ID_TOKEN" \
  -F "files=@docs/samples/bl-q3-fact-demo/skill-send-purchase-request.md;filename=SKILL.md"
# 重复 3 次, 每次一个 skill 文件 (注意 filename=SKILL.md 这个 multipart 字段)

# 3. 打开 catfish-web
open http://localhost:5173/admin/facts

# 4. 上传 policy-purchase-revision.md, 自动进分析, 等 30-90 秒看结果
```

## 演示预期

上传 `policy-purchase-revision.md` 后, 鲶鱼自动:

1. **提取 5 个事实点**:
   - "采购总监审批阈值 5 万 → 3 万"
   - "急采购流程需 CFO 二次签字"
   - "供应商资质审核加 ISO 27001 项"
   - "季度采购计划提前申报 2 周 → 3 周"
   - "进口设备采购加海关清单"

2. **找出 2 个受影响 skill** (3 个 sample 里有 2 个会命中):
   - `skill-send-purchase-request` confidence 95% (直接撞阈值)
   - `skill-quarterly-budget-review` confidence 75% (撞提前申报天数)
   - `skill-vendor-onboarding` 不命中 — **这是 FACT 的"非误报"卖点**

3. **生成 2 个 patches**, 每个含:
   - rationale (引用 raw_quote)
   - changes (老/新片段对照)
   - 完整改后 SKILL.md

4. 你点"采纳 → 发布到 SkillsHub" → 弹"已发布新版本: 1.0.fact-xxxxxxxx" → SkillsHub 真有新版本.

## 故障排查

- **上传后一直转圈不进分析阶段**: gateway log 看 `facts.upload` / `facts.analyze` 是否报错. 常见 LLM 配额超 / API key 失效.
- **分析完 0 个 impact**: SkillsHub 没 skill 可扫. 先 publish sample skill.
- **LLM 解析 0 个事实点**: 检查模型选的对不对 (`fact_analyzer` use_case), 可能掉 fallback 到一个差模型.
- **采纳后报 "skills-hub publish 失败 already exists"**: version 没正确 bump. 看 gateway log 的 `_bump_version_in_skill_md` 输入输出.

## 演示后回收

```bash
# 删 demo 生成的 fact (软删, 文件还在方便复盘)
# catfish-web /admin/facts → 点 demo fact → 撤销

# 删 demo 发布的 skill 新版本 (如果不想留)
curl -X DELETE "http://127.0.0.1:8997/skills/department/skill-send-purchase-request/1.0.fact-xxxxxxxx" \
  -H "Authorization: Bearer $TOKEN"
```
