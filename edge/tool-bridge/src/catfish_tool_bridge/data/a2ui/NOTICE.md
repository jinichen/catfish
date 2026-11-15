# A2UI basic catalog v1

`basic_catalog_v1.json` 原样拷自 https://github.com/a2ui-project/a2ui `catalogs/basic/v1/catalog.json`
(commit 3ed9601, 2026-11-13), Apache License 2.0。

对话卡片 (`a2ui_card.py`) 用它生成给模型的提示词、校验模型写的卡片; Companion 用 `@a2ui/web_core` 里同一份
基础目录渲染。两边裁剪成同一组组件 (a2ui_card.CARD_COMPONENTS / companion-app/src/lib/a2uiCard.ts CARD_COMPONENTS)。
升级 A2UI 时两边一起换。
