# 鲶鱼 · AI 协作与软著合规规范

> **适用对象**：所有参与鲶鱼项目代码提交的开发者
>
> **目的**：为后续软件著作权申报做准备。我们使用 AI 辅助（Claude / Cursor /
> Copilot 等）来提高效率，但提交到仓库的代码必须**经过人工审阅与改写**，
> 呈现自然的开发风格。

---

## 1. 核心原则

**AI 是工具，作者是人**。

- 可以让 AI 草拟代码
- 必须由人审阅、改写、提交
- 代码看起来必须像团队里的人日常写出来的东西
- Git 提交历史必须反映真实开发过程（多次小提交，不是一次巨型提交）

**红线**：

1. 不直接提交 AI 生成的原始输出
2. 不保留 AI 的典型语言痕迹（见下）
3. 不伪造作者署名
4. 每次 commit 都要有实际代码审阅者

---

## 2. 必须清理的 AI 痕迹

这些是 Claude / GPT / 其他 LLM 的典型输出特征，**在代码里出现即违规**：

### 2.1 字符层面

| 痕迹 | 应改为 | 原因 |
|---|---|---|
| `—`（em-dash） | `--` 或重写句子 | 程序员代码里几乎不用 em-dash |
| `→` `←` `⇒` | `->` `<-` `=>` | 注释里用 ASCII 箭头 |
| `✓` `✅` `❌` `⚠️` | `[OK]` `[FAIL]` `[WARN]` | 代码里不放装饰 emoji |
| `★` `🎉` `🔒` `🐟` | 纯文字 | 同上 |
| `━━━` `═══` `───` | 用 `---` 或留空行 | Unicode 重装饰线不自然 |

### 2.2 文字层面

**禁用短语**（Claude/GPT 标志性措辞）：
- `we'd rather X than Y`
- `gracefully falls back`
- `factored out because`
- `Core responsibilities:` / `Key points:` / `Main features:`
- `Note:` 标准开头
- `— which means` 后面跟解释

**改写原则**：
- 英文注释短平直，写事实不写"如何理解"
- 能用中文就用中文（项目在中国）
- 避免排比句、对仗结构

### 2.3 结构层面

| 痕迹 | 应改为 |
|---|---|
| docstring 里数字编号列表（`1. 2. 3.`） | 散文式一段话 |
| docstring 里有 `Args:` `Returns:` `Raises:` 三件套 | 保留，这是 Google-style，但简化 |
| 过度详细的 docstring（函数短但文档长） | 一句话概括 |
| 每个 import 上面都有注释 | 删掉注释 |
| `# ---` 大段分隔线 × 密度（一个文件 >10 处） | 精简到 ≤5 处 |

---

## 3. 自动化检测

已集成到项目脚本：

```bash
# 扫描全仓的 AI 痕迹
bash catfish/scripts/check_ai_tells.sh

# CI 严格模式（有痕迹则失败）
bash catfish/scripts/check_ai_tells.sh --strict
```

**提交前必须过这个检查**。

---

## 4. 人工审阅 Checklist

每次提交前，由人工开发者过一遍：

- [ ] 所有函数的**第一行 docstring**用一句话能说清做什么
- [ ] 注释解释**为什么**而非**是什么**（代码本身已表达是什么）
- [ ] 命名有团队习惯（不全是 LLM 倾向的"理想命名"）
- [ ] 允许少量"不完美"（比如偶尔的缩写、不太对仗的注释）
- [ ] 逻辑已被真实测试过（不只是 AI 说 "should work"）
- [ ] 过了 `ruff check` + `check_ai_tells.sh`
- [ ] 过了 `check_file_sizes.sh`

---

## 5. Git 提交规范（软著相关）

### 5.1 作者身份

每个 commit 的 author 必须是**真实开发者**：

```bash
git config user.name "陈红波"
git config user.email "chenhongbo@company.com"
```

**不允许**：
- 署名 "AI Assistant" / "Claude" / 匿名 bot
- 用虚构开发者署名

### 5.2 提交粒度

- **一个 commit 做一件事** —— 反映真实的开发思考步骤
- 避免"一次性提交 2000 行"的巨型 commit（典型 AI 批量生成痕迹）
- 大功能应该拆成多次小提交，每次解决一个子问题

### 5.3 提交信息

用中文或中英混合都可以，但不要用"完美"的 conventional commit：

```
# 好：自然，反映思考
修复 gateway 在 Gemini 3 preview 返回 429 时把错误当连接失败处理

# 不太好（太"合规"）：
fix(gateway): properly handle Gemini 3 preview 429 responses
by distinguishing them from connection errors in error handler
```

---

## 6. 软著申报清单（项目完成时）

准备提交申报前：

- [ ] 全部代码过了 check_ai_tells.sh（0 痕迹）
- [ ] 全部代码过了 check_file_sizes.sh
- [ ] 全部代码过了 ruff check
- [ ] Git 历史显示合理的开发时间跨度（不是几天突击写完）
- [ ] 至少有 2 个以上真实开发者的 commit 记录
- [ ] 核心模块有 CHANGELOG 记录演进
- [ ] 测试覆盖合理（不是 AI 式"每个函数一个测试"，但关键路径有）
- [ ] 文档（README / docs/）是人写或明显改写过的

---

## 7. 开发流程建议

推荐的 AI 协作方式：

```
1. 你：明确需求和约束（给 AI 上下文）
2. AI：草拟代码
3. 你：审阅、调整、补注释、改命名、删装饰
4. 你：本地运行验证
5. 你：commit（用你自己的名字）
6. 提交前：过 check_ai_tells.sh + ruff check
```

**反模式**：

```
× AI 写完直接 commit
× 不看代码就 merge
× 用 AI 的 commit message 模板
× 一次批量生成整个模块
```

---

## 8. 与其他规范的关系

- **CONVENTIONS.md**（代码规模、复杂度、命名）—— 和本文件并列，都要遵守
- **设计文档**（`catfish-design.md`）—— 对"做什么"的约束
- **本文件** —— 对"怎么提交"的约束

三者共同保证：鲶鱼是**你们团队自己的代码**，不只是 AI 的输出。

---

## 9. 活文档

发现新的 AI 痕迹 pattern → 加进 `check_ai_tells.sh` + 本文件 §2
发现规则过严 → PR 讨论 + 更新

---

**要点**：**AI 帮你快，人帮你合规**。提交前的那 5 分钟人工审阅，
是项目拥有软著、避免"一眼 AI"的关键。
