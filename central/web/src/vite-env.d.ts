/// <reference types="vite/client" />

/** vite import.meta.env 类型补全 (BL-ARCH2 fix2 5/10).
 *
 * 显式列出 catfish-web 用到的 VITE_ 前缀变量, 不写也能工作 (vite/client 给
 * import.meta.env 字符串索引), 写出来 IDE 会有补全 + 拼错就报错.
 */
interface ImportMetaEnv {
  readonly VITE_OIDC_ISSUER?: string;
  readonly VITE_OIDC_CLIENT_ID?: string;
  readonly VITE_OIDC_SCOPE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
