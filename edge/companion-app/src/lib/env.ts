/** Dev / Prod 模式判断 + 环境配置。 */

export const isDev = import.meta.env.DEV;
export const isProd = import.meta.env.PROD;

export const config = {
  gatewayUrl: "http://127.0.0.1:8999",
  pollIntervalMs: 3000,
};
