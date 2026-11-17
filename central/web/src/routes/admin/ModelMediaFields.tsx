/** 出图 / 出视频模型的专属配置 (11/17)。
 *
 * 文生图 / 图生图 / 文生视频 / 图生视频跟对话模型一样是一行模型配置, 区别是:
 *   · 按张 / 按秒计费 → 一个单价 (元 / 张、元 / 秒), 不用三段 token 单价
 *   · 没有上下文窗口、工具调用这些 → 换成默认参数、时长范围、清晰度档位、最多几张原图
 * 后端是 ModelConfig.price_per_unit + ModelConfig.media (media_config.MediaConfig)。
 *
 * 单独一个文件: ModelForm.tsx 已经 770 行 (红线 800)。
 */
import { useEffect, useState } from "react";

import { isVideoMode, modeUnit, type MediaConfig, type ModelConfig } from "../../lib/model_config";
import { Field, HINT, INPUT, MONO } from "./modelConfigShared";

function num(v: string): number | null {
  const t = v.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

/** 单价 —— 放在「展示与计价」那一栏里, 替换三段 token 单价。 */
export function MediaUnitPrice({ model, onChange }: { model: ModelConfig; onChange: (m: ModelConfig) => void }) {
  const unit = modeUnit(model.mode);
  return (
    <Field
      label={`单价（元 / ${unit}）`}
      hint={`百炼等厂商价目表里的价格（${isVideoMode(model.mode) ? "按生成视频的秒数，不同清晰度价格不同，按常用档位填" : "按成功生成的张数"}）。留空则审计页按 0 算，成本不准。`}
    >
      <input
        style={INPUT}
        type="number"
        step="0.001"
        value={model.price_per_unit ?? ""}
        onChange={(e) => onChange({ ...model, price_per_unit: num(e.target.value) })}
        placeholder={isVideoMode(model.mode) ? "0.6" : "0.2"}
      />
    </Field>
  );
}

/** JSON 对象编辑框: 输入时不打断, 失焦 / 合法时才写回。 */
function JsonObjectField({
  label,
  hint,
  value,
  placeholder,
  onChange,
}: {
  label: string;
  hint: string;
  value: Record<string, unknown> | undefined;
  placeholder: string;
  onChange: (v: Record<string, unknown>) => void;
}) {
  const [text, setText] = useState(() => (value && Object.keys(value).length ? JSON.stringify(value) : ""));
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setText(value && Object.keys(value).length ? JSON.stringify(value) : "");
  }, [JSON.stringify(value ?? {})]); // eslint-disable-line react-hooks/exhaustive-deps
  const commit = (t: string) => {
    if (!t.trim()) {
      setErr(null);
      onChange({});
      return;
    }
    try {
      const v = JSON.parse(t);
      if (!v || typeof v !== "object" || Array.isArray(v)) throw new Error("要是 {…} 对象");
      setErr(null);
      onChange(v as Record<string, unknown>);
    } catch (e) {
      setErr(`不是合法的 JSON 对象: ${e instanceof Error ? e.message : String(e)}`);
    }
  };
  return (
    <Field label={label} hint={err ?? hint}>
      <input
        style={{ ...MONO, ...(err ? { borderColor: "var(--status-err)" } : null) }}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={(e) => commit(e.target.value)}
        placeholder={placeholder}
      />
    </Field>
  );
}

