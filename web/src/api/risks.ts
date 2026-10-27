/**
 * P1-c 接口：四类预警（预警清单 / 手工扫描 / 处理）、6.7 预警规则配置、
 * 转化率分析、2.5 丢单登记与分析。
 *
 * 约定：ID 一律 string，金额一律整数分（*_fen）。
 */
import { get, patch, post } from './client';
import type {
  ConversionResp,
  Lead,
  ListResp,
  LostAnalysisResp,
  RiskAlert,
  RiskCounts,
  RiskQuery,
  RiskScanResp,
  RuleConfig,
} from '@/types';

/* ---------------- 预警清单（2.4） ---------------- */

/** 预警清单（status 默认 pending；专员后端强制只看自己） */
export function listRisks(params: RiskQuery) {
  return get<ListResp<RiskAlert>>('/risks', params);
}

/** 预警状态计数（工作台/风险中心徽标） */
export function fetchRiskCounts() {
  return get<RiskCounts>('/risks/counts');
}

/** 手工触发预警扫描（卡单 / 总时长 / 节奏窗口 + 转化率），可重复执行（幂等） */
export function scanRisks() {
  return post<RiskScanResp>('/risks/scan');
}

/** 处理预警：待处理 / 处理中 / 已解除 */
export function handleRisk(riskId: string, body: { status: string; handle_note?: string }) {
  return patch<RiskAlert>(`/risks/${riskId}`, body);
}

/* ---------------- 6.7 预警规则配置 ---------------- */

/** 预警规则与阈值列表（登录即可读） */
export function fetchRiskRules() {
  return get<{ items: RuleConfig[]; total: number }>('/risk-rules');
}

/** 修改阈值（仅 admin / market_manager）。只提交改动过的行。 */
export function updateRiskRules(items: { key: string; value: string | number }[]) {
  return patch<{ ok: boolean; items: RuleConfig[] }>('/risk-rules', { items });
}

/* ---------------- 转化率分析 ---------------- */

/** 阶段转化率 vs 基准（owner_id 仅管理人员生效，专员后端强制本人） */
export function fetchConversion(params?: { start?: string; end?: string; owner_id?: string }) {
  return get<ConversionResp>('/dashboard/conversion', params);
}

/* ---------------- 2.5 丢单 ---------------- */

/** 丢单分析（原因 / 环节 / 人员分布 + 明细清单） */
export function fetchLostAnalysis(params?: {
  start?: string;
  end?: string;
  owner_id?: string;
  include_leads?: boolean;
}) {
  return get<LostAnalysisResp>('/dashboard/lost-analysis', params);
}

/** 登记丢单（必须多选 R1–R5） */
export function loseOpportunity(
  leadId: string,
  body: { lost_reason_types: string[]; remark?: string },
) {
  return post<{ lead: Lead }>(`/opportunities/${leadId}/lose`, body);
}

/** 撤销丢单（误判可恢复，留日志） */
export function revertLoss(leadId: string, remark?: string) {
  return post<{ lead: Lead }>(`/opportunities/${leadId}/lose/revert`, { remark });
}
