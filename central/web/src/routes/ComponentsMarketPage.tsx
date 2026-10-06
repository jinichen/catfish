/** /market/components — 组件 · 模型与运行包 (10/3)。
 *
 * 中央 /components/ 静态目录 + manifest.json (10/1 组件分发 P0) 一直只有"下载通道", 门户里
 * 没有一处能看到发布了哪些组件 —— 鸿波问"可供下载安装的模型和模块在哪里看"。这一页只读:
 * 读同源的 /components/manifest.json (nginx 静态托管, 开发机由 vite-components.ts 托管同一路径),
 * 按组件分组列出各平台的包; 应该有却缺的平台标出来 (Mac 有、Windows 没有 = Windows 员工装不了)。
 *
 * 员工不在这里下载: Companion 里对应的功能 (「会议」页 / 🎤) 点安装, 自动下载 + 校验 sha256。
 * 这里的下载链接给断网机器的 IT 手动拷用。
 *
 * 10/3 下午: 管理员能在这页上传 (ComponentUploadCard, 分块续传) 和下架; 后端 llm-gateway
 * components_admin.py。手动拷进服务器目录 + 跑脚本的老办法仍然能用, 收在页底。
 */
import { useCallback, useEffect, useState } from "react";

import { Card } from "../components/Card";
import { Badge, BTN, DataTable, type Column } from "../components/DataTable";
import { ConfirmDialog } from "../components/Dialog";
import { roleAllows } from "../components/RoleGate";
import { deleteComponent, type ComponentEntry, type Manifest } from "../lib/components_admin";
import { useAuthStore } from "../store/auth";
import { ComponentUploadCard } from "./ComponentUploadCard";
import { formatSize, platformLabel } from "./componentsFormat";

/** 认识的组件: 给员工看的名字 / 用途 / 应该有哪些平台。不认识的照样列, 只是没说明。 */
const KNOWN: Record<string, { title: string; purpose: string; platforms: string[] }> = {
  "meeting-asr": {
    title: "会议转写组件包",
    purpose: "会议纪要、聊天 🎤 语音输入、上传音频转文字 —— 在员工电脑上离线转写 (FunASR), 录音不出本机",
    platforms: ["mac-arm64", "windows-x64"],
  },
  "embed-model": {
    title: "向量模型包 (BGE-M3)",
    purpose: "知识体系「语义」检索在员工电脑上算向量 (BGE-M3 ONNX INT8 + tokenizer, ~560MB); 不装则只能靠中央网关的向量服务。Apple 芯片 Mac 用 any 包; Windows 用 windows-x64 包 (多带微软 ONNX Runtime DLL); Intel Mac 没有本地运行时, 只能走网关",
    platforms: ["any", "windows-x64"],
  },
};

/** 按组件名分组; 每组里同平台只留最高版本 (manifest 生成时已经去过重, 这里再保险一次) */
export function groupByComponent(entries: ComponentEntry[]): Array<{ name: string; items: ComponentEntry[]; missing: string[] }> {
  const groups = new Map<string, ComponentEntry[]>();
  for (const e of entries) groups.set(e.name, [...(groups.get(e.name) ?? []), e]);
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, items]) => {
      const have = new Set(items.map((i) => i.platform));
      // 有平台无关 (any) 的包就哪个平台都不缺
      const missing = have.has("any") ? [] : (KNOWN[name]?.platforms ?? []).filter((p) => !have.has(p));
      return { name, items: [...items].sort((a, b) => a.platform.localeCompare(b.platform)), missing };
    });
}

function columns(onDelete: ((e: ComponentEntry) => void) | null): Array<Column<ComponentEntry>> {
  const cols: Array<Column<ComponentEntry>> = [
    { header: "平台", width: "18%", cell: (r) => platformLabel(r.platform) },
    { header: "版本", width: "9%", cell: (r) => r.version },
    { header: "大小", width: "10%", align: "right", cell: (r) => formatSize(r.size) },
    {
      header: "SHA-256",
      truncate: true,
      cell: (r) => (
        <span title={r.sha256} style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{r.sha256}</span>
      ),
    },
    {
      header: "",
      width: 90,
      cell: (r) => (
        // 给断网机器的 IT 手动拷; 员工在 Companion 里装就行
        <a href={`/components/${encodeURIComponent(r.file)}`} download style={{ fontSize: 12 }}>下载安装包</a>
      ),
    },
  ];
  if (onDelete) {
    cols.push({ header: "", width: 60, cell: (r) => <button type="button" style={BTN} onClick={() => onDelete(r)}>下架</button> });
  }
  return cols;
}

export function ComponentsMarketPage() {
  const me = useAuthStore((s) => s.me);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<ComponentEntry | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    fetch("/components/manifest.json", { cache: "no-store" })
      .then(async (r) => {
        if (r.status === 404) return { schema: 1, generated_at: "", components: [] } as Manifest;
        if (!r.ok) throw new Error(`读取组件清单失败 (HTTP ${r.status})`);
        return (await r.json()) as Manifest;
      })
      .then(setManifest)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(load, [load]);

  async function confirmDelete() {
    if (!deleting) return;
    setBusy(true);
    try {
      await deleteComponent(deleting.file);
      setDeleting(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

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
            {isAdmin ? "在下面「上传组件包」传一个。" : "请管理员发布。"}
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
                  ⚠ 缺 {g.missing.map(platformLabel).join("、")} 的包 —— 这些电脑上的员工装不了
                  {isAdmin ? " (打包方法见页底, 打好在下面上传)" : ", 请联系管理员"}。
                </div>
              )}
              <DataTable columns={columns(isAdmin ? setDeleting : null)} rows={g.items} rowKey={(r) => r.file} />
            </div>
          </Card>
        );
      })}

      {isAdmin && <ComponentUploadCard onPublished={load} />}

      {isAdmin && (
        <details style={{ fontSize: 13, color: "var(--text-muted)" }}>
          <summary style={{ cursor: "pointer" }}>怎么打安装包 · 不走浏览器怎么发布</summary>
          <ol style={{ margin: "8px 0 0", paddingLeft: 20, lineHeight: 1.9 }}>
            <li>
              会议转写组件包: Mac 版在 Apple 芯片 Mac 上跑 <code>edge/companion-app/scripts/build-meeting-asr-pack.sh</code>;
              Windows 版在 GitHub Actions 跑「Build meeting-asr pack (Windows)」, 从 Artifacts 下载。
            </li>
            <li>
              文件名 <code>&lt;名字&gt;-&lt;x.y.z&gt;-&lt;平台&gt;.tar.gz</code>, 平台 mac-arm64 / mac-x64 / windows-x64 /
              any (跟平台无关的模型)。同名同平台多个版本, 清单只列最高的。
            </li>
            <li>
              不想走浏览器: 拷进服务器的 <code>components/</code> 目录, 再跑
              <code> python3 delivery/catfish-poc/tools/build_component_manifest.py ./components</code>。
            </li>
          </ol>
        </details>
      )}

      {deleting && (
        <ConfirmDialog
          title={`下架 ${deleting.file}?`}
          danger
          busy={busy}
          confirmLabel="下架"
          onConfirm={() => void confirmDelete()}
          onCancel={() => setDeleting(null)}
        >
          文件从服务器删掉, 清单里不再有它; 已经装好的员工不受影响, 还没装的就装不了这个版本了。
          {manifest.components.filter((c) => c.name === deleting.name && c.platform === deleting.platform).length === 1 &&
            " 这是这个平台唯一的版本, 下架后这个平台的员工就装不了这个组件了。"}
        </ConfirmDialog>
      )}
    </div>
  );
}
