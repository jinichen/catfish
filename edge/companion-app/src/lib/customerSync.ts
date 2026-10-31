/** 客户专属 SOUL 跟中央走 (10/31)。
 *
 * 原来客户端没设 env 就默认 FFCS, 每家客户的员工都加载了中电福富的内网约定。现在每套部署在
 * 中央 .env 写 CATFISH_CUSTOMER, 通过 /api/me 的 customer 告诉 Companion; Companion 记下来,
 * 按它加载 (或不加载) SOUL_<客户>.md。
 */
import { fetchMe } from "./me";
import { identitySetCustomer } from "./tauri_compliance";

/** 取一次中央的 customer 并同步给 Rust 端。中央太老 (没这个字段) 就不动, 保持本机原样。 */
export async function syncCustomerFromCentral(
  deps: { fetchMe: typeof fetchMe; setCustomer: typeof identitySetCustomer } = {
    fetchMe,
    setCustomer: identitySetCustomer,
  },
): Promise<boolean> {
  const me = await deps.fetchMe();
  if (typeof me.customer !== "string") return false;
  return deps.setCustomer(me.customer);
}

/** 启动后过一会儿同步一次 (等登录 / 网络就绪), 之后每 6 小时再看一眼。返回停止函数。 */
export function startCustomerSync(): () => void {
  const tick = () => {
    syncCustomerFromCentral().catch((e) => console.warn("[customer] 从中央取客户名失败 (下次再试):", e));
  };
  const first = setTimeout(tick, 20_000);
  const timer = setInterval(tick, 6 * 3600_000);
  return () => {
    clearTimeout(first);
    clearInterval(timer);
  };
}
