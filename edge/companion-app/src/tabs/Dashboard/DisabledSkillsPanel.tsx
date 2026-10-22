/**
 * 技能库「高级」里的已停用技能 (10/22)。
 *
 * Companion 第一次启动时把 hermes 自带、办公用不上的技能 (机器学习 / GitHub / 写代码 / 智能家居 ...)
 * 默认停用 —— 技能清单每次聊天都整份发给模型, 白占几千字, 首个回复更慢。需要的时候在这里点「启用」,
 * 之后不会再被关掉 (src-tauri/src/services/hermes_skills_default.rs)。新对话生效。
 */
import { useEffect, useState } from "react";

import { fetchDisabledSkills, setSkillsEnabled } from "../../lib/tauri";
import { muted } from "../Collab/formsUi";
import { itemStyle } from "../Collab/roomLinkUi";

export default function DisabledSkillsPanel() {
  const [names, setNames] = useState<string[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = () => {
    fetchDisabledSkills()
      .then(setNames)
      .catch((e) => setMsg(`读不到停用列表: ${String(e)}`));
  };
  useEffect(load, []);

  const enable = async (list: string[], key: string) => {
    setBusy(key);
    setMsg(null);
    try {
      const n = await setSkillsEnabled(list, true);
      setMsg(n > 0 ? `已启用 ${n} 个, 新开的对话里生效。` : "没有需要启用的。");
      load();
    } catch (e) {
      setMsg(`启用失败: ${String(e)}`);
    } finally {
      setBusy(null);
    }
  };

  if (names === null && !msg) return null;
  return (
    <div style={{ ...itemStyle, gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <strong style={{ fontSize: 13 }}>已停用的技能 ({names?.length ?? 0})</strong>
        {names && names.length > 0 && (
          <button type="button" disabled={busy !== null} onClick={() => void enable([], "*")}
                  style={{ fontSize: 12, cursor: busy ? "wait" : "pointer" }}>
            {busy === "*" ? "启用中…" : "全部启用"}
          </button>
        )}
      </div>
      <div style={{ ...muted, fontSize: 12 }}>
        机器学习、GitHub、写代码、智能家居这类办公用不上的自带技能默认不开 —— 它们的说明每次聊天都要发给模型,
        开着会让小鲶回复变慢。需要哪个点「启用」, 新开的对话里就能用, 以后也不会再被关掉。
      </div>
      {names && names.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {names.map((n) => (
            <span key={n} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12,
                                   fontFamily: "var(--font-mono)", padding: "2px 6px",
                                   border: "1px solid var(--catfish-border)", borderRadius: 4 }}>
              {n}
              <button type="button" disabled={busy !== null} onClick={() => void enable([n], n)}
                      style={{ fontSize: 11, padding: "0 4px", cursor: busy ? "wait" : "pointer" }}>
                {busy === n ? "…" : "启用"}
              </button>
            </span>
          ))}
        </div>
      )}
      {msg && <div style={{ fontSize: 12 }}>{msg}</div>}
    </div>
  );
}
