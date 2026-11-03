// @vitest-environment jsdom
/** 11/3: 天气按网络位置定到国外时提示; 点它设常驻城市, 保存后强制重拉。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  set: vi.fn(async () => undefined),
  get: vi.fn(async () => ({ fetchedAt: "t", entries: [], stale: false, error: null })),
}));
vi.mock("../../lib/weather", async (orig) => ({
  ...(await orig<typeof import("../../lib/weather")>()),
  weatherConfigGet: vi.fn(async () => ({ homeCity: "", tempCities: ["上海"] })),
  weatherConfigSet: api.set,
  weatherGet: api.get,
}));

import WeatherChip from "./WeatherChip";

const entry = (over: Record<string, unknown>) => ({
  city: "Takehiracho", tempC: "18", desc: "阴", humidity: "1", wind: "1", icon: "☁️", rawArea: "", ...over,
});

afterEach(cleanup);

describe("WeatherChip", () => {
  it("按网络位置定到国外: 提示定位可能不准", () => {
    render(<WeatherChip weather={{ fetchedAt: "t", stale: false, error: null,
      entries: [entry({ country: "Japan", autoLocated: true })] }} onChange={() => {}} />);
    expect(screen.getByText(/定位可能不准/)).toBeTruthy();
  });

  it("设了城市 / 国内: 不提示", () => {
    render(<WeatherChip weather={{ fetchedAt: "t", stale: false, error: null,
      entries: [entry({ city: "福州", country: "China", autoLocated: false })] }} onChange={() => {}} />);
    expect(screen.queryByText(/定位可能不准/)).toBeNull();
  });

  it("点天气填城市保存: 写配置 (保留临时城市) 并强制重拉", async () => {
    const onChange = vi.fn();
    render(<WeatherChip weather={{ fetchedAt: "t", stale: false, error: "本地: 超时", entries: [] }} onChange={onChange} />);
    await act(async () => { fireEvent.click(screen.getByText(/设城市/)); });
    fireEvent.change(screen.getByLabelText("常驻城市"), { target: { value: " 福州 " } });
    await act(async () => { fireEvent.click(screen.getByText("保存")); });
    expect(api.set).toHaveBeenCalledWith({ homeCity: "福州", tempCities: ["上海"] });
    expect(api.get).toHaveBeenCalledWith(true);
    expect(onChange).toHaveBeenCalled();
  });
});
