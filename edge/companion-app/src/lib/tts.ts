/** TTS 调用 helper — BL-VOICE2 (5/10).
 *
 * Rust 端 (commands/tts.rs) 调 piper subprocess 合成 wav, 返绝对路径.
 * 前端用 Tauri convertFileSrc 转 asset:// URL, 喂给 <audio>.play() 播放.
 *
 * 跟 STT 对称: lib/speech.ts (没单独抽, 在 ChatTab 内联) 调 invoke('speech_*').
 *
 * 全局只有一个"当前播放" — 点新消息的喇叭会停旧的, 避免重叠. 用 module-level
 * audio 实例 + URL 复用.
 *
 * BL-VOICE2 fix3 (5/10): 喂 piper 之前要做 markdown → 朗读文本 清洗 — LLM 输出
 * 含 *bold* / # 标题 / `code` / [链接](url) / 列表 - / 数学符号 / 表情 等,
 * piper 全当字符念会节奏乱. 鸿波反馈"断句不太合理是不是模型不够好" — 不是
 * 模型问题, 是输入文本含 markdown 没清洗.
 */

import { invoke } from "@tauri-apps/api/core";

/** 把 markdown / 代码块 / 链接等清洗成纯朗读文本 (BL-VOICE2 fix3, 5/10).
 *
 * 规则 (按优先级):
 *   1. 整段 fenced code block ```...``` → 完全删 (代码不朗读)
 *   2. inline code `xxx` → 删反引号但保留内容 (短语鲶鱼可能要念)
 *   3. [text](url) → 只留 text (URL 不朗读, 长 + 念字符乱)
 *   4. ![alt](url) → 删整段 (图片不朗读)
 *   5. **bold** / *italic* → 删星号留内容
 *   6. ~~strike~~ → 删波浪线留内容
 *   7. # / ## / ### 标题前缀 → 删 # 留文字
 *   8. 列表前缀 - / * / + + 序号 1. → 删, 加句号 ("第一条"语义)
 *   9. 表格分隔 | --- | → 删
 *   10. > 引用 → 删
 *   11. 多个连续换行 → 句号 + 空格 (piper 会断句换气)
 *   12. URL 残留 (https://... 没在 markdown 里) → 念 "链接" 替代
 *   13. emoji + 控制字符 → 删
 *   14. 半角逗号/句号 → 全角 (中文 piper 对全角标点断句更敏感)
 */
