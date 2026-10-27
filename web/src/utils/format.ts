import dayjs from 'dayjs';

/* ==================== 金额：后端一律整数分 ==================== */

/** 分 → 元字符串（保留两位小数），空值显示 '-' */
export function fenToYuan(fen?: number | null, dash = '-'): string {
  if (fen === null || fen === undefined || Number.isNaN(fen)) return dash;
  return (fen / 100).toFixed(2);
}

/** 分 → 元数字，便于 antd InputNumber 回填 */
export function fenToYuanNumber(fen?: number | null): number | undefined {
  if (fen === null || fen === undefined || Number.isNaN(fen)) return undefined;
  return Number((fen / 100).toFixed(2));
}

/** 元 → 分（提交时使用，四舍五入取整） */
export function yuanToFen(yuan?: number | string | null): number | undefined {
  if (yuan === null || yuan === undefined || yuan === '') return undefined;
  const n = typeof yuan === 'string' ? Number(yuan) : yuan;
  if (Number.isNaN(n)) return undefined;
  return Math.round(n * 100);
}

/** 分 → 万元展示（看板金额用） */
export function fenToWan(fen?: number | null): string {
  if (fen === null || fen === undefined || Number.isNaN(fen)) return '-';
  return (fen / 1000000).toFixed(2);
}

/* ==================== 时间 ==================== */

/** 时间格式化 */
export function fmtDate(v?: string | null, pattern = 'YYYY-MM-DD'): string {
  if (!v) return '-';
  const d = dayjs(v);
  return d.isValid() ? d.format(pattern) : '-';
}

export function fmtDateTime(v?: string | null): string {
  return fmtDate(v, 'YYYY-MM-DD HH:mm');
}

/** 小时数 → 友好时长 */
export function fmtHours(hours?: number | null): string {
  if (hours === null || hours === undefined || Number.isNaN(hours)) return '-';
  if (hours < 1) return `${Math.round(hours * 60)} 分钟`;
  if (hours < 48) return `${hours.toFixed(1)} 小时`;
  return `${(hours / 24).toFixed(1)} 天`;
}

/* ==================== 超时倒计时 ==================== */

export interface CountdownResult {
  text: string;
  /** overdue=已超时，warning=即将超时（<6h），normal=正常 */
  level: 'overdue' | 'warning' | 'normal' | 'none';
}

/**
 * 基于**后端下发的截止时刻**做倒计时（权威口径，截止时刻已按工作日 + 法定节假日算好）。
 * 优先用这个；只有后端没给截止时刻时，才退回下面的本地估算函数。
 */
export function countdownToDeadline(deadline?: string | null): CountdownResult {
  if (!deadline) return { text: '-', level: 'none' };
  const diffMin = dayjs(deadline).diff(dayjs(), 'minute');
  const abs = Math.abs(diffMin);
  const h = Math.floor(abs / 60);
  const m = abs % 60;
  const text = `${diffMin < 0 ? '已超时' : '剩余'} ${h}h${m}m`;
  if (diffMin <= 0) return { text, level: 'overdue' };
  return { text, level: diffMin <= 360 ? 'warning' : 'normal' };
}

/**
 * 首次跟进时限倒计时：以指派时间为起点，24 小时为限（S1-5）
 * 已跟进或未指派则不显示倒计时
 */
export function firstFollowCountdown(
  assignAt?: string | null,
  firstFollowAt?: string | null,
  limitHours = 24,
): CountdownResult {
  if (!assignAt || firstFollowAt) return { text: '-', level: 'none' };
  const deadline = dayjs(assignAt).add(limitHours, 'hour');
  const diffMin = deadline.diff(dayjs(), 'minute');
  if (diffMin <= 0) {
    const over = Math.abs(diffMin);
    const h = Math.floor(over / 60);
    const m = over % 60;
    return { text: `已超时 ${h}h${m}m`, level: 'overdue' };
  }
  const h = Math.floor(diffMin / 60);
  const m = diffMin % 60;
  return {
    text: `剩余 ${h}h${m}m`,
    level: diffMin <= 360 ? 'warning' : 'normal',
  };
}

/**
 * 7 个工作日未跟进回收倒计时：以最近一次跟进（无则指派）为起点估算
 */
export function recycleCountdown(
  assignAt?: string | null,
  lastFollowAt?: string | null,
  workdays = 7,
): CountdownResult {
  const start = lastFollowAt || assignAt;
  if (!start) return { text: '-', level: 'none' };
  // 按自然日估算：7 个工作日 ≈ 9 个自然日
  const deadline = dayjs(start).add(Math.round(workdays * 1.4), 'day');
  const diffDay = deadline.diff(dayjs(), 'day');
  if (diffDay < 0) return { text: `已超回收期 ${-diffDay} 天`, level: 'overdue' };
  if (diffDay <= 2) return { text: `剩 ${diffDay} 天回收`, level: 'warning' };
  return { text: `剩 ${diffDay} 天`, level: 'normal' };
}

/* ==================== Tag 颜色映射 ==================== */

/** 线索状态颜色（lost = 已丢单，灰色以示终止） */
export const STATUS_COLOR: Record<string, string> = {
  unassigned: 'default',
  assigned: 'blue',
  following: 'processing',
  valid: 'success',
  invalid: 'error',
  lost: 'default',
  signed: 'gold',
};

