/** P3.3.8 (6/10 鸿波): 早安天气 TS wrapper.
 *
 * 走 ~/.catfish/weather_cache.json (6h cache, 一天 4 次自然落点).
 * wttr.in 拉, 无需 API key. home_city 空 → 按 wttr.in 服务端 IP 自动定位.
 * 详见 src-tauri/src/commands/weather.rs.
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";

export interface WeatherEntry {
  city: string;
  tempC: string;
  desc: string;
  humidity: string;
  wind: string;
  icon: string;
  rawArea: string;
}

export interface WeatherCache {
  fetchedAt: string;        // ISO-8601
  entries: WeatherEntry[];
  stale: boolean;
  error: string | null;
}

export interface WeatherConfig {
  homeCity: string;         // 空 = wttr.in IP 自动
  tempCities: string[];     // 临时城市
}

/** 拉 cache, force=true 强制重拉. */
export const weatherGet = (force = false) =>
  rawInvoke<WeatherCache>("weather_get", { force });

export const weatherConfigGet = () =>
  rawInvoke<WeatherConfig>("weather_config_get");

export const weatherConfigSet = (config: WeatherConfig) =>
  rawInvoke<void>("weather_config_set", { config });
