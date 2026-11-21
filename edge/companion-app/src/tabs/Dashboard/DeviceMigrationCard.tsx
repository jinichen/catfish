/** 设置 → 隐私 / 本机数据 → 换机迁移 (BL-L9, docs/DEVICE-MIGRATION-DESIGN.md)。
 *
 * 上半: 旧电脑「导出」—— 一句话说带什么、一个「连同文件」勾选、一句话说不带什么, 设口令,
 * 存成 .catfishmove 文件。逐项清单不给员工看 (10/31 鸿波: 列太细反而乱); 有没有漏登记的
 * 数据文件由 CI 的 check_migration_catalog.py 把关, 不靠员工看警告。
 * 下半: 新电脑「导入」(DeviceMigrationImport, 跟首次启动引导共用)。
 * 11/21: 上半多一种「直传到新电脑」(扫码, DeviceMigrationDirectSend) —— 迁移包用新电脑的一次性公钥加密,
 * 经中央边传边删 (中央只见密文)。「导出成文件」照旧不经过中央。账号和授权刻意不带, 新电脑重新登录。
 */
import * as React from "react";

import DeviceMigrationDirectSend from "../../components/DeviceMigrationDirectSend";
import DeviceMigrationImport, {
  inputStyle,
  plainBtn,
  primaryBtn,
} from "../../components/DeviceMigrationImport";
import {
  directReceiveStatus,
  directSendStatus,
  entryKey,
  formatBytes,
  migrationExport,
  migrationPickSave,
  migrationScan,
  passphraseHint,
  type MigrationExportReport,
  type MigrationScan,
} from "../../lib/deviceMigration";
import { revealInFinder } from "../../lib/tauri";
import { fileManagerName } from "../../lib/platformLabels";

const muted: React.CSSProperties = {
  fontSize: 11,
  color: "var(--catfish-text-muted)",
};
const h4: React.CSSProperties = { margin: "14px 0 6px", fontSize: 13 };

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;
}

const tabBtn = (on: boolean): React.CSSProperties => ({
  ...plainBtn,
  background: on ? "var(--catfish-cyan)" : "transparent",
  color: on ? "white" : "var(--catfish-text)",
  borderColor: on ? "var(--catfish-cyan)" : "var(--catfish-border)",
});

type Role = "" | "old" | "new";

/** 「这台是哪台」的大按钮 */
function RoleCard({ icon, title, sub, onClick }: { icon: string; title: string; sub: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        flex: "1 1 240px",
        textAlign: "left",
        padding: "12px 14px",
        border: "1px solid var(--catfish-border)",
        borderRadius: 8,
        background: "var(--catfish-bg)",
        color: "var(--catfish-text)",
        cursor: "pointer",
        lineHeight: 1.6,
      }}
    >
      <div style={{ fontSize: 14, fontWeight: 600 }}>
        {icon} {title}
      </div>
      <div style={muted}>{sub}</div>
    </button>
  );
}

/** 11/21 鸿波「这个UI是不是应该优化一下」: 原来旧电脑 / 新电脑两半同时摆出来, 员工得先读完才知道自己该看哪半;
 *  直传还要等「清点本机数据」才出来。改成先问这台是哪台, 只给那一半; 直传不等清点 (只有「连同文件」要用清点结果)。 */
export default function DeviceMigrationCard() {
  const [role, setRole] = React.useState<Role>("");
  const [scan, setScan] = React.useState<MigrationScan | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [mode, setMode] = React.useState<"direct" | "file">("direct");

  React.useEffect(() => {
    migrationScan()
      .then(setScan)
      .catch((e) => setError(String(e)));
    // 正在传 / 正在收的, 回到这页直接进那一半
    directSendStatus()
      .then((s) => s && ["packing", "sending", "finishing"].includes(s.state) && setRole("old"))
      .catch(() => {});
    directReceiveStatus()
      .then((s) => s && ["waiting", "receiving", "verifying", "ready"].includes(s.state) && setRole("new"))
      .catch(() => {});
  }, []);

  const files = scan?.entries.filter((e) => e.class === "optional" && e.bytes > 0) ?? [];
  const filesBytes = files.reduce((n, e) => n + e.bytes, 0);
  const back = (
    <button type="button" style={{ ...plainBtn, padding: "2px 10px", fontSize: 11 }} onClick={() => setRole("")}>
      ← 不是这台
    </button>
  );

  return (
    <div style={{ padding: 12, fontSize: 12 }}>
      <h3 style={{ margin: 0, fontSize: 15 }}>🧳 换机迁移</h3>
      <div style={{ ...muted, marginTop: 4, lineHeight: 1.6 }}>
        换电脑 / 重装系统时, 把小鲶对你的积累 (工作日志、记忆、知识库、对话、技能、待办) 整个搬过去。账号和授权不搬, 新电脑重新登录。
      </div>

      {role === "" && (
        <>
          <h4 style={h4}>这台电脑是…</h4>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <RoleCard icon="📤" title="旧电脑" sub="把这台上的资料搬到新电脑" onClick={() => setRole("old")} />
            <RoleCard icon="📥" title="新电脑" sub="把旧电脑上的资料搬到这台" onClick={() => setRole("new")} />
          </div>
        </>
      )}

      {role === "old" && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "14px 0 8px" }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>📤 这台是旧电脑: 把资料搬出去</span>
            {back}
          </div>
          <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
            <button type="button" style={tabBtn(mode === "direct")} onClick={() => setMode("direct")}>
              直传到新电脑 (两台都开着, 推荐)
            </button>
            <button type="button" style={tabBtn(mode === "file")} onClick={() => setMode("file")}>
              导出成文件 (新电脑不在手边)
            </button>
          </div>
          {mode === "direct" && <DeviceMigrationDirectSend files={files.map(entryKey)} filesBytes={filesBytes} />}
          {mode === "file" && !scan && !error && <div style={muted}>正在清点本机数据…</div>}
          {mode === "file" && scan && <FileExport scan={scan} files={files.map(entryKey)} filesBytes={filesBytes} />}
          {mode === "file" && error && !scan && <div style={errStyle}>🚫 {error}</div>}
        </>
      )}

      {role === "new" && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "14px 0 8px" }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>📥 这台是新电脑: 把资料搬进来</span>
            {back}
          </div>
          <DeviceMigrationImport />
        </>
      )}
    </div>
  );
}

