# A2UI basic catalog v1

`basic_catalog_v1.json` 原样拷自 https://github.com/a2ui-project/a2ui `catalogs/basic/v1/catalog.json`
(commit 3ed9601, 2026-11-13), Apache License 2.0。

`catfish_catalog_v1.json` 是从它派生的鲶鱼目录 (`a2ui_catalog.py` 生成: 留 12 个基础组件 + 邮件卡 / 日程卡 /
待办项 / 文件卡 4 个自定义组件, 去掉 openUrl, 说明里的例子换成我们的目录 id、Image 换成 Text)。
对话卡片 (`a2ui_card.py`) 用鲶鱼目录生成给模型的提示词、校验模型写的卡片; Companion 的基础组件用 `@a2ui/web_core`
里同一份基础目录的实现, 自定义组件自己画 (companion-app/src/tabs/Chat/catfishCardComponents.tsx)。两边组件名单一致
(a2ui_catalog.component_names / companion-app/src/lib/a2uiCard.ts CARD_COMPONENTS, 测试对着查)。
升级 A2UI 时: 换 basic_catalog_v1.json → `python -m catfish_tool_bridge.a2ui_catalog` 重新生成 → Companion 一起升。

# A2UI VegaChart catalog v1 (11/16 数据图表)

`vegachart_catalog_v1.json` 原样拷自同一仓库的官方扩展目录 `catalogs/vegachart/v1/catalog.json`
(commit 3a64be7, 2026-10-22), Apache License 2.0。只有一个组件 `VegaChart` (spec = Vega-Lite 图表描述, height = 高度)。
鲶鱼目录把它并进来 (`a2ui_catalog.py` CUSTOM_COMPONENTS, 属性跟上游一致, tests/test_a2ui_chart.py 对着查),
出卡 / 改卡时 `a2ui_chart.py` 再查一遍 (只认 Vega-Lite、数据写在卡里、不许 url / href / image)。
上游的 TypeScript 渲染包 (`typescript/catalogs/vegachart`, Lit 版) 没发到 npm; Companion 照它的做法用 vega-embed 写了 React 版
(companion-app/src/tabs/Chat/VegaChart.tsx: 动态加载、SVG、不要操作菜单、表达式走 vega-interpreter、loader 全拒)。
升级时: 换这个文件 → 核对 VegaChart 属性 → `python -m catfish_tool_bridge.a2ui_catalog` 重新生成。
