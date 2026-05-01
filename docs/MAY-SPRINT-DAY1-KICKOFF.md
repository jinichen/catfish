# 五一 Day 1 启动包 (5/1 9 点开干)

> 4-30 晚整理. 5/1 早 9 点直接按这个干, 不要再花脑力规划.

---

## 9:00 ~ 13:00 · 多模态语音输入 (4h)

### 阶段 1 (9:00 ~ 10:00) — SFSpeechRecognizer PoC 试通 1h

**目标**: 1 小时内验证 SFSpeechRecognizer 在 Tauri Rust 能不能调通.

**起手代码** (新建 `edge/companion-app/src-tauri/src/commands/speech.rs`):

```rust
use tauri::Window;

#[cfg(target_os = "macos")]
#[tauri::command]
pub fn start_speech_recognition(window: Window) -> Result<(), String> {
    use objc::{msg_send, sel, sel_impl, runtime::Object};
    use objc::class;
    
    unsafe {
        // Step 1: 拿默认 recognizer
        let recognizer_class = class!(SFSpeechRecognizer);
        let recognizer: *mut Object = msg_send![recognizer_class, defaultRecognizer];
        
        if recognizer.is_null() {
            return Err("SFSpeechRecognizer 不可用 (macOS < 10.15?)".to_string());
        }
        
        // Step 2: 申请权限 (异步回调, PoC 阶段先打 log)
        // SFSpeechRecognizer.requestAuthorization { status in ... }
        let _: () = msg_send![recognizer_class, requestAuthorization: ^(status: i64) {
            println!("speech_auth_status: {}", status);
        }];
    }
    
    Ok(())
}
```

**Cargo.toml 加依赖** (`edge/companion-app/src-tauri/Cargo.toml`):

```toml
[target.'cfg(target_os = "macos")'.dependencies]
objc = "0.2"
objc-foundation = "0.1"
core-foundation = "0.9"
```

**Info.plist 加权限** (`edge/companion-app/src-tauri/Info.plist`):

```xml
<key>NSMicrophoneUsageDescription</key>
<string>鲶鱼需要麦克风做语音输入</string>
<key>NSSpeechRecognitionUsageDescription</key>
<string>鲶鱼需要 Speech 权限把你说的话转成文本</string>
```

**验证**: `cargo build --release` 通过 → 启动 Companion → 点 🎤 按钮 → 看 Tauri 日志有 `speech_auth_status: 3` (=authorized) 即 PoC 通过.

---

### 阶段 2A — SFSpeechRecognizer 通了 (10:00 ~ 13:00, 3h)

**做完整流程**:
- AVAudioEngine 捕麦 + 流式输入到 SFSpeechAudioBufferRecognitionRequest (1.5h)
- recognitionTaskWithRequest_resultHandler 回调 → emit 事件给前端 (0.5h)
- 前端 React: 按住 🎤 按钮说话, 松开停止, 文本填入对话框 (1h)

### 阶段 2B — SFSpeechRecognizer 不通 (10:00 ~ 13:00, 3h)

**fallback 切方案 X — 利用 macOS 系统原生 dictation**:

不用 SFSpeechRecognizer. macOS 系统层早有 dictation (按 `fn fn` 双击触发). 鲶鱼做的事:
- 🎤 按钮点击 → Tauri 调 `osascript` 模拟按 `fn fn` 触发系统 dictation
- 系统 dictation 自己弹窗收音, 转文字, 自动填到当前焦点输入框 (Companion 对话框)
- 鲶鱼检测对话框文本变化, 自动 trigger send

**优点**: 0 代码量, 用户体验跟系统其他 app 一致, 不用申请权限 (用户已经在系统里授过)
**缺点**: UX 不是"鲶鱼内嵌按钮", 是"鲶鱼提示用户按 fn fn"
**起手代码**:

```rust
#[cfg(target_os = "macos")]
#[tauri::command]
pub fn trigger_macos_dictation() -> Result<(), String> {
    std::process::Command::new("osascript")
        .arg("-e")
        .arg("tell application \"System Events\" to key code 63 using {function down}")
        .output()
        .map_err(|e| e.to_string())?;
    Ok(())
}
```

(key code 63 + fn modifier 模拟 fn fn 双击, 可能需要调试)

**或者更简单 fallback**: Companion UI 加提示 "按 fn fn 双击启动 macOS 系统听写", 0 代码, 文档解决.

---

## 13:00 ~ 14:00 · 午饭 + 休息

---

## 14:00 ~ 18:00 · 文件上传 (4h)

### 阶段 3 (14:00 ~ 15:00) — 前端 UI 1h

**注意**: `edge/companion-app/src/components/FilePill.tsx` 已存在 (4-28 ship 文件下载 UI 优雅化时做的). Day 1 复用 FilePill 显示已选文件, 新建 `FileUpload.tsx` 处理拖拽逻辑.

**起手代码** (`edge/companion-app/src/components/FileUpload.tsx`):

```tsx
import { useState } from 'react';

export function FileUpload({ onUpload }: { onUpload: (files: File[]) => void }) {
  const [files, setFiles] = useState<File[]>([]);
  
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const dropped = Array.from(e.dataTransfer.files);
    setFiles(prev => [...prev, ...dropped]);
    onUpload(dropped);
  };
  
  return (
    <div onDrop={handleDrop} onDragOver={e => e.preventDefault()}>
      {/* 拖拽区域 + FilePill chip 显示已选文件 */}
      {files.map(f => (
        <span key={f.name} className="filepill">
          📎 {f.name} ({(f.size / 1024).toFixed(1)} KB)
        </span>
      ))}
    </div>
  );
}
```

