/**
 * P1-d 接口：4.4 周报、4.5 周会视图、2.7 项目评审卡、2.6 回款管理。
 *
 * 约定：ID 一律 string，金额一律整数分（*_fen），日期一律 YYYY-MM-DD。
 */
import { LONG_TIMEOUT } from './client';
import { del, get, patch, post } from './client';
import type {
  BusinessOverview,
  ListResp,
  PaymentCreateBody,
  PaymentQuery,
  PaymentRecord,
  PaymentSummary,
  PendingReview,
  PendingReviewResp,
  ProjectReview,
  ReviewCreateBody,
  WeeklyCurrentResp,
  WeeklyMeetingResp,
  WeeklyReport,
  WeeklyTeamResp,
} from '@/types';

/* ---------------- 4.4 周报 ---------------- */

/** 我的某周周报（没有则 item=null，提示可生成） */
export function fetchCurrentWeekly(period?: string) {
  return get<WeeklyCurrentResp>('/reports/weekly/current', { period });
}

/** 周报列表（专员后端强制本人；管理人员可传 user_id） */
export function listWeekly(params?: {
  period?: string;
  user_id?: string;
  page?: number;
  page_size?: number;
}) {
  return get<ListResp<WeeklyReport>>('/reports/weekly', params);
}

/** 团队周报汇总（管理人员） */
export function fetchTeamWeekly(period?: string) {
  return get<WeeklyTeamResp>('/reports/weekly/team', { period });
}

/**
 * 只汇总指标（宿主能力，不依赖模型）：AI 不可用也能出数据表。
 * 正文保持原样，`generated_by` 为 `pending` 时表示"还没生成正文"。
 */
export function collectWeekly(body: { period?: string; user_id?: string }) {
  return post<{ item: WeeklyReport }>('/reports/weekly/collect', body);
}

/**
 * AI 生成正文（模型能力）：AI 不可用会 503。
 *  人工写过的正文默认不覆盖，需要 `force: true` 显式确认。
 */
export function generateWeeklyBody(reportId: string, body?: { force?: boolean }) {
  return post<{ item: WeeklyReport }>(`/reports/weekly/${reportId}/generate-body`, body ?? {}, {
    timeout: LONG_TIMEOUT,
  });
}

/**
 * 组合操作：先汇总指标，再尽力生成 AI 正文（模型慢，用长超时）。
 * `ai_body_error` 非空表示正文没生成出来（指标照常返回，正文留空）——需在界面上明说。
 */
export function generateWeekly(body: { period?: string; user_id?: string; use_ai?: boolean }) {
  return post<{ item: WeeklyReport; ai_body_error: string | null }>(
    '/reports/weekly/generate',
    body,
    { timeout: LONG_TIMEOUT },
  );
}

/** 编辑周报（正文 / 问题 / 下周计划） */
export function updateWeekly(
  reportId: string,
  body: { body?: string; problems?: string; next_plan?: string },
) {
  return patch<{ item: WeeklyReport }>(`/reports/weekly/${reportId}`, body);
}

/** 提交周报 */
export function submitWeekly(reportId: string) {
  return post<{ item: WeeklyReport }>(`/reports/weekly/${reportId}/submit`);
}

/* ---------------- 4.1 经营大盘 ---------------- */

/**
 * 经营大盘（仅 admin / executive）。
 * 口径与各明细页一致：后端复用同一批服务函数，不重写 SQL。
 */
export function fetchOverview(days = 30) {
  return get<BusinessOverview>('/reports/overview', { days });
}

/* ---------------- 4.5 周会视图 ---------------- */

/** 周会视图：团队概览 + 逐人项目节奏 + 待评审（管理人员） */
export function fetchWeeklyMeeting(params?: { start?: string; end?: string }) {
  return get<WeeklyMeetingResp>('/dashboard/weekly-meeting', params);
}

/* ---------------- 2.7 项目评审卡 ---------------- */

/** 待评审清单（管理人员） */
export function fetchPendingReviews() {
  return get<PendingReviewResp>('/reviews/pending');
}

/** 评审记录列表 */
export function listReviews(params: {
  lead_id?: string;
  reviewer_id?: string;
  verdict?: string;
  page?: number;
  page_size?: number;
}) {
  return get<ListResp<ProjectReview>>('/reviews', params);
}

/** 单条评审卡详情 */
export function getReview(reviewId: string) {
  return get<ProjectReview>(`/reviews/${reviewId}`);
}

/** 填写评审卡（5 看 + 真执行判定） */
export function createReview(body: ReviewCreateBody) {
  return post<ProjectReview>('/reviews', body);
}

/** 供页面复用的待评审项类型 */
export type { PendingReview };

/* ---------------- 2.6 回款 ---------------- */

/** 回款记录列表 */
export function listPayments(params: PaymentQuery) {
  return get<ListResp<PaymentRecord>>('/payments', params);
}

/** 回款总览（合同额 / 已回款 / 未回款 / 未回款清单） */
export function fetchPaymentSummary(params?: {
  start?: string;
  end?: string;
  owner_id?: string;
}) {
  return get<PaymentSummary>('/payments/summary', params);
}

/** 登记回款 */
export function createPayment(body: PaymentCreateBody) {
  return post<PaymentRecord>('/payments', body);
}

/** 修改回款记录 */
export function updatePayment(
  paymentId: string,
  body: Partial<Omit<PaymentCreateBody, 'lead_id'>>,
) {
  return patch<PaymentRecord>(`/payments/${paymentId}`, body);
}

/** 删除回款记录（前端需二次确认） */
export function deletePayment(paymentId: string) {
  return del<{ ok: boolean }>(`/payments/${paymentId}`);
}
