import { get } from './client';
import type {
  ConversionResp,
  FunnelResp,
  Lead,
  LeadBoardResp,
  ListResp,
  Notification,
  Reminder,
  RiskCenterResp,
  SearchResp,
  StaffOption,
  WorkbenchResp,
} from '@/types';

/** 工作台（按角色返回区块） */
export function fetchWorkbench() {
  return get<WorkbenchResp>('/dashboard/workbench');
}

/** 线索看板 */
export function fetchLeadBoard(params: { start?: string; end?: string }) {
  return get<LeadBoardResp>('/dashboard/lead-board', params);
}

/** 2.3 业绩漏斗与加权预测（owner_id 仅管理人员可传） */
/**
 * 阶段转化率（实际 vs 基准，含是否预警）。
 * 4.3 团队视角用它给出"实际率 vs 基准"的预警，而不只是静态基准表。
 */
export function fetchConversion(params?: { owner_id?: string }) {
  return get<ConversionResp>('/dashboard/conversion', params);
}

export function fetchFunnel(params?: { owner_id?: string }) {
  return get<FunnelResp>('/dashboard/funnel', params);
}

/** 2.3 负责人下拉（市场人员） */
export function fetchStaffOptions() {
  return get<{ items: StaffOption[]; total: number }>('/dashboard/staff-options');
}

/** 2.4 风险与卡单中心 */
export function fetchRiskCenter() {
  return get<RiskCenterResp>('/dashboard/risk-center');
}

/* ---- 待办 ---- */

export function listReminders(params: {
  status?: string;
  type?: string;
  page?: number;
  page_size?: number;
}) {
  return get<ListResp<Reminder>>('/reminders', { status: 'pending', ...params });
}

/* ---- 通知 ---- */

export function listNotifications() {
  return get<{ items: Notification[]; total: number }>('/notifications');
}

/* ---- 全局搜索 ---- */

export function globalSearch(keyword: string) {
  return get<SearchResp>('/search', { keyword });
}

/** 供其它模块复用的线索类型导出 */
export type { Lead };
