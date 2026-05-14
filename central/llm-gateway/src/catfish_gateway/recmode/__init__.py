"""BL-LEARN-RECMODE (5/14): 录屏+语音教学引擎 — events 捕获 / 综合 / 落 skill.

子模块:
- cdp_listener: 连 Catfish Chrome ws://localhost:9222 监听 events 落 JSONL + 截图
- aggregator: 综合 events + 语音转写 + keyframes → 喂 catfish-private-main → SKILL.md
- session: 一次录屏 session 的状态机 (idle / recording / analyzing / preview / done)

设计文档: docs/LEARN-RECMODE-DESIGN.md
关联 task: #59 BL-LEARN-RECMODE
"""