const errStyle: React.CSSProperties = {
  marginTop: 10,
  padding: "6px 10px",
  background: "rgba(220,38,38,0.05)",
  border: "1px solid rgba(220,38,38,0.3)",
  borderRadius: 4,
  color: "var(--status-err)",
};

/** 导出成口令加密的 .catfishmove 文件 (不经过中央, 员工自己带到新电脑) */
function FileExport({
  scan,
  files,
  filesBytes,
}: {
  scan: MigrationScan;
  files: string[];
  filesBytes: number;
}) {
  const [withFiles, setWithFiles] = React.useState(false);
  const [pass, setPass] = React.useState("");
  const [pass2, setPass2] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [result, setResult] = React.useState<MigrationExportReport | null>(
    null,
  );
  const hint = passphraseHint(pass);
  const mismatch = pass2.length > 0 && pass !== pass2;

  const run = async () => {
    setError(null);
    setResult(null);
    const dest = await migrationPickSave(
      `鲶鱼迁移包-${today()}.catfishmove`,
    ).catch(() => null);
    if (!dest) return;
    setBusy(true);
    try {
      setResult(await migrationExport(dest, pass, withFiles ? files : []));
      setPass("");
      setPass2("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div style={{ ...muted, lineHeight: 1.6 }}>
        迁移包用你设的口令加密, 不经过公司服务器, 你自己用 U
        盘、隔空投送或网盘带到新电脑。
      </div>
      <div style={{ lineHeight: 1.7 }}>
        <div>
          会带上:
          工作日志、记忆和画像、个人知识库、技能、早安待办、对话历史、设置等 (约{" "}
          {formatBytes(scan.requiredBytes)}, 打包时会压缩)。
        </div>
        {filesBytes > 0 && (
          <label style={{ display: "block" }}>
            <input
              type="checkbox"
              checked={withFiles}
              disabled={busy}
              onChange={(e) => setWithFiles(e.target.checked)}
            />{" "}
            连同文件一起带: 上传的附件、生成的文件等 ({formatBytes(filesBytes)})
          </label>
        )}
        <div style={muted}>
          不会带: 账号和授权 (到新电脑重新登录), 搜索索引和本地模型
          (新电脑自己重建)。
        </div>
        <div
          style={{
            display: "flex",
            gap: 8,
            alignItems: "center",
            marginTop: 8,
            flexWrap: "wrap",
          }}
        >
          <input
            type="password"
            placeholder="设一个迁移口令 (至少 8 位)"
            value={pass}
            onChange={(e) => setPass(e.target.value)}
            style={inputStyle}
            disabled={busy}
          />
          <input
            type="password"
            placeholder="再输一遍"
            value={pass2}
            onChange={(e) => setPass2(e.target.value)}
            style={inputStyle}
            disabled={busy}
          />
        </div>
        {(hint || mismatch) && (
          <div style={{ ...muted, color: "var(--catfish-orange)" }}>
            {mismatch ? "两次输入不一样" : hint}
          </div>
        )}
        <div style={muted}>
          口令丢了包就打不开, 公司和我们都帮不了。请记好, 到新电脑上导入时要用。
        </div>
        <div style={{ marginTop: 8 }}>
          <button
            type="button"
            style={primaryBtn(busy || pass.length < 8 || pass !== pass2)}
            onClick={() => void run()}
            disabled={busy || pass.length < 8 || pass !== pass2}
          >
            {busy
              ? `导出中… ${withFiles ? "(带了文件, 要等一会儿)" : ""}`
              : "选择保存位置并导出"}
          </button>
        </div>
      </div>
      {result && (
        <div
          style={{
            marginTop: 10,
            padding: "8px 12px",
            background: "rgba(22,163,74,0.05)",
            border: "1px solid rgba(22,163,74,0.3)",
            borderRadius: 4,
            lineHeight: 1.7,
          }}
        >
          <div style={{ fontWeight: 600, color: "#16A34A" }}>
            ✓ 已导出 {result.files} 个文件 ({formatBytes(result.bytes)})
          </div>
          <code style={{ fontSize: 11 }}>{result.path}</code>{" "}
          <button
            type="button"
            style={plainBtn}
            onClick={() => void revealInFinder(result.path).catch(() => {})}
          >
            在{fileManagerName()}中显示
          </button>
          <div style={muted}>
            这台电脑上的数据没有删除。建议在新电脑导入成功后,
            在这台电脑上退出登录。
          </div>
        </div>
      )}
      {error && !result && <div style={errStyle}>🚫 {error}</div>}
    </div>
  );
}
