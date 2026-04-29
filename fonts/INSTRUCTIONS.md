# 客户 IT 部署字体 — 操作指引

> **谁看这份**: 客户公司 IT 同事 / 系统管理员
> **场景**: 部署鲶鱼到员工电脑后, 还需要补几个商业字体让公文模板渲染合规

---

## 一、确认你需要补哪些字体

鲶鱼生成央国企公文需要这 4 个字体:

| 字体 | 公司机器通常 | 鲶鱼 fallback |
|------|--------------|---------------|
| 方正小标宋简体 | ✅ 国企机预装 | 思源宋体 (近似但不是它) |
| 仿宋_GB2312 | ✅ Win 机预装 | 文泉驿仿宋 |
| 黑体 | ✅ 全平台预装 | (不需要) |
| 楷体_GB2312 | ✅ Win 机预装, mac 没有 | 思源宋体 |

确认 1: 你们公司有没有方正字库授权? (大概率有, 买 Office 一般附赠)
确认 2: 现役员工机字体齐不齐? 抽查几台 `fc-list` (Linux) / Font Book (mac) / 控制面板/字体 (Win) 看一下.

---

## 二、补字体的步骤 (公司域控统一推)

### Windows (推荐 — 域控批处理)

把字体文件 (.TTF / .OTF) 放到公司 SMB 共享 `\\公司内网\share\catfish-fonts\`, 域控推一个 logon script:

```powershell
# logon-install-fonts.ps1
$src = "\\file-server\share\catfish-fonts\*.TTF"
$dst = "$env:WINDIR\Fonts\"
Copy-Item -Path $src -Destination $dst -Force -ErrorAction SilentlyContinue
```

或者 **每台员工机**一次性手动: 全选字体文件 → 右键 → 安装 (需要管理员).

### macOS (员工自助 / 鲶鱼内置安装)

鲶鱼装好之后, 让员工:

1. 打开"访达" → 进入 `/Applications/Catfish Companion.app/Contents/Resources/fonts/customer/`
   (本鲶鱼安装目录下的 `fonts/customer/` 文件夹)
2. 把公司提供的字体 .TTF / .OTF 文件**复制**进去
3. 重启鲶鱼 — 鲶鱼第一次启动会自动注册字体到 `~/Library/Fonts/` (或弹窗征求同意)

或者员工**直接双击 .TTF** 在 Font Book 里安装, 也行.

### Linux (一般是服务器, 用户机不考虑)

```bash
mkdir -p ~/.fonts
cp /path/to/share/*.TTF ~/.fonts/
fc-cache -fv
```

---

## 三、验收

随意一个员工跟鲶鱼说:

> "写一份给公司领导关于 X 项目的请示件, 1 页"

鲶鱼输出 .docx, 员工 Word 打开后**目测**:
- 主标题字体是不是 "方正小标宋简体" (粗壮宋体, 不是默认宋体)
- 正文是不是 "仿宋_GB2312" (横平竖直, 不是默认宋体)
- 一级标题是不是 "黑体"

3 个都对 → 部署完成.

---

## 四、合规话术 (CISO 问你时)

**Q**: 鲶鱼自带这些商业字体吗?
**A**: 不带. 鲶鱼只在生成的 .docx 里写字体名 (不写字体二进制), 字体由公司机器自己装.

**Q**: 字体授权归谁?
**A**: 公司公司. 鲶鱼不参与字体授权链, 风险归零.

**Q**: 万一员工机字体没装上呢?
**A**: 鲶鱼 fallback 到思源宋体 (Adobe + Google 开源, SIL OFL 许可证), 视觉接近合规标准, 但**不完全等同于方正字体**. 建议做完字体推装.

---

## 五、麻烦时找谁

- 字体推装失败 / 域控脚本写不对 → 我们鲶鱼团队远程协助 (1 次/周, 已在 PoC 合同)
- 鲶鱼读不到 customer/ 字体 → `~/.catfish/companion.log` 抓日志给我们
- 员工说"字体不对" → 让员工把生成的 .docx 拷给鲶鱼团队, 我们检查字体名声明
