# 鲶鱼字体目录

> **核心约束**: 鲶鱼**绝对不分发任何商业字体的二进制文件**, 法律风险归零.

---

## 为什么有这个目录

鲶鱼里多个 skill 会生成 .docx / .xlsx / .pptx, 央国企汇报材料对字体有硬要求 (方正小标宋简体 / 仿宋_GB2312 等). 但**这些是商业字体**, 不能直接打包到鲶鱼分发给客户.

折中方案: 三层目录, 各司其职.

---

## 三层结构

```
fonts/
├── opensource/      # 开源免费字体, 鲶鱼自带 (commit 进 git)
├── customer/        # 客户公司自己的授权字体, 客户 IT 部署时填 (空目录 + .gitignore)
└── (系统已装)       # macOS / Win 自带的 (黑体 / 楷体), 不需要管
```

| 层级 | 谁负责 | 走 git 吗 | 法律责任 |
|------|--------|----------|----------|
| `opensource/` | 我们 | ✅ commit | 开源协议合规 (SIL OFL / Apache 2.0 等), 我们承担 |
| `customer/` | 客户 IT | ❌ .gitignore | **客户用自己公司的字库授权**, 责任在客户 |
| 系统已装 | 客户 / Apple / MS | N/A | 无 |

---

## 优先级 (skill 找字体的顺序)

1. `customer/` 最优 — 客户公司合规字体 (例: 方正小标宋简体, 客户自己买的)
2. `opensource/` 次之 — 我们打包的开源 fallback (例: 思源宋体替代方正小标宋)
3. 系统字体最后 — Word 自动 fallback (没装就显示默认中文字体)

无论哪层都找不到 → .docx 仍写正确**字体名**到 XML, 客户机有装就显示对的字体.

---

## opensource/ 推荐内容

| 字体名 | 文件名 | 大小 | 替代什么 | 来源 |
|--------|--------|------|----------|------|
| 思源宋体 | SourceHanSerifCN-Regular.otf | ~12 MB | 方正小标宋简体 (主标题) | [Adobe Fonts (SIL OFL)](https://github.com/adobe-fonts/source-han-serif/releases) |
| 思源宋体 Bold | SourceHanSerifCN-Bold.otf | ~12 MB | 加粗版本 | 同上 |
| 思源黑体 | SourceHanSansCN-Regular.otf | ~12 MB | 黑体 (一级标题) | [Adobe Fonts (SIL OFL)](https://github.com/adobe-fonts/source-han-sans/releases) |
| 文泉驿仿宋 | wqy-fangsong.ttf | ~2 MB | 仿宋_GB2312 (正文) | [文泉驿 (GPL)](http://wenq.org/) |

**总计 ~38 MB**, 比 .app 主程序小, 可接受.

⚠ **思源** 系列是 Adobe + Google 联合开源, SIL OFL 许可证, 商用 / 分发**完全合法**.

---

## customer/ 客户填什么

客户 IT 部署鲶鱼时, 把公司**已购授权**的字体 .ttf / .ttc / .otf 文件**直接拷贝**到 `fonts/customer/` 即可:

```
fonts/customer/
├── FZXBSJW.TTF              # 方正小标宋简体 (公司方正商业授权下)
├── FangSong_GB2312.ttf      # 仿宋_GB2312
└── (其他公司风格字体)
```

**要点**:
- 这些字体的**授权由客户公司自己拥有**, 鲶鱼只是读取使用, 不分发
- 客户卸载鲶鱼 → 字体不会留下 (因为我们装到 catfish 自身的字体注册表, 不动系统 Fonts 目录)
- `customer/` 目录 commit 一个空 placeholder, **绝不 commit 任何 .ttf 二进制**

---

## skill 怎么用 (代码)

```python
from catfish.fonts.loader import find_font

# 给 LLM 用 — 检查某字体在不在
path = find_font("方正小标宋简体")
if path is None:
    # fallback 到开源等价物
    path = find_font("思源宋体")
```

或者 python-docx 写 .docx 时直接用字体名 (不需要路径), Word 打开时根据字体名查系统:

```python
set_run_font(run, "方正小标宋简体", Pt(16))
```

字体在客户机存不存在不是 .docx 生成的事, 是渲染时的事.

---

## 已知边界

- **macOS 字体安装**: 我们不自动安装到 `~/Library/Fonts/`, 那是用户决定的事 (用户启动鲶鱼时弹窗 "是否注册字体?" 让用户点同意)
- **Linux**: 服务器侧渲染 .docx 走 LibreOffice 的话需要 `~/.fonts/`; 鲶鱼边缘端不在 Linux 渲染, 暂不考虑
- **iOS / Android**: 移动端不在范围
