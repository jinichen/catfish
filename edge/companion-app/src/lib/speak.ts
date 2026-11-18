/**
 * 朗读 (11/18 学习第 4 步, 答辩陪练评审提问): 用系统自带的语音 (Web Speech API) —— 不联网、不出本机。
 * macOS WKWebView 和 Windows WebView2 都带中文语音 (婷婷 / 慧慧 / 晓晓桌面版…); 没有中文语音就用默认的。
 * 选「最像中文」的那个: zh-CN 优先, 其次 zh-*; 同一语言里名字带 Premium / Enhanced / Natural 的优先 (更自然)。
 */
export function speechAvailable(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window && typeof SpeechSynthesisUtterance !== "undefined";
}

export function pickVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | null {
  const score = (v: SpeechSynthesisVoice) =>
    (v.lang === "zh-CN" ? 100 : v.lang.startsWith("zh") ? 60 : 0) + (/premium|enhanced|natural|neural/i.test(v.name) ? 10 : 0)
    + (v.localService ? 2 : 0);
  const best = [...voices].sort((a, b) => score(b) - score(a))[0];
  return best && score(best) >= 60 ? best : null;
}

export function speak(text: string, rate = 1.0): boolean {
  if (!speechAvailable() || !text.trim()) return false;
  const synth = window.speechSynthesis;
  synth.cancel();
  const u = new SpeechSynthesisUtterance(text.replace(/[*_`#>]/g, ""));
  const v = pickVoice(synth.getVoices());
  if (v) u.voice = v;
  u.lang = v?.lang ?? "zh-CN";
  u.rate = rate;
  synth.speak(u);
  return true;
}

export function stopSpeaking(): void {
  if (speechAvailable()) window.speechSynthesis.cancel();
}
