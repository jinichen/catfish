/** 教学过程中就地存密码 —— 嵌在那条 tool call 底下。
 *
 * # 为什么是"就地"，不是"去 📚 里存"
 *
 * 老流程里存密码和教学是两条不相通的路：员工先去 📚 存一条、起个名字、复制
 * 引用串，再回教学里告诉模型用哪个 ref。中间那次**人工搬运**是所有问题的根：
 * 搬错了对不上，搬对了又被 `_infer_params` 焊进冻结的 `script.py` —— 8/17
 * 鸿波改了 EIS 密码，UI 存进 `catfish-teaching:http://eis.ffcs.cn`，而冻结的
 * skill 读的是 4/28 那条 `eis_password`，登录报"账号或密码错误"，两边谁也不
 * 知道谁。
 *
 * 密码在**它被需要的那一刻**捕获，就没有中间环节了：站点是当前页给的，绑定
 * 关系是系统连的，不经过人的记忆，也没有任何字符串可以被焊死。
 *
 * # 红线
 *
 * 密码只走 `saveTeachingCredential`（Tauri IPC → Rust → 系统凭据库）。
 * 不进 shell 参数、不进配置文件、不进聊天消息、不进模型上下文。存完发给模型
 * 的那句话由 `retryPrompt(site)` 生成 —— 它的签名里根本没有密码参数。
 *
 * # 两条路
 *
 *   新存一条        → saveTeachingCredential(site, password, [site])
 *                     label 直接用 hostname，员工不用给它起名字（起名字正是
 *                     8/17 那串套娃引用的来源）
 *   挂到已存的那条  → addTeachingCredentialSite(label, site)
 *                     多入口共用一个密码 (eis → neis 跳转) 走这条，不重输密码
 */

import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import {
  addTeachingCredentialSite,
  listTeachingCredentials,
  saveTeachingCredential,
  type TeachingCredential,
} from "../../../lib/tauri";
import {
  linkableCredentials,
  retryPrompt,
  type CredentialRequest,
} from "../../../lib/needsCredential";

/** 发一条普通用户消息 —— 跟审批按钮同一条路 (ChatPanel 监听这个 event)。 */
function sendToChat(text: string) {
  window.dispatchEvent(
    new CustomEvent("catfish:approval-send", { detail: { text } }),
  );
}

