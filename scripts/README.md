# 运营脚本

平台团队的日常运营/管理脚本。不是业务代码，也不是员工工具。

## 已有

| 脚本 | 用途 |
|---|---|
| `check_file_sizes.sh` | 扫描源码，报告 ≥500 行的文件（warning）和 ≥800 行的文件（error） |
| `check_ai_tells.sh` | 扫描代码里的 AI 生成痕迹（em-dash、Unicode 箭头、装饰符、编号 docstring 等），为软著合规做准备 |

## 用法

```bash
# 报告模式（只列出超标文件）
bash catfish/scripts/check_file_sizes.sh

# CI 模式（≥800 行的文件存在则退出码 1）
bash catfish/scripts/check_file_sizes.sh --strict
```

## 相关位置

- 员工一键装（本地搜索 + Hermes MCP）：`catfish/onboarding/install-catfish.{sh,ps1}`
- Hermes skill 安装：`catfish/edge/local-search/hermes-skill/{install,uninstall}.sh`
- PyInstaller 单文件打包：`catfish/edge/local-search/pyinstaller/build.{sh,bat}`

## 计划的（按需加）

- `seed-starter-packs/` — 生成新员工 skill 包
- `cost-report/` — 月度 LLM 成本报表
- `skill-moderation/` — Skills Hub 审核队列工具
- `policy-check/` — 离线验证策略规则
