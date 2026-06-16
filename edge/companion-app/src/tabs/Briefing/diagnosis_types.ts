// P3.4.4 (6/15 鸿波): 早安卡数据源诊断类型 — 给 DataDiagnosisCard 跟 AdvisorView 共享.

/** 单个数据源 (邮件 / 日历 / TODO) 的拉取结果. */
export interface SourceStatus {
  /** 调用是否成功 (Promise fulfilled + JSON.parse 出数组). */
  ok: boolean;
  /** 拉到的条目数. 即使 ok=true 也可能 count=0 (e.g. Mail.app 真空, 日历今天没事件). */
  count: number;
  /** ok=false 时的真实失败原因 (Tauri rejected reason / JSON parse error).
   *  原样保留, 不前端瞎加工 — 后端 (calendar.rs:451 那段诊断文案) 才知道真相. */
  reason?: string;
}