export function stripMarkdownForTTS(text: string): string {
  let s = text;

  // 1. fenced code block (```...``` 含语言标记)
  s = s.replace(/```[\s\S]*?```/g, " (代码块) ");
  // 2. inline code
  s = s.replace(/`([^`]+)`/g, "$1");
  // 4. 图片 (放 link 之前, 因为 ![] 跟 [] 都匹配)
  s = s.replace(/!\[([^\]]*)\]\([^)]*\)/g, "");
  // 3. 链接
  s = s.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1");
  // 5. bold / italic (顺序: 双星号先于单星号)
  s = s.replace(/\*\*([^*]+)\*\*/g, "$1");
  s = s.replace(/\*([^*]+)\*/g, "$1");
  s = s.replace(/__([^_]+)__/g, "$1");
  s = s.replace(/_([^_]+)_/g, "$1");
  // 6. strike
  s = s.replace(/~~([^~]+)~~/g, "$1");
  // 7. heading prefix
  s = s.replace(/^#{1,6}\s+/gm, "");
  // 8. list bullets / numbered
  s = s.replace(/^[\s]*[-*+]\s+/gm, "");
  s = s.replace(/^[\s]*\d+\.\s+/gm, "");
  // 9. table separator
  s = s.replace(/^\s*\|?\s*[-:]+\s*(\|\s*[-:]+\s*)+\|?\s*$/gm, "");
  // 表格内容 | a | b | c | → "a b c" (空格分隔, piper 自然断)
  s = s.replace(/^\|(.+)\|\s*$/gm, (_, body) =>
    body.split("|").map((c: string) => c.trim()).filter(Boolean).join(" "),
  );
  // 10. blockquote
  s = s.replace(/^>\s*/gm, "");
  // 12. 残留 URL (没被 [text](url) 包的)
  s = s.replace(/https?:\/\/\S+/g, " 链接 ");
  // 13. emoji + 控制字符 (基本面 emoji 范围)
  // 注: 不能用 /\p{Emoji}/u 因为 \p 还要 unicode-property-escape support.
  s = s.replace(
    /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{1F000}-\u{1F1FF}\u{1F200}-\u{1F2FF}]/gu,
    "",
  );
  // 11. 多换行 → 句号 (piper 句号断句更明显)
  s = s.replace(/\n{2,}/g, "。");
  s = s.replace(/\n/g, "。");
  // 14. 半角逗号/句号 → 全角 (中文 piper 对全角更敏感, 断句更自然)
  // 但小心 1.5 这种数字, 用 lookahead 不替换数字间的小数点
  s = s.replace(/,(?!\d)/g, "，");
  s = s.replace(/\.(?!\d)/g, "。");

  // 多空格 → 单空格, 句首尾 trim
  s = s.replace(/[ \t]{2,}/g, " ").trim();

  // 多重句号 (段连续换行变了一串 "。。。") 压成单个
  s = s.replace(/。{2,}/g, "。");

  // BL-VOICE2 fix6 (5/10): 长句强制插逗号
  s = insertCommasInLongRuns(s);

  return s;
}

/** 长句 (> THRESHOLD 字, 没标点) 强制插逗号 — BL-VOICE2 fix6 (5/10).
 *
 * 鸿波反馈"断句不理想是不是用 jieba". 不需要 jieba — piper 中文走音素级合成
 * 不依赖词边界, jieba 收益的 80% 跟"标点插入"重叠. 用 regex + 助词识别即可.
 *
 * 算法:
 *   1. 按 [。！？\n] 切句子
 *   2. 每句 > 30 字 → 在 12-25 字位置找最近的"助词后" 插 "，"
 *      助词: 的 了 是 在 和 与 或 但 则 而 也 着 过 就 都 把 被 让 给
 *           对 从 向 为 由 以 用 把 关于 由于 因为 所以 然而 因此
 *   3. 实在找不到助词 → 按 20 字硬切, 加 "，"
 *
 * 注意: 不切到数字 / 字母词中间 (避免 "ChatGPT" → "Chat，GPT").
 */
function insertCommasInLongRuns(text: string): string {
  const SOFT_THRESHOLD = 25;       // 超过这个字数开始考虑插逗号
  const MIN_CHUNK_KEYWORD = 5;     // 命中助词时, tail 最少 5 字 (放宽找更准的助词位置)
  const MIN_CHUNK_FALLBACK = 12;   // fallback 强切时, 两段都至少 12 字 (避免 "他，去了")

  // 助词 — 后面是天然停顿点
  const STOP_WORDS = [
    "的", "了", "是", "在", "和", "与", "或", "但", "则", "而",
    "也", "着", "过", "就", "都", "把", "被", "让", "给",
    "对", "从", "向", "为", "由", "以", "用",
    "关于", "由于", "因为", "所以", "然而", "因此", "不过",
  ];

  // 字母 / 数字 / 全角字符 (拉丁、CJK、数字) — 这些不应该被切到中间
  const isWordChar = (ch: string) => /[A-Za-z0-9_·゠-ヿ]/.test(ch);

  function processSentence(sent: string): string {
    if (sent.length <= SOFT_THRESHOLD) return sent;
    // 已经有逗号? 不动 (LLM 自己已经断好)
    if (sent.includes("，") || sent.includes(",")) return sent;

    // ── 第一步: 助词扫描 (放宽 MIN_CHUNK_KEYWORD = 5, 允许助词靠后) ──
    // 偏好接近 SOFT_THRESHOLD 的位置, 但只要助词命中就接受.
    let bestIdx = -1;
    let bestDist = Infinity;
    const kwUpper = sent.length - MIN_CHUNK_KEYWORD;
    const ideal = Math.min(SOFT_THRESHOLD, kwUpper);

    if (kwUpper >= MIN_CHUNK_KEYWORD) {
      for (let i = MIN_CHUNK_KEYWORD; i <= kwUpper; i++) {
        // 跳过词中切 (避免 "ChatGPT" → "Chat，GPT")
        if (isWordChar(sent[i] || "") && isWordChar(sent[i - 1] || "")) continue;

        for (const w of STOP_WORDS) {
          if (i >= w.length && sent.slice(i - w.length, i) === w) {
            const dist = Math.abs(i - ideal);
            if (dist < bestDist) {
              bestDist = dist;
              bestIdx = i;
            }
            break;
          }
        }
      }
    }

    // ── 第二步: 助词没找到 → fallback 在 ideal 位置硬切 (MIN_CHUNK_FALLBACK 严一点) ──
    if (bestIdx === -1) {
      const fbUpper = sent.length - MIN_CHUNK_FALLBACK;
      if (fbUpper < MIN_CHUNK_FALLBACK) return sent;  // 句子太短不切
      bestIdx = Math.min(ideal, fbUpper);
      // 避开拉丁/数字词中
      while (bestIdx < fbUpper && isWordChar(sent[bestIdx] || "") && isWordChar(sent[bestIdx - 1] || "")) {
        bestIdx++;
      }
    }

    const head = sent.slice(0, bestIdx);
    const tail = sent.slice(bestIdx);
    // 递归处理 tail (可能也很长)
    return head + "，" + processSentence(tail);
  }

  // 按 [。！？]+ 切, 保留分隔符
  const parts = text.split(/([。！？])/);
  const out: string[] = [];
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 0) {
      out.push(processSentence(parts[i]));
    } else {
      out.push(parts[i]);  // 标点本身
    }
  }
  return out.join("");
}

export interface TtsStatus {
  piper_installed: boolean;
  piper_path: string | null;
  default_voice: string;
  default_voice_ready: boolean;
  voice_dir: string | null;
}

/** 探测 piper 二进制 + 默认 voice 模型是否就绪. AgentPrefsCard 显状态用. */
export async function fetchTtsStatus(): Promise<TtsStatus> {
  return invoke<TtsStatus>("tts_status");
}

/** 合成 + 返 wav 路径 (绝对路径, /tmp/catfish-tts-<pid>-<ns>.wav).
 *  喂给 piper 的 text 已经 strip 过 markdown (BL-VOICE2 fix3). */
async function synthesizeWavPath(text: string, voice?: string): Promise<string> {
  const cleaned = stripMarkdownForTTS(text);
  return invoke<string>("tts_synthesize", { text: cleaned, voice: voice ?? null });
}

// 全局唯一 audio 实例 — 点新喇叭停旧的, 避免几条同时响.
let _currentAudio: HTMLAudioElement | null = null;
let _currentToken = 0;

/** 主入口: text → 合成 → 播放. 返 promise 在播放完成时 resolve.
 *
 * 行为:
 *   - 已有播放中的会立刻停掉
 *   - 合成中 await invoke (~500ms-2s 取决于文本长度)
 *   - 播放完 resolve, 中途被新调用打断则 reject 'aborted'
 *   - piper 不可用 / 模型缺失 → reject 详细错误 (UI 弹 toast)
 */
export async function speak(text: string, voice?: string): Promise<void> {
  // 立刻停旧的 (token 失效的 callback 进来会 noop)
  stopSpeaking();
  const myToken = ++_currentToken;

  let wavPath: string;
  try {
    wavPath = await synthesizeWavPath(text, voice);
  } catch (e) {
    throw new Error(`TTS 合成失败: ${e instanceof Error ? e.message : String(e)}`);
  }

  if (myToken !== _currentToken) {
    // 期间又被打断
    throw new Error("aborted");
  }

  // 转 asset:// URL — Tauri 配的 assetProtocol scope 含 /tmp/catfish-tts-*
  const { convertFileSrc } = await import("@tauri-apps/api/core");
  const url = convertFileSrc(wavPath);

  const audio = new Audio(url);
  _currentAudio = audio;

  return new Promise<void>((resolve, reject) => {
    audio.addEventListener("ended", () => {
      if (_currentAudio === audio) _currentAudio = null;
      resolve();
    });
    audio.addEventListener("error", () => {
      if (_currentAudio === audio) _currentAudio = null;
      reject(new Error("audio 播放失败 (CSP / asset protocol scope?)"));
    });
    audio.play().catch((e) => {
      if (_currentAudio === audio) _currentAudio = null;
      reject(new Error(`audio.play 拒绝: ${e?.message ?? e}`));
    });
  });
}

/** 停止当前播放 (没有就 noop). */
export function stopSpeaking(): void {
  _currentToken++;  // 让 in-flight 合成的 callback 知道自己被替换了
  if (_currentAudio) {
    try {
      _currentAudio.pause();
      _currentAudio.currentTime = 0;
    } catch {
      /* ignore */
    }
    _currentAudio = null;
  }
}

/** 当前是否在播放. */
export function isSpeaking(): boolean {
  return _currentAudio !== null && !_currentAudio.paused;
}