export default function InlineCredentialPrompt({ req }: { req: CredentialRequest }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [others, setOthers] = useState<TeachingCredential[]>([]);
  /** 列表读完了没。没读完就先什么都不显 —— 见下面 alreadySaved。 */
  const [loaded, setLoaded] = useState(false);

  // 本机已存的其它凭据 —— 用来提供"挂到已有的那条"。列不出来不影响新存一条,
  // 所以失败只 warn (但仍然标 loaded, 否则一直卡在空白)。
  useEffect(() => {
    void (async () => {
      try {
        setOthers(await listTeachingCredentials());
      } catch (e) {
        console.warn("[credential] 列表读取失败:", e);
      } finally {
        setLoaded(true);
      }
    })();
  }, []);

  const finish = (msg: string) => {
    setDone(msg);
    setPassword("");
    sendToChat(retryPrompt(req.site));
  };

  const saveNew = async () => {
    setError("");
    setBusy(true);
    try {
      // label 用 hostname 本身。让员工另起一个名字，就又多了一个只有他知道
      // 的映射 —— 8/17 那串套娃引用正是从"名字"这个自由字段长出来的。
      await saveTeachingCredential(req.site, password, [req.site]);
      finish(`已保存 ${req.site} 的密码`);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const attachTo = async (label: string) => {
    setError("");
    setBusy(true);
    try {
      await addTeachingCredentialSite(label, req.site);
      finish(`${req.site} 已挂到「${label}」，用同一个密码`);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <div style={wrapStyle}>
        <div style={{ ...hintStyle, color: "var(--catfish-cyan)" }}>✓ {done}，正在重试…</div>
      </div>
    );
  }

  // 这个站点其实已经存过了 —— 切走会话再回来时会走到这儿。
  //
  // tool 结果是持久化的, 重新加载会话时那条 "needs_credential" 原样还在,
  // 于是一张**早就处理完**的卡片会再弹一次密码框。员工看到"又要我输密码",
  // 只能靠自己记得刚才存过没有。
  //
  // ⚠ 判据**故意比后端窄**: 只认 sites 命中和 label 精确相等, 不去解析 label
  //   里的 URL (后端 sites_of 会退回解析 label)。理由是不能在 TS 里再写第三份
  //   hostname 归一化 —— 那正是这次要消灭的东西。窄的后果是"已经存过了却还
  //   显示输入框", 员工再存一次覆盖成同一条, 无害; 反过来 (存过了却不显示)
  //   才会让人卡住。方向选安全的那边。
  //
  // 列表没读完之前先不渲染: 不然会先闪一个密码输入框再消失, 看着像 bug。
  if (!loaded) return null;

  //
  // ⚠ 只在 reason==="missing" 时才走这条捷径。
  //   `unreadable` 是"索引里有、钥匙串里没有" —— 索引照样说"存过了", 按它藏掉
  //   输入框的话员工只会看到一句"应该已经处理完了", 而下一步永远填不上。
  //   8/18 实撞: keyring 少开一个 feature, 密码全进了 mock store, 索引却记着,
  //   于是每次都走进这个死角。
  const alreadySaved =
    req.reason === "missing" &&
    others.some((c) => c.label === req.site || c.sites?.includes(req.site));
  if (alreadySaved) {
    return (
      <div style={wrapStyle}>
        <div style={hintStyle}>
          {req.site} 的密码本机已经存过了 —— 这一步应该已经处理完。
          要改密码去 📚 → 登录密码。
        </div>
      </div>
    );
  }

  // 能"共用同一个密码"的只有**没**覆盖当前站点的那几条。见 linkableCredentials。
  const linkable = linkableCredentials(others, req.site);

  return (
    <div style={wrapStyle} onClick={(e) => e.stopPropagation()}>
      {/* ⚠ 标题必须跟 reason 走。
          8/19 鸿波截图: 📚 里明明列着 neis.ffcs.cn, 这里却写"还没存过登录密码"
          —— 两个界面当着他的面互相打脸。这一轮走的是 unreadable (索引里有、
          取不出来), 硬写"还没存过"就是在撒谎, 而且把人往"是不是我没存对"上带。 */}
      <div style={titleStyle}>
        {req.reason === "missing" ? (
          <>
            <strong>{req.site}</strong> 还没存过登录密码
          </>
        ) : (
          <>
            <strong>{req.site}</strong> 的密码存过了，但这次<strong>没取出来</strong>
          </>
        )}
      </div>
      {req.reason !== "missing" && (
        <div style={hintStyle}>
          重新输一次会覆盖掉旧的那条。要是输完还是这样，就不是密码的问题 ——
          说明取密码的通道断了，重启一下鲶鱼 Companion。
        </div>
      )}
      {req.pageTitle && <div style={hintStyle}>当前页：{req.pageTitle}</div>}

      <div style={rowStyle}>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && password && !busy) void saveNew();
          }}
          placeholder={`${req.site} 的登录密码`}
          autoComplete="off"
          style={inputStyle}
        />
        <button
          type="button"
          disabled={busy || !password}
          onClick={() => void saveNew()}
          style={primaryBtnStyle}
        >
          {busy ? "保存中…" : "保存并继续"}
        </button>
      </div>

      {/* 多入口: eis.ffcs.cn 存过了, 登录时跳到 neis.ffcs.cn。
          这时候不该让员工把同一个密码再输一遍 —— 输两遍就是两条凭据,
          下次改密码只改一条，另一条继续用旧的，又回到 8/17 那个局面。

          ⚠ 用 linkable 不是 others: 已经覆盖当前站点的那条挂上去是**静默空转**
          (点了没反应, 再点还是没反应)。判据在 linkableCredentials, 那儿写了为什么。 */}
      {linkable.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={hintStyle}>或者：跟本机已存的某条用同一个密码</div>
          <div style={chipRowStyle}>
            {linkable.map((c) => (
              <button
                key={c.label}
                type="button"
                disabled={busy}
                onClick={() => void attachTo(c.label)}
                title={
                  c.sites?.length
                    ? `已覆盖：${c.sites.join("、")}`
                    : "这条还没配站点，挂上去之后就按站点找得到了"
                }
                style={chipStyle}
              >
                {c.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {error && <div style={errStyle}>{error}</div>}
      <div style={{ ...hintStyle, fontSize: 11 }}>
        密码只写入本机系统凭据库，不进聊天、配置文件或终端。以后改密码在 📚 →
        保存登录密码 里改，不用重新教一遍。
      </div>
    </div>
  );
}

const wrapStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: "10px 12px",
  borderTop: "1px solid var(--catfish-border)",
};
const titleStyle: CSSProperties = { fontSize: 13, lineHeight: 1.4 };
const hintStyle: CSSProperties = {
  fontSize: 12,
  color: "var(--catfish-text-muted)",
  lineHeight: 1.5,
};
const rowStyle: CSSProperties = { display: "flex", gap: 8, alignItems: "center" };
const inputStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  padding: "6px 9px",
  border: "1px solid var(--catfish-border)",
  borderRadius: 6,
  background: "transparent",
  color: "var(--catfish-text)",
  fontSize: 13,
};
const primaryBtnStyle: CSSProperties = {
  padding: "6px 12px",
  border: "1px solid var(--catfish-cyan)",
  borderRadius: 6,
  background: "var(--catfish-cyan)",
  color: "white",
  cursor: "pointer",
  fontSize: 12,
  flexShrink: 0,
};
const chipRowStyle: CSSProperties = { display: "flex", flexWrap: "wrap", gap: 6 };
const chipStyle: CSSProperties = {
  padding: "3px 9px",
  border: "1px solid var(--catfish-border)",
  borderRadius: 999,
  background: "transparent",
  color: "var(--catfish-text-muted)",
  cursor: "pointer",
  fontSize: 11,
};
const errStyle: CSSProperties = {
  fontSize: 12,
  color: "var(--catfish-danger, #c0392b)",
  whiteSpace: "pre-wrap",
};
