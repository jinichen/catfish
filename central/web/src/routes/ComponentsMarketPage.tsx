/** /market/components — 组件 · 模型与运行包 (10/3)。
 *
 * 中央 /components/ 静态目录 + manifest.json (10/1 组件分发 P0) 一直只有"下载通道", 门户里
 * 没有一处能看到发布了哪些组件 —— 鸿波问"可供下载安装的模型和模块在哪里看"。这一页只读:
 * 读同源的 /components/manifest.json (nginx 静态托管, 开发机由 vite-components.ts 托管同一路径),
 * 按组件分组列出各平台的包; 应该有却缺的平台标出来 (Mac 有、Windows 没有 = Windows 员工装不了)。
 *
 * 员工不在这里下载: Companion 里对应的功能 (「会议」页 / 🎤) 点安装, 自动下载 + 校验 sha256。
 * 这里的下载链接给断网机器的 IT 手动拷用。上新包不走浏览器 (一个包两个多 G), 见页底说明。
 */
import { useEffect, useState } from "react";

import { Card } from "../components/Card";
import { Badge, DataTable, type Column } from "../components/DataTable";
import { roleAllows } from "../components/RoleGate";
import { useAuthStore } from "../store/auth";

interface ComponentEntry {
  name: string;
  version: string;
  platform: string;
  file: string;
  size: number;
  sha256: string;
}

interface Manifest {
  schema: number;
  generated_at: string;
  components: ComponentEntry[];
}

/** 认识的组件: 给员工看的名字 / 用途 / 应该有哪些平台。不认识的照样列, 只是没说明。 */
const KNOWN: Record<string, { title: string; purpose: string; platforms: string[] }> = {
  "meeting-asr": {
    title: "会议转写组件包",
    purpose: "会议纪要、聊天 🎤 语音输入、上传音频转文字 —— 在员工电脑上离线转写 (FunASR), 录音不出本机",
    platforms: ["mac-arm64", "windows-x64"],
  },
};

const PLATFORM_LABEL: Record<string, string> = {
  "mac-arm64": "Mac (Apple 芯片)",
  "mac-x64": "Mac (Intel)",
  "windows-x64": "Windows x64",
};

export function formatSize(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  return `${Math.max(1, Math.round(bytes / 1e3))} KB`;
}

/** 按组件名分组; 每组里同平台只留最高版本 (manifest 生成时已经去过重, 这里再保险一次) */
export function groupByComponent(entries: ComponentEntry[]): Array<{ name: string; items: ComponentEntry[]; missing: string[] }> {
  const groups = new Map<string, ComponentEntry[]>();
  for (const e of entries) groups.set(e.name, [...(groups.get(e.name) ?? []), e]);
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, items]) => {
      const have = new Set(items.map((i) => i.platform));
      const missing = (KNOWN[name]?.platforms ?? []).filter((p) => !have.has(p));
      return { name, items: [...items].sort((a, b) => a.platform.localeCompare(b.platform)), missing };
    });
}

const COLUMNS: Array<Column<ComponentEntry>> = [
  { header: "平台", width: "20%", cell: (r) => PLATFORM_LABEL[r.platform] ?? r.platform },
  { header: "版本", width: "10%", cell: (r) => r.version },
  { header: "大小", width: "12%", align: "right", cell: (r) => formatSize(r.size) },
  {
    header: "SHA-256",
    truncate: true,
    cell: (r) => (
      <span title={r.sha256} style={{ fontFamily: "var(--font-mono, monospace)", fontSize: 11 }}>{r.sha256}</span>
    ),
  },
  {
    header: "",
    width: 110,
    cell: (r) => (
      // 给断网机器的 IT 手动拷; 员工在 Companion 里装就行
      <a href={`/components/${encodeURIComponent(r.file)}`} download style={{ fontSize: 12 }}>下载安装包</a>
    ),
  },
];

export function ComponentsMarketPage() {
  const me = useAuthStore((s) => s.me);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/components/manifest.json", { cache: "no-store" })
      .then(async (r) => {
        if (r.status === 404) return { schema: 1, generated_at: "", components: [] } as Manifest;
        if (!r.ok) throw new Error(`读取组件清单失败 (HTTP ${r.status})`);
        return (await r.json()) as Manifest;
      })
      .then(setManifest)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  if (error) return <div style={{ color: "var(--status-err)" }}>{error}</div>;
  if (!manifest) return <div>加载中…</div>;

  const groups = groupByComponent(manifest.components);
  const isAdmin = !!me && roleAllows(me.role, "admin");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <Card title={`组件 · 模型与运行包 · ${groups.length} 个组件 / ${manifest.components.length} 个安装包`}>
        <div style={{ color: "var(--text-muted)", fontSize: 13, lineHeight: 1.7 }}>
          体积大、不是人人都用的模型和运行环境不放进 Companion 安装包, 由中央统一发布。
          员工在 Companion 里用到对应功能时点「安装」, 自动下载并校验, 不用到这里下载。
          {manifest.generated_at && <> 清单更新于 {manifest.generated_at.slice(0, 16).replace("T", " ")} (UTC)。</>}
        </div>
      </Card>

      {groups.length === 0 && (
        <Card title="还没有发布任何组件">
          <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
            {isAdmin ? "按下面「怎么发布组件」放好安装包、生成清单。" : "请管理员发布。"}
          </div>
        </Card>
      )}

      {groups.map((g) => {
        const info = KNOWN[g.name];
        return (
          <Card key={g.name} title={`${info?.title ?? g.name}`}>
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
                <Badge tone="neutral">{g.name}</Badge>
                {info && <span style={{ color: "var(--text-muted)" }}>{info.purpose}</span>}
              </div>
              {g.missing.length > 0 && (
                <div style={{ fontSize: 12, color: "var(--status-warn)" }}>
                  ⚠ 缺 {g.missing.map((p) => PLATFORM_LABEL[p] ?? p).join("、")} 的包 —— 这些电脑上的员工装不了
                  {isAdmin ? " (打包方法见页底)" : ", 请联系管理员"}。
                </div>
              )}
              <DataTable columns={COLUMNS} rows={g.items} rowKey={(r) => r.file} />
            </div>
          </Card>
        );
      })}

      {isAdmin && (
        <Card title="怎么发布组件 (管理员)">
          <ol style={{ margin: 0, paddingLeft: 20, fontSize: 13, lineHeight: 1.9 }}>
            <li>
              打安装包: 会议转写组件包 Mac 版在 Apple 芯片 Mac 上跑{" "}
              <code>edge/companion-app/scripts/build-meeting-asr-pack.sh</code>; Windows 版在 GitHub Actions 跑
              「Build meeting-asr pack (Windows)」, 从 Artifacts 下载。
            </li>
            <li>
              把 <code>&lt;名字&gt;-&lt;版本&gt;-&lt;平台&gt;.tar.gz</code> 放进中央的 <code>components/</code> 目录
              (docker 部署挂载在 <code>${"{CATFISH_COMPONENTS_DIR:-./components}"}</code>)。
            </li>
            <li>
              生成清单: <code>python3 delivery/catfish-poc/tools/build_component_manifest.py ./components</code>,
              刷新本页就能看到; 员工的 Companion 下次打开对应功能时就能装。
            </li>
          </ol>
          <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 8 }}>
            安装包一个两个多 G, 不走浏览器上传。同名同平台放了多个版本, 清单只列最高的。
          </div>
        </Card>
      )}
    </div>
  );
}
