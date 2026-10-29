// 10/30: 门户顶栏显示中央端版本号 (鸿波「现在在页面上无法确定中央端的版本号」)。
//
// 号由网关 /api/central/version 给 (不用登录): docker 部署读 compose 里的 CATFISH_CENTRAL_VERSION,
// 开发机读源码树的 central/VERSION。六个镜像同一个号, 格式 日期-源码哈希 (scripts/central_version.sh)。
import { useEffect, useState } from "react";

export interface CentralVersion {
  version: string;
  source: "env" | "source-tree" | "none";
}

let cached: Promise<CentralVersion | null> | null = null;

export function fetchCentralVersion(): Promise<CentralVersion | null> {
  cached ??= fetch("/api/central/version", { headers: { Accept: "application/json" } })
    .then((r) => (r.ok ? (r.json() as Promise<CentralVersion>) : null))
    .catch(() => null)
    .then((v) => {
      if (!v) cached = null; // 网关暂时没起来: 下次再试, 不把失败缓存住
      return v;
    });
  return cached;
}

export function useCentralVersion(): CentralVersion | null {
  const [v, setV] = useState<CentralVersion | null>(null);
  useEffect(() => {
    let alive = true;
    void fetchCentralVersion().then((r) => alive && setV(r));
    return () => {
      alive = false;
    };
  }, []);
  return v;
}

export function centralVersionTitle(v: CentralVersion): string {
  if (v.source === "env") return "中央端版本 (镜像版本号, 六个镜像同一个号)";
  if (v.source === "source-tree") return "中央端版本 (开发机: 读的是源码里的 central/VERSION, 可能比代码旧)";
  return "网关没报出版本号";
}
