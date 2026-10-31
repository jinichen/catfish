import { describe, expect, it, vi } from "vitest";

import { syncCustomerFromCentral } from "./customerSync";
import type { MeInfo } from "./me";

const me = (extra: Partial<MeInfo>): MeInfo => ({
  email: "a@x.cn",
  department: "d",
  role: "employee",
  managed_departments: [],
  auth_method: "oidc",
  ...extra,
});

describe("syncCustomerFromCentral", () => {
  it("中央给了客户名 (含空串) 就同步; 老中央没这个字段就不动", async () => {
    const setCustomer = vi.fn(async () => true);
    await syncCustomerFromCentral({ fetchMe: async () => me({ customer: "ffcs" }), setCustomer });
    expect(setCustomer).toHaveBeenLastCalledWith("ffcs");
    await syncCustomerFromCentral({ fetchMe: async () => me({ customer: "" }), setCustomer });
    expect(setCustomer).toHaveBeenLastCalledWith("");
    setCustomer.mockClear();
    expect(await syncCustomerFromCentral({ fetchMe: async () => me({}), setCustomer })).toBe(false);
    expect(setCustomer).not.toHaveBeenCalled();
  });
});