/** 状态兜底文案（后端字典缺失时使用） */
export const STATUS_LABEL: Record<string, string> = {
  unassigned: '未指派',
  assigned: '已指派',
  following: '跟进中',
  valid: '有效商机',
  invalid: '无效商机',
  lost: '已丢单',
  signed: '已签约',
};

/** 预警种类颜色（四类系统预警 + R1–R5 业务风险） */
export const ALERT_KIND_COLOR: Record<string, string> = {
  business_risk: 'volcano',
  stuck: 'red',
  total_duration: 'orange',
  /** 项目节奏不清（主口径：缺时间承诺） */
  rhythm_unclear: 'magenta',
  /** 年度节奏窗口参照（长期未填时间时的额外参照） */
  rhythm_window: 'purple',
  conversion: 'gold',
};

/** 预警处理状态颜色 */
export const RISK_STATUS_COLOR: Record<string, string> = {
  pending: 'warning',
  processing: 'processing',
  resolved: 'success',
};

/** 评审真执行判定颜色（能力=红 / 态度=橙 / 正常=绿） */
export const VERDICT_COLOR: Record<string, string> = {
  ability: 'red',
  attitude: 'orange',
  normal: 'green',
};

/** 评审卡 5 看单项结论颜色 */
export const REVIEW_RESULT_COLOR: Record<string, string> = {
  pass: 'green',
  risk: 'orange',
  fail: 'red',
};

/** 周报正文生成方式颜色 */
export const GENERATED_BY_COLOR: Record<string, string> = {
  pending: 'orange',
  ai: 'geekblue',
  rule: 'default',
  manual: 'green',
};

/** 周报正文生成方式文案 */
export const GENERATED_BY_LABEL: Record<string, string> = {
  pending: '待生成正文',
  ai: 'Agently 生成',
  rule: '历史规则生成',
  manual: '人工编辑',
};

/** 来源颜色（8 类来源循环取色） */
const SOURCE_COLORS = ['blue', 'cyan', 'geekblue', 'purple', 'magenta', 'orange', 'green', 'volcano'];

export function sourceColor(source?: string | null): string {
  if (!source) return 'default';
  let hash = 0;
  for (let i = 0; i < source.length; i += 1) hash += source.charCodeAt(i);
  return SOURCE_COLORS[hash % SOURCE_COLORS.length];
}

/** 客户类型颜色 */
export const ORG_TYPE_COLOR: Record<string, string> = {
  bureau: 'purple',
  public_school: 'blue',
  private_school: 'cyan',
  vocational: 'geekblue',
  higher_vocational: 'magenta',
  university: 'volcano',
};

export function orgTypeColor(v?: string | null): string {
  return (v && ORG_TYPE_COLOR[v]) || 'blue';
}

/** 提醒类型颜色（5 类 + 待确认） */
export const REMINDER_COLOR: Record<string, string> = {
  first_follow_due: 'orange',
  first_follow_overdue: 'red',
  recycle_due: 'gold',
  recycled: 'default',
  next_follow_due: 'blue',
  pending_confirm: 'purple',
};

/** 风险类型颜色 */
export const RISK_COLOR: Record<string, string> = {
  r1_budget_unclear: 'orange',
  r2_trust_missing: 'red',
  r3_decision_chain: 'volcano',
  r4_demand_unclear: 'gold',
  r5_project_rhythm: 'purple',
};

/** 超时倒计时颜色 */
export const COUNTDOWN_COLOR: Record<CountdownResult['level'], string> = {
  overdue: 'red',
  warning: 'orange',
  normal: 'green',
  none: 'default',
};

/* ==================== 字典转换 ==================== */

export interface Option {
  value: string;
  label: string;
}

/** 字典数组 → value→label 映射 */
export function toLabelMap(list?: Option[] | null): Record<string, string> {
  const map: Record<string, string> = {};
  (list || []).forEach((i) => {
    map[i.value] = i.label;
  });
  return map;
}

/** 取字典标签，缺失时回退为原值或备用映射 */
export function dictLabel(
  list: Option[] | undefined,
  value?: string | null,
  fallback?: Record<string, string>,
): string {
  if (!value) return '-';
  const hit = list?.find((i) => i.value === value);
  if (hit) return hit.label;
  return fallback?.[value] ?? value;
}

/** 金额千分位（元） */
export function formatYuan(fen?: number | null): string {
  const v = fenToYuanNumber(fen);
  if (v === undefined) return '-';
  return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** 百分比（0-1 小数 → 百分数） */
export function fmtPercent(v?: number | null, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '-';
  return `${(v * 100).toFixed(digits)}%`;
}

/**
 * 活跃率等「比率」字段的宽容展示：
 * 后端可能下发 0-1 小数（0.45），也可能下发 0-100 的百分数（45），两种都能正确显示。
 */
export function fmtRate(v?: number | null, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '-';
  return `${(Math.abs(v) <= 1 ? v * 100 : v).toFixed(digits)}%`;
}

/** 宽容比率 → 0-100 的数值（供 Progress / SimpleBar 使用） */
export function rateToPercent(v?: number | null): number {
  if (v === null || v === undefined || Number.isNaN(v)) return 0;
  const p = Math.abs(v) <= 1 ? v * 100 : v;
  return Math.max(0, Math.min(100, Number(p.toFixed(1))));
}