/** 「能力与限制」那一栏换成这个。 */
export function MediaFields({ model, onChange }: { model: ModelConfig; onChange: (m: ModelConfig) => void }) {
  const media: MediaConfig = model.media ?? {};
  const set = (patch: Partial<MediaConfig>) => onChange({ ...model, media: { ...media, ...patch } });
  const video = isVideoMode(model.mode);
  return (
    <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid var(--border)" }}>
      <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 6 }}>
        {video ? "视频参数" : "图片参数"}{" "}
        <span style={{ color: "var(--text-muted)" }}>（照上游官方文档填；留空则交给上游的默认值）</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 8 }}>
        {video ? (
          <>
            <Field label="默认时长（秒）" hint="员工没说多长时用这个">
              <input
                style={INPUT}
                type="number"
                value={media.default_duration ?? ""}
                onChange={(e) => set({ default_duration: num(e.target.value) })}
                placeholder="5"
              />
            </Field>
            <Field label="最短 / 最长（秒）" hint="超出范围的请求会被夹到范围内（例：万相 2.6 是 2–15）">
              <div style={{ display: "flex", gap: 4 }}>
                <input
                  style={INPUT}
                  type="number"
                  value={media.min_duration ?? ""}
                  onChange={(e) => set({ min_duration: num(e.target.value) })}
                  placeholder="2"
                />
                <input
                  style={INPUT}
                  type="number"
                  value={media.max_duration ?? ""}
                  onChange={(e) => set({ max_duration: num(e.target.value) })}
                  placeholder="15"
                />
              </div>
            </Field>
            <Field label="清晰度档位" hint="逗号分隔，第一个是默认（例：720P, 1080P）。档位越高越贵">
              <input
                style={INPUT}
                value={(media.resolutions ?? []).join(", ")}
                onChange={(e) =>
                  set({ resolutions: e.target.value.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean) })
                }
                placeholder="720P, 1080P"
              />
            </Field>
            <Field
              label="画面大小怎么传"
              hint="照模型的 API 文档选：万相 2.x 文生传「宽*高」、图生只传档位；万相 3.0 是档位 + 画幅比。选错了上游会静默忽略，按它的默认（常是 1080P，更贵）出"
            >
              <select
                style={INPUT}
                value={media.size_param ?? ""}
                onChange={(e) => set({ size_param: (e.target.value || null) as MediaConfig["size_param"] })}
              >
                <option value="">默认（文生 = 宽*高，图生 = 档位）</option>
                <option value="size">宽*高（size）</option>
                <option value="resolution">只给档位（resolution）</option>
                <option value="resolution_ratio">档位 + 画幅比（resolution + ratio，万相 3.0）</option>
              </select>
            </Field>
            {model.mode === "image_to_video" ? (
              <Field
                label="首帧图怎么传"
                hint="万相 2.x 用 img_url，万相 3.0 用 media 里的 first_frame。选错了上游会忽略原图，出来的视频跟图无关"
              >
                <select
                  style={INPUT}
                  value={media.image_input ?? ""}
                  onChange={(e) => set({ image_input: (e.target.value || null) as MediaConfig["image_input"] })}
                >
                  <option value="">默认（img_url，万相 2.x）</option>
                  <option value="img_url">img_url（万相 2.x）</option>
                  <option value="first_frame">media 首帧 first_frame（万相 3.0）</option>
                </select>
              </Field>
            ) : null}
            <Field label="配音" hint="模型能不能出带声音的视频。能的话员工端可以选要不要声音">
              <select
                style={INPUT}
                value={media.audio == null ? "" : media.audio ? "yes" : "no"}
                onChange={(e) => set({ audio: e.target.value === "" ? null : e.target.value === "yes" })}
              >
                <option value="">不确定 / 不让员工选</option>
                <option value="yes">能出带声音的</option>
                <option value="no">只出无声的</option>
              </select>
            </Field>
          </>
        ) : model.mode === "image_to_image" ? (
          <Field label="最多几张原图" hint="一次改图最多带几张原图 / 参考图（百炼 Qwen-Image 是 1–3）。留空 = 1">
            <input
              style={INPUT}
              type="number"
              value={media.max_input_images ?? ""}
              onChange={(e) => set({ max_input_images: num(e.target.value) })}
              placeholder="1"
            />
          </Field>
        ) : null}
        <JsonObjectField
          label="默认参数（JSON）"
          hint="每次都带给上游的参数，员工请求里的同名参数优先；要强制的写在上面「参数覆盖」里"
          value={media.params}
          placeholder={video ? '{"watermark": false}' : '{"watermark": false, "prompt_extend": true}'}
          onChange={(params) => set({ params })}
        />
        <JsonObjectField
          label="尺寸表（JSON，可选）"
          hint={
            video
              ? '覆盖内置的「档位/画幅 → 宽*高」，例 {"720P/16:9": "1280*720"}'
              : '覆盖横 / 方 / 竖三种画幅的尺寸，例 {"landscape": "1664*928"}'
          }
          value={media.sizes}
          placeholder={video ? '{"720P/16:9": "1280*720"}' : '{"square": "1328*1328"}'}
          onChange={(sizes) => set({ sizes: sizes as Record<string, string> })}
        />
      </div>
      <div style={HINT}>
        上游模型写成 <code>dashscope/型号</code>（阿里百炼原生接口），其它厂商照对话模型的写法（如{" "}
        <code>openai/gpt-image-1</code>）。每种用途挂「默认」的那个给员工用；员工没权限或没配 key 时落到同用途的下一个。
      </div>
    </div>
  );
}
