# A2UI basic catalog v1

`basic_catalog_v1.json` 原样拷自 https://github.com/a2ui-project/a2ui `catalogs/basic/v1/catalog.json`
(commit 3ed9601, 2026-11-13), Apache License 2.0。

`catfish_catalog_v1.json` 是从它派生的鲶鱼目录 (`a2ui_catalog.py` 生成: 留 12 个基础组件 + 邮件卡 / 日程卡 /
待办项 / 文件卡 4 个自定义组件, 去掉 openUrl, 说明里的例子换成我们的目录 id、Image 换成 Text)。
对话卡片 (`a2ui_card.py`) 用鲶鱼目录生成给模型的提示词、校验模型写的卡片; Companion 的基础组件用 `@a2ui/web_core`
里同一份基础目录的实现, 自定义组件自己画 (companion-app/src/tabs/Chat/catfishCardComponents.tsx)。两边组件名单一致
(a2ui_catalog.component_names / companion-app/src/lib/a2uiCard.ts CARD_COMPONENTS, 测试对着查)。
升级 A2UI 时: 换 basic_catalog_v1.json → `python -m catfish_tool_bridge.a2ui_catalog` 重新生成 → Companion 一起升。
