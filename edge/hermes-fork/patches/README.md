# Catfish 代码 patch 目录

这是啥
----

我们对 hermes 上游 (`~/.hermes/hermes-agent/`) 的**非品牌代码改动**, 以 `.patch`
文件形式存在这里. 父脚本 `../apply_brand_patch.py` 会在 `--apply / --revert /
--verify` 时自动遍历本目录, 用 `patch -p1` 把它们打到 hermes 源码上.

跟 brand RULES 的区别
----

| 维度 | RULES (apply_brand_patch.py 顶部) | patches/ (本目录) |
| --- | --- | --- |
| 改什么 | 单行字符串字面量 (Hermes -> 鲶鱼, ⚕ -> 🐟) | 代码块 (加常量 / 加 helper / 加分支) |
| 实现 | Python `str.replace()` / `re.sub()` | `patch -p1 -i NNNN.patch` |
| 锚点 | 原字符串本身 | git diff 上下文 (3 行) |
| 抗上游漂移 | 强 (字符串变了直接 MISS) | 中等 (`patch` fuzzy 容忍小改动) |
| 适用场景 | 品牌词 / 显示文案 | bugfix / 行为修复 / 加配置 |

两个机制**并存**, 互不干扰. `--apply` 先跑 RULES 再跑 patches.

命名规范
----

`NNNN-描述.patch`

- `NNNN`: 4 位数字, 按 apply 顺序递增 (0001 / 0002 / ...). 字典序就是 apply 序.
- `描述`: kebab-case, 简短说明改了啥, 跟 commit message 主语对得上.
- 一个 patch 文件 = 一个独立修复, 不要把无关改动塞进同一个 patch.

例子: `0001-api-server-cors-tauri-origin.patch`

怎么加新 patch
----

1. 在 hermes-agent 仓库 (catfish-local-patches 分支) 里把改动 commit 掉:
   ```bash
   cd ~/.hermes/hermes-agent
   git checkout catfish-local-patches
   # 改代码
   git commit -m "<描述>"
   ```

2. 从 commit 导出 patch 文件 (只挑那一个 commit 影响的文件, 别夹带):
   ```bash
   git show <commit_sha> --format="" -- <改的文件路径> \
     > ~/person_task/catfish/edge/hermes-fork/patches/NNNN-<描述>.patch
   ```

   如果改动跨多个文件 / 多个 commit, 用 `git diff`:
   ```bash
   git diff main..catfish-local-patches -- <文件> > .../patches/NNNN-...patch
   ```

3. 验证 patch 干净 (能在 main 上 apply, 能在 working tree reverse):
   ```bash
   cd ~/.hermes/hermes-agent
   # 当前在 catfish-local-patches 上, 文件含 patch 内容 -> reverse dry-run 必须过
   patch -p1 -R --dry-run -i .../patches/NNNN-...patch
   ```

4. 测试自动重打:
   ```bash
   cd ~/person_task/catfish/edge/hermes-fork
   python3 apply_brand_patch.py --revert    # 移除
   python3 apply_brand_patch.py --apply     # 重打
   python3 apply_brand_patch.py --verify    # 确认
   ```

5. commit 到 catfish 仓库 (这个目录在 catfish, 不在 hermes-agent):
   ```bash
   cd ~/person_task/catfish
   git add edge/hermes-fork/patches/NNNN-*.patch
   git commit -m "<BL-XXX>: 加 NNNN-... patch"
   ```

测试方法
----

完整 sanity test:

```bash
cd ~/person_task/catfish/edge/hermes-fork

# 1. dry-run 看会做啥
python3 apply_brand_patch.py

# 2. 验证当前状态
python3 apply_brand_patch.py --verify

# 3. revert + 重 apply 循环 (会改文件, 慎用)
python3 apply_brand_patch.py --revert
python3 apply_brand_patch.py --apply
python3 apply_brand_patch.py --verify
```

git hooks 集成
----

`--install-hooks` 已经在 hermes-agent 装了 post-merge / post-rewrite /
post-checkout, 每次 `git pull` 后自动跑 `--apply`. 加 patch 后不需要再装一次,
hook 会自动捡上新的 .patch 文件 (`sorted(PATCHES_DIR.glob("*.patch"))`).