集成到 ChatInput, 文件随消息一起送到后端.

### 阶段 4 (15:00 ~ 18:00) — 后端解析 3h

**位置**: `edge/tool-bridge/src/catfish_tool_bridge/file_parser.py` (新建)

```python
"""文件解析 — PDF/Excel/Word → 文本, inject 到对话 system prompt 顶部.

支持格式 (4-30 起步版):
- .pdf   pypdf
- .xlsx  openpyxl (skill 已用)
- .docx  python-docx (skill 已用)
- .csv   csv 标准库
- .txt / .md  直接读

不支持:
- .doc (老 Word) — 提示员工另存为 .docx
- 加密 PDF — 提示员工先解密
"""

from __future__ import annotations
from pathlib import Path
from typing import Any

def parse_file(file_path: str | Path) -> dict[str, Any]:
    """解析文件返回 {filename, type, text, char_count, error}"""
    path = Path(file_path)
    suffix = path.suffix.lower()
    
    parsers = {
        '.pdf': _parse_pdf,
        '.xlsx': _parse_xlsx,
        '.xls': _parse_xlsx,
        '.docx': _parse_docx,
        '.csv': _parse_csv,
        '.txt': _parse_text,
        '.md': _parse_text,
    }
    parser = parsers.get(suffix)
    if not parser:
        return {"filename": path.name, "type": suffix, "error": f"不支持 {suffix}"}
    
    try:
        text = parser(path)
        return {
            "filename": path.name,
            "type": suffix,
            "text": text[:50000],  # 截断防 token 爆炸
            "char_count": len(text),
            "truncated": len(text) > 50000,
        }
    except Exception as e:
        return {"filename": path.name, "type": suffix, "error": str(e)}


def _parse_pdf(path: Path) -> str:
    # gateway venv 已装 pypdfium2 (4-30 验证), 用它不用 pypdf
    import pypdfium2 as pdfium  # noqa: PLC0415
    pdf = pdfium.PdfDocument(str(path))
    parts = []
    for i in range(len(pdf)):
        page = pdf[i]
        textpage = page.get_textpage()
        parts.append(textpage.get_text_range())
    return "\n\n".join(parts)


def _parse_xlsx(path: Path) -> str:
    from openpyxl import load_workbook  # noqa: PLC0415
    wb = load_workbook(path, read_only=True, data_only=True)
    parts = []
    for sheet in wb.worksheets:
        parts.append(f"## Sheet: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            cells = [str(c) if c is not None else "" for c in row]
            parts.append("\t".join(cells))
    return "\n".join(parts)


def _parse_docx(path: Path) -> str:
    from docx import Document  # noqa: PLC0415
    doc = Document(str(path))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n\n".join(parts)


def _parse_csv(path: Path) -> str:
    import csv  # noqa: PLC0415
    with path.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.reader(f)
        return "\n".join("\t".join(row) for row in reader)


def _parse_text(path: Path) -> str:
    return path.read_text(encoding='utf-8', errors='replace')
```

集成到 catfish_tools.py 加新工具 `catfish_parse_file`, 让 LLM 知道有这个 tool 可调.

### 阶段 5 (18:00 ~ 19:00) — 测试 + commit 1h

跑通 4 个真实文件:
- 一份 PDF (鸿波公司汇报材料 if 拿到)
- 一份 Excel (鸿波公司周报)
- 一份 Word (鸿波公司任意 docx)
- 一份 CSV (任意)

每个文件上传后 → Companion 对话 → 模型应该能引用文件内容回答问题.

commit:
```bash
git commit -m "feat(multimodal): macOS 听写 + 文件上传 PDF/Excel/Word

Day 1 of 五一 sprint:
- src-tauri/src/commands/speech.rs SFSpeechRecognizer (or fallback fn fn)
- src/components/FileUpload.tsx 拖拽 + FilePill
- tool-bridge/file_parser.py 5 格式解析 + 50KB 截断
- catfish_parse_file 工具注册
- 测试 4 真实文件跑通"
```

---

## 19:00 ~ 21:00 · 收工 + Day 2 准备

- 写当天 CHANGELOG (在 4-30 段后加 5/1 段)
- 同步 BACKLOG §M.1 三项 ⬜ → ✅
- 更新 PROJECT-STATUS W19a 进度
- 看一眼 Day 2 (Skill 全生命周期 4 步) 起手位置

---

## 关键决策点 (10:00 必须拍板)

```
SFSpeechRecognizer PoC 1h 试:
  通了 → 阶段 2A (3h 完整集成 流式 + push-to-talk)
  不通 → 阶段 2B (3h fallback 方案 X — 系统原生 fn fn dictation)
```

**判断标准**: cargo build 通过 + 启 Companion + 点按钮 + Tauri log 有 `speech_auth_status: 3` (=authorized).

---

## 应急

- macOS 听写 / SFSpeechRecognizer 完全做不出来 → Day 1 上午改做文件上传 UI (上下午对调), 听写延到 5/6+ 用 whisper.cpp 集成
- 文件上传 PDF/Excel 解析有依赖问题 → catfish gateway venv 已经装了 **pypdfium2 + openpyxl + docx** (4-30 实测), tool-bridge 用同 venv 即可, 不用额外 pip install
- 测试发现某个文件类型解析烂 → 50KB 截断 + 清晰错误信息给员工, 不强求所有 PDF 解析完美 (扫描版 PDF 需要 OCR, 不在 5/1 范围)
