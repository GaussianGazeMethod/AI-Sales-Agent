import dayjs from 'dayjs';

/**
 * 闭环三（P2）交付域的展示常量与兜底工具。
 * 约定：后端下发 `*_label` 时一律以后端为准，这里只做字典缺失时的兜底。
 */

/* ==================== 10 阶段 ==================== */

/** 10 阶段顺序（一 项目管理 贯穿全程，其余顺序执行） */
export const DELIVERY_PHASE_ORDER = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

/** 阶段名称兜底（与后端 `delivery_phases` 字典文案保持一致） */
export const DELIVERY_PHASE_LABEL: Record<number, string> = {
  1: '一 项目管理',
  2: '二 项目启动',
  3: '三 项目交付',
  4: '四 场景应用培训',
  5: '五 服务支持',
  6: '六 主动运营',
  7: '七 建立标杆',
  8: '八 项目验收',
  9: '九 项目增购',
  10: '十 续费',
};

/** 阶段中文名（phase_label 优先，其次本地兜底） */
export function phaseLabel(phase?: number | string | null, label?: string | null): string {
  if (label) return label;
  const n = Number(phase);
  return DELIVERY_PHASE_LABEL[n] ?? (phase === null || phase === undefined ? '-' : String(phase));
}

/* ==================== 合同 / 交接 / 续费 状态兜底 ==================== */

export const CONTRACT_STATUS_LABEL: Record<string, string> = {
  executing: '执行中',
  accepted: '已验收',
  renewed: '已续费',
  terminated: '已终止',
};

export const CONTRACT_STATUS_COLOR: Record<string, string> = {
  executing: 'processing',
  accepted: 'success',
  renewed: 'gold',
  terminated: 'default',
};

export const HANDOVER_STATUS_LABEL: Record<string, string> = {
  none: '未交接',
  pending: '待双方确认',
  sales_confirmed: '销售已确认',
  delivery_confirmed: '交付已确认',
  confirmed: '双方已确认',
};

export const HANDOVER_STATUS_COLOR: Record<string, string> = {
  none: 'default',
  pending: 'warning',
  sales_confirmed: 'processing',
  delivery_confirmed: 'processing',
  confirmed: 'success',
};

export const RENEW_STATUS_LABEL: Record<string, string> = {
  none: '未启动',
  pending: '待启动',
  evaluating: '评估中',
  in_progress: '推进中',
  following: '跟进中',
  renewed: '已续费',
  lost: '未续费',
};

/** 运营动作类型兜底（字典 operation_action_types 优先） */
export const OPERATION_ACTION_LABEL: Record<string, string> = {
  visit: '定期回访',
  training: '使用培训',
  report: '数据报告',
  activity: '活动运营',
  requirement: '需求收集',
  complaint_follow: '投诉跟进',
};

export const RECORD_STATUS_LABEL: Record<string, string> = {
  done: '已完成',
  pending: '待跟进',
  following: '跟进中',
};

/* ==================== 剩余天数 / 停留天数 ==================== */

/** 剩余天数档位：<15 天红、<90 天橙、其余默认（需求：<90 标橙、<15 工作日标红） */
export function expireLevel(days?: number | null): 'overdue' | 'red' | 'orange' | 'normal' | 'none' {
  if (days === null || days === undefined || Number.isNaN(days)) return 'none';
  if (days < 0) return 'overdue';
  if (days < 15) return 'red';
  if (days < 90) return 'orange';
  return 'normal';
}

export const EXPIRE_COLOR: Record<string, string> = {
  overdue: 'red',
  red: 'red',
  orange: 'orange',
  normal: 'default',
  none: 'default',
};

export const EXPIRE_TEXT: Record<string, string> = {
  overdue: '已到期',
  red: '临近到期',
  orange: '即将到期',
  normal: '正常',
  none: '-',
};

/**
 * 阶段停留文案。
 *  业务口径：`base_days = 0` 表示**未设停留时长基准**，此时不显示超期，只提示「基准未设」。
 */
export function stayText(stay?: number | null, base?: number | null): string {
  const s = stay ?? 0;
  if (!base) return `停留 ${s} 天 / 基准未设`;
  return `停留 ${s} 天 / 基准 ${base} 天`;
}

/** 基准文案（0 → 基准未设） */
export function baseText(base?: number | null): string {
  return base ? `基准 ${base} 天` : '基准未设';
}

/** 是否真的超期：必须「已设基准」且后端 overdue 为真 */
export function isOverdue(overdue?: boolean | null, base?: number | null): boolean {
  return !!overdue && !!base;
}

/** 距今天数（未来为正） */
export function daysFromNow(date?: string | null): number | null {
  if (!date) return null;
  const d = dayjs(date);
  if (!d.isValid()) return null;
  return d.startOf('day').diff(dayjs().startOf('day'), 'day');
}

/** 往前推 N 个自然日（用于「续费：到期前 90 天」等固定天数口径） */
export function minusDays(date?: string | null, days = 0): string | null {
  if (!date) return null;
  const d = dayjs(date);
  return d.isValid() ? d.subtract(days, 'day').format('YYYY-MM-DD') : null;
}

/**
 * 往前推 N 个工作日（只跳过周六周日，法定节假日以后端计算为准）。
 * 用于「验收：到期前 15 个工作日」「续费：到期前 90 天」的提醒时间展示。
 */
export function minusWorkdays(date?: string | null, workdays = 0): string | null {
  if (!date) return null;
  const d = dayjs(date);
  if (!d.isValid()) return null;
  let cursor = d.startOf('day');
  let left = workdays;
  while (left > 0) {
    cursor = cursor.subtract(1, 'day');
    const w = cursor.day();
    if (w !== 0 && w !== 6) left -= 1;
  }
  return cursor.format('YYYY-MM-DD');
}
