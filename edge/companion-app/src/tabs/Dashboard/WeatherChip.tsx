/** 早安页头上的天气。11/3: 点它可以设常驻城市。
 *
 * 鸿波 11/3: 显示「Takehiracho 18°C Overcast」—— 没设城市时 wttr.in 按访问 IP 定位, 这台机器的
 * 流量全走代理, IP 在代理出口 (日本)。后台一直有 home_city 配置, 只是界面没入口。现在点天气就能填;
 * 按网络位置定到国外时直接提示「定位可能不准」。
 */
import { useState } from "react";

import { likelyWrongLocation, weatherConfigGet, weatherConfigSet, weatherGet, type WeatherCache } from "../../lib/weather";

const muted = { fontSize: 12, color: "var(--catfish-text-muted)" } as const;
const linkBtn = {
  background: "transparent", border: "none", padding: 0, cursor: "pointer", fontFamily: "inherit",
  ...muted,
} as const;

export default function WeatherChip({ weather, onChange }: { weather: WeatherCache | null; onChange: (w: WeatherCache) => void }) {
  const [editing, setEditing] = useState(false);
  const [city, setCity] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function open() {
    setError("");
    try {
      setCity((await weatherConfigGet()).homeCity);
    } catch {
      setCity("");
    }
    setEditing(true);
  }

  async function save() {
    setBusy(true);
    setError("");
    try {
      const cfg = await weatherConfigGet().catch(() => ({ homeCity: "", tempCities: [] }));
      await weatherConfigSet({ ...cfg, homeCity: city.trim() });
      onChange(await weatherGet(true));
      setEditing(false);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (editing) {
    return (
      <span style={{ display: "inline-flex", gap: 6, alignItems: "center", ...muted }}>
        <input
          autoFocus
          aria-label="常驻城市"
          value={city}
          placeholder="常驻城市, 如 福州 (留空 = 按网络位置)"
          onChange={(e) => setCity(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void save();
            if (e.key === "Escape") setEditing(false);
          }}
          style={{ fontSize: 12, padding: "2px 6px", width: 220, background: "var(--catfish-bg)", color: "var(--catfish-text)",
            border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-sm)", fontFamily: "inherit" }}
        />
        <button type="button" style={{ ...linkBtn, color: "var(--catfish-cyan)" }} disabled={busy} onClick={() => void save()}>
          {busy ? "保存中…" : "保存"}
        </button>
        <button type="button" style={linkBtn} onClick={() => setEditing(false)}>取消</button>
        {error && <span style={{ color: "var(--catfish-orange)" }}>{error}</span>}
      </span>
    );
  }

  if (weather && weather.entries.length > 0) {
    const wrong = weather.entries.some(likelyWrongLocation);
    return (
      <button
        type="button"
        onClick={() => void open()}
        title={wrong ? "按网络位置定的, 网络走了代理时不准 —— 点这里设常驻城市" : `点这里设常驻城市 · wttr.in · ${weather.fetchedAt}`}
        style={{ ...linkBtn, display: "inline-flex", gap: 8, alignItems: "center" }}
      >
        {weather.entries.map((e, i) => (
          <span key={i}>
            {e.icon} {e.city || "本地"} {e.tempC}°C
            {e.desc && <span style={{ opacity: 0.7 }}> {e.desc}</span>}
          </span>
        ))}
        {wrong && <span style={{ color: "var(--catfish-hint-amber-text)" }}>· 定位可能不准, 点这里设城市</span>}
      </button>
    );
  }

  if (weather?.error) {
    return (
      <button type="button" onClick={() => void open()} title={weather.error} style={{ ...linkBtn, fontSize: 11, opacity: 0.8 }}>
        🌐 天气不可用 · 设城市
      </button>
    );
  }
  return null;
}
