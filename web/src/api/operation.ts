/**
 * P2-b 接口：闭环三「运营与客户成功」
 * 覆盖：3.6 服务支持（FAQ + 需求登记）、3.7 产品问题与承诺台账、3.8 投诉管理、
 *      3.9 运营指标（活跃率）、3.10 回访记录、3.11 验收管理、3.12 续费与增购、4.6 运营复盘。
 *
 * 约定：ID 一律 string，金额一律整数分（*_fen），日期一律 YYYY-MM-DD。
 */
import { client, del, get, patch, post } from './client';
import type {
  AcceptanceDueItem,
  AcceptanceFailBody,
  AcceptanceOut,
  AcceptancePassBody,
  AcceptanceQuery,
  AcceptanceUpdateBody,
  ComplaintCloseBody,
  ComplaintCreateBody,
  ComplaintOut,
  ComplaintQuery,
  ComplaintStats,
  ComplaintUpdateBody,
  FaqCreateBody,
  FaqOut,
  FaqUpdateBody,
  IssueAssignBody,
  IssueCreateBody,
  IssueQuery,
  IssueReplyBody,
  IssueResolveBody,
  IssueStats,
  IssueUpdateBody,
  ListResp,
  MetricImportResult,
  OperationMetric,
  OperationMetricCreateBody,
  OperationMetricDashboard,
  OperationMetricQuery,
  OperationMetricUpdateBody,
  OperationRecordCreateBody,
  OperationRecordOut,
  OperationRecordQuery,
  OperationRecordUpdateBody,
  OperationReviewResp,
  ProductIssueOut,
  RenewalConcludeBody,
  RenewalCreateBody,
  RenewalDueItem,
  RenewalOut,
  RenewalQuery,
  RenewalStats,
  RenewalUpdateBody,
} from '@/types';

/* ==================== 3.6 服务支持：FAQ ==================== */

/** FAQ 列表（按浏览量倒序，最多 100 条；按分类 / 关键词过滤） */
export function listFaqs(params?: { category?: string; keyword?: string }) {
  return get<ListResp<FaqOut>>('/faqs', params);
}

/** 新增 FAQ */
export function createFaq(body: FaqCreateBody) {
  return post<FaqOut>('/faqs', body);
}

/** 编辑 FAQ */
export function updateFaq(id: string, body: FaqUpdateBody) {
  return patch<FaqOut>(`/faqs/${id}`, body);
}

/* ==================== 3.7  产品问题与承诺台账 ==================== */

/** 台账列表（产研角色后端强制只看派给自己的） */
export function listIssues(params: IssueQuery) {
  return get<ListResp<ProductIssueOut>>('/product-issues', params);
}

/** 台账统计（总数 / 未闭环 / 超期 / 影响续费 / 平均解决时长 / 各维分布） */
export function fetchIssueStats(params?: { start?: string; end?: string }) {
  return get<IssueStats>('/product-issues/stats', params);
}

/** 登记产品问题 / 销售承诺（type=promise 时 promised_date 必填） */
export function createIssue(body: IssueCreateBody) {
  return post<ProductIssueOut>('/product-issues', body);
}

/** 编辑台账记录（部分更新） */
export function updateIssue(id: string, body: IssueUpdateBody) {
  return patch<ProductIssueOut>(`/product-issues/${id}`, body);
}

/** 派单（产研 / 销售）；已解决 / 已回复的记录不可再派单（409） */
export function assignIssue(id: string, body: IssueAssignBody) {
  return post<ProductIssueOut>(`/product-issues/${id}/assign`, body);
}

/** 标记已解决（solution 必填） */
export function resolveIssue(id: string, body: IssueResolveBody) {
  return post<ProductIssueOut>(`/product-issues/${id}/resolve`, body);
}

/** 回复客户（C19 关键一步）；未派单时后端返回 409 */
export function replyIssue(id: string, body: IssueReplyBody) {
  return post<ProductIssueOut>(`/product-issues/${id}/reply`, body);
}

/* ==================== 3.8 投诉管理 ==================== */

/** 投诉列表 */
export function listComplaints(params: ComplaintQuery) {
  return get<ListResp<ComplaintOut>>('/complaints', params);
}

/** 投诉统计（按原因分布） */
export function fetchComplaintStats() {
  return get<ComplaintStats>('/complaints/stats');
}

/**
 * 登记投诉。
 *  原因 = 产品问题 / 承诺没兑现时，后端会**自动往台账插一条**，
 *    页面需提示「已同步登记到产品问题与承诺台账」。
 */
export function createComplaint(body: ComplaintCreateBody) {
  return post<ComplaintOut>('/complaints', body);
}

/** 处理投诉（改状态 / 结果；待处理会自动转处理中） */
export function updateComplaint(id: string, body: ComplaintUpdateBody) {
  return patch<ComplaintOut>(`/complaints/${id}`, body);
}

/** 投诉闭环（handle_result 必填） */
export function closeComplaint(id: string, body: ComplaintCloseBody) {
  return post<ComplaintOut>(`/complaints/${id}/close`, body);
}

/** 投诉升级（仅 admin / delivery_manager，后端 403） */
export function escalateComplaint(id: string, body?: { remark?: string }) {
  return post<ComplaintOut>(`/complaints/${id}/escalate`, body ?? {});
}

/* ==================== 3.9 运营指标（活跃率） ==================== */

