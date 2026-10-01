// dev 模式下的 /components/ (10/1, docs/MEETING-MINUTES-PLAN.md §4).
//
// 生产由 nginx 托管 (catfish-locations.conf `location ^~ /components/`)。开发机的
// 中央门户是 `vite --port 5173`, 不经过 nginx —— 没有这个插件的话
// /components/manifest.json 会落到 SPA 回退, 返回 200 + index.html, Companion
// 只会报"manifest 解析失败", 看不出是路径根本不存在。
//
// 行为对齐 nginx: 缺文件 404 (不落 SPA); manifest 不缓存; 支持 Range (断点续传);
// 禁止跳出目录。目录: CATFISH_COMPONENTS_DIR, 默认 ~/.catfish-hub/components
// (跟开发机上 wiki-hub / skills-hub 的 ~/.catfish-hub 并列)。

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { Plugin } from "vite";

const PREFIX = "/components/";

export function componentsDir(): string {
  return (
    process.env.CATFISH_COMPONENTS_DIR ||
    path.join(os.homedir(), ".catfish-hub", "components")
  );
}

export function componentsPlugin(): Plugin {
  return {
    name: "catfish-components",
    configureServer(server) {
      // 直接 use (不 return 函数) = 排在 vite 内置中间件和 SPA 回退之前
      server.middlewares.use((req, res, next) => {
        const url = (req.url || "").split("?")[0];
        if (!url.startsWith(PREFIX)) return next();

        const root = path.resolve(componentsDir());
        let rel: string;
        try {
          rel = decodeURIComponent(url.slice(PREFIX.length));
        } catch {
          res.statusCode = 400;
          return res.end("bad path");
        }
        const file = path.resolve(root, rel);
        if (!rel || !file.startsWith(root + path.sep)) {
          res.statusCode = 404;
          return res.end("not found");
        }
        let stat: fs.Stats;
        try {
          stat = fs.statSync(file);
        } catch {
          res.statusCode = 404;
          return res.end("not found");
        }
        if (!stat.isFile()) {
          res.statusCode = 404;
          return res.end("not found");
        }

        const isManifest = rel === "manifest.json";
        res.setHeader("Content-Type", isManifest ? "application/json" : "application/gzip");
        res.setHeader(
          "Cache-Control",
          isManifest ? "no-cache, no-store, must-revalidate" : "public, max-age=86400",
        );
        res.setHeader("Accept-Ranges", "bytes");

        const range = /^bytes=(\d+)-(\d*)$/.exec(String(req.headers.range || ""));
        if (range) {
          const start = Number(range[1]);
          const end = range[2] ? Math.min(Number(range[2]), stat.size - 1) : stat.size - 1;
          if (start >= stat.size || start > end) {
            res.statusCode = 416;
            res.setHeader("Content-Range", `bytes */${stat.size}`);
            return res.end();
          }
          res.statusCode = 206;
          res.setHeader("Content-Range", `bytes ${start}-${end}/${stat.size}`);
          res.setHeader("Content-Length", String(end - start + 1));
          if (req.method === "HEAD") return res.end();
          return fs.createReadStream(file, { start, end }).pipe(res);
        }
        res.statusCode = 200;
        res.setHeader("Content-Length", String(stat.size));
        if (req.method === "HEAD") return res.end();
        fs.createReadStream(file).pipe(res);
      });
    },
  };
}