/** 活跃率看板（总览 + 分学校排名 + 近 6 期趋势） */
export function fetchMetricDashboard(params: {
  period_type?: string;
  period?: string;
  owner_id?: string;
}) {
  return get<OperationMetricDashboard>('/operation-metrics/dashboard', params);
}

/** 运营指标列表 */
export function listOperationMetrics(params: OperationMetricQuery) {
  return get<ListResp<OperationMetric>>('/operation-metrics', params);
}

/** 手工录入单条指标（活跃率由后端自动算 = 登陆人数 ÷ 总账号数） */
export function createOperationMetric(body: OperationMetricCreateBody) {
  return post<OperationMetric>('/operation-metrics', body);
}

/** 编辑指标 / 人工标记高质量文件数量 */
export function updateOperationMetric(id: string, body: OperationMetricUpdateBody) {
  return patch<OperationMetric>(`/operation-metrics/${id}`, body);
}

/** 导入运营指标（multipart 字段名固定为 file） */
export function importOperationMetrics(file: File) {
  const form = new FormData();
  form.append('file', file);
  return post<MetricImportResult>('/operation-metrics/import', form);
}

/**
 * 下载导入模板（xlsx）。
 *  接口需要登录态，不能用裸 `<a href>`（不会带 Authorization），
 *    这里用 axios 取 blob 再触发浏览器下载。
 */
export async function downloadMetricTemplate() {
  const resp = await client.get<Blob>('/operation-metrics/template', { responseType: 'blob' });
  const url = window.URL.createObjectURL(resp.data);
  const a = document.createElement('a');
  a.href = url;
  a.download = '运营指标导入模板.xlsx';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  window.URL.revokeObjectURL(url);
}

/* ==================== 3.10 回访记录（运营记录） ==================== */

/** 运营记录列表（回访 / 培训 / 数据报告 / 活动 / 需求收集 / 投诉跟进） */
export function listOperationRecords(params: OperationRecordQuery) {
  return get<ListResp<OperationRecordOut>>('/operation-records', params);
}

/** 新增运营记录 / 需求登记 */
export function createOperationRecord(body: OperationRecordCreateBody) {
  return post<OperationRecordOut>('/operation-records', body);
}

/** 编辑运营记录（需求登记的状态切换也走这里） */
export function updateOperationRecord(id: string, body: OperationRecordUpdateBody) {
  return patch<OperationRecordOut>(`/operation-records/${id}`, body);
}

/** 删除运营记录（前端需二次确认） */
export function deleteOperationRecord(id: string) {
  return del<{ ok: boolean }>(`/operation-records/${id}`);
}

/* ==================== 3.11 验收管理 ==================== */

/** 验收列表 */
export function listAcceptances(params: AcceptanceQuery) {
  return get<ListResp<AcceptanceOut>>('/acceptances', params);
}

/** 验收临近清单（服务期到期前 N 个工作日，SOP 默认 15） */
export function fetchAcceptanceDue(params?: { workdays?: number }) {
  return get<{ items: AcceptanceDueItem[]; total: number }>('/acceptances/due', params);
}

/** 发起 / 更新验收登记（同一合同重复调用为更新） */
export function createAcceptance(body: { contract_id: string }) {
  return post<AcceptanceOut>('/acceptances', body);
}

/** 提交验收申请（记申请日期） */
export function submitAcceptance(id: string, body?: { remark?: string }) {
  return post<AcceptanceOut>(`/acceptances/${id}/submit`, body ?? {});
}

/** 验收通过（signed_date 客户签署确认书日期必填；通过后合同转「已验收」） */
export function passAcceptance(id: string, body: AcceptancePassBody) {
  return post<AcceptanceOut>(`/acceptances/${id}/pass`, body);
}

/** 验收未通过（rectify_note 整改说明必填） */
export function failAcceptance(id: string, body: AcceptanceFailBody) {
  return post<AcceptanceOut>(`/acceptances/${id}/fail`, body);
}

/** 编辑验收记录 */
export function updateAcceptance(id: string, body: AcceptanceUpdateBody) {
  return patch<AcceptanceOut>(`/acceptances/${id}`, body);
}

/* ==================== 3.12 续费与增购 ==================== */

/** 续费评估列表 */
export function listRenewals(params: RenewalQuery) {
  return get<ListResp<RenewalOut>>('/renewals', params);
}

/** 到期前 N 天（默认 90）续费清单：附带活跃率 / 验收状态 / 未闭环问题数 */
export function fetchRenewalDue(params?: { days?: number }) {
  return get<{ items: RenewalDueItem[]; total: number }>('/renewals/due', params);
}

/** 续费率统计（含 SaaS 基准 85% 与差距） */
export function fetchRenewalStats() {
  return get<RenewalStats>('/renewals/stats');
}

/** 新建续费评估 */
export function createRenewal(body: RenewalCreateBody) {
  return post<RenewalOut>('/renewals', body);
}

/** 编辑续费评估 */
export function updateRenewal(id: string, body: RenewalUpdateBody) {
  return patch<RenewalOut>(`/renewals/${id}`, body);
}

/** 续费结论（已续费 / 已流失 / 推进中 / 待评估） */
export function concludeRenewal(id: string, body: RenewalConcludeBody) {
  return post<RenewalOut>(`/renewals/${id}/conclude`, body);
}

/* ==================== 4.6 运营复盘 ==================== */

/** 运营复盘（续费率与流失原因 / 投诉 / 产品问题 / 交付延期 / 活跃率） */
export function fetchOperationReview(params?: { period?: string }) {
  return get<OperationReviewResp>('/dashboard/operation-review', params);
}
