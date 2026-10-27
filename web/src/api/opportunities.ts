/**
 * 闭环二 · 商机管理接口（2.1 列表 / 2.2 详情：环节推进、金额、风险、流转）。
 * 约定：ID 一律 string，金额一律整数分（*_fen）。
 */
import { get, patch, post } from './client';
import type {
  AmountChangeResp,
  AutoApplyResp,
  AutoJudgeResp,
  Lead,
  ListResp,
  OpportunityDetailResp,
  RiskAlert,
  RiskCreateResp,
  StageDetailProgress,
  StageMoveResp,
  StandardCondition,
  StandardJudgementResp,
  SalesStageConfirmationRequest,
  SalesStageConfirmationResp,
  ToggleStandardResp,
} from '@/types';

/** 2.1 商机列表（后端默认按「环节停留最久优先」排序） */
export function listOpportunities(params: {
  keyword?: string;
  page?: number;
  page_size?: number;
}) {
  return get<ListResp<Lead>>('/opportunities', params);
}

/** 2.2 商机详情聚合（环节进度 / 金额变更 / 风险 / 流转 / 跟进） */
export function getOpportunityDetail(leadId: string) {
  return get<OpportunityDetailResp>(`/opportunities/${leadId}`);
}

/**
 * **指定环节**的进度与达成依据（点 7 环节步骤条看历史 / 未来环节）。
 *
 * - 历史环节：返回当时勾选的标准 + 达成依据原文；
 * - 没进入过的环节：标准清单照常返回、`completion.is_done=false`、`entered_at=null`；
 * - 非法环节 code → 422；别人负责的商机 → 403。
 */
export function getStageProgressDetail(leadId: string, stage: string) {
  return get<StageDetailProgress>(`/opportunities/${leadId}/stages/${stage}/progress`);
}

/**
 * 勾选 / 取消完成标准。
 *
 *  evidence 已改为**可选**：
 * 不传时后端会去跟进记录里自动找依据并照抄原文；**找不到才会 422**
 * （文案「没有可依据的跟进信息」）—— 这是为了不退回"无痕补勾"。
 * 想省事就直接用 autoJudgeStandards + autoApplyStandards（AI 按跟进判定并采纳）。
 */
export function toggleStandard(
  leadId: string,
  standardId: string,
  body: { is_done: boolean; evidence?: string; remark?: string },
) {
  return post<ToggleStandardResp>(
    `/opportunities/${leadId}/standards/${standardId}/toggle`,
    body,
  );
}

/**
 *  AI 按「已提交的跟进信息」逐条判定当前环节的完成标准（**手动重判**入口）。
 *
 *  正常路径**不需要人点**：跟进写入成功后系统会自动判定并落库，进页面直接读
 * {@link getStandardJudgement} 即可；本接口只用于"判定结果不对 / 有新跟进想立刻重判"。
 * 返回里每条判定都带 `evidence_quote`（引用跟进原文）+ `followup_id`，可核对；
 * 判定结果同时会覆盖存储（同一商机只留最新一条）。
 * AI 不可用 → 503（后端明确失败，不降级猜）。
 */
export function autoJudgeStandards(leadId: string) {
  return post<AutoJudgeResp>(`/opportunities/${leadId}/standards/auto-judge`, {});
}

export type CompletedReview = Omit<AutoJudgeResp, 'items'> & {
  items: Array<AutoJudgeResp['items'][number] & {
    current_evidence: string | null;
    current_done_at: string | null;
  }>;
};

export function reviewCompletedStandards(leadId: string) {
  return post<CompletedReview>(`/opportunities/${leadId}/standards/review-completed`, {});
}

/**
 * 读该商机**最新一次** AI 判定（系统自动触发的那次）。
 *
 * `judgement=null` = 还没判定过（或判定失败）；`stale=true` = 判完之后又有新跟进，
 * 需要（自动）重新判定。前端据此决定是否静默重判。
 */
export function getStandardJudgement(leadId: string) {
  return get<StandardJudgementResp>(`/opportunities/${leadId}/standards/judgement`);
}

/**
 * 采纳 AI 判定并落库（依据=引用的跟进原文；服务端会再逐字核对一遍）。
 *
 *  `conditions` 必须原样回传：并列交付物的标准（如「建设方案与报价」）由宿主按
 * 共享固定双条件 + AND 规则重新组合，缺分项或编造原话都会整批 422。
 * 前端不因传了 `conditions` 而自行判定达成（服务器规则为准）。
 */
export function autoApplyStandards(
  leadId: string,
  items: Array<{
    standard_id: string;
    followup_id: string;
    evidence_quote: string;
    conditions?: StandardCondition[];
  }>,
) {
  return post<AutoApplyResp>(`/opportunities/${leadId}/standards/auto-apply`, { items });
}

/** 负责人明确确认当前展示版本的阶段候选，并仅应用明确选择的档案更正。 */
export function confirmStageCandidates(leadId: string, body: SalesStageConfirmationRequest) {
  return post<SalesStageConfirmationResp>(
    `/opportunities/${leadId}/standards/sales-confirm`,
    body,
  );
}

/** 推进到下一环节（硬门槛未过时后端返回 409，detail 为提示语） */
export function advanceStage(leadId: string, basis: string, remark?: string) {
  return post<StageMoveResp>(`/opportunities/${leadId}/stage/advance`, { basis, remark });
}

export function signOpportunity(leadId: string, amountFen: number, signDate: string) {
  return post<{ lead: Lead }>(`/opportunities/${leadId}/stage/sign`, {}, {
    params: { contract_amount_fen: amountFen, sign_date: signDate },
  });
}

/** 回退上一环节 */
export function rollbackStage(leadId: string, basis: string, remark?: string) {
  return post<StageMoveResp>(`/opportunities/${leadId}/stage/rollback`, { basis, remark });
}

/** 金额滚动修正（必须填变更依据） */
export function changeAmount(leadId: string, amountFen: number, reason: string) {
  return post<AmountChangeResp>(`/opportunities/${leadId}/amount`, {
    amount_fen: amountFen,
    reason,
  });
}

/** 登记 R1–R5 风险 */
export function addRisks(leadId: string, riskTypes: string[], description: string) {
  return post<RiskCreateResp>(`/opportunities/${leadId}/risks`, {
    risk_types: riskTypes,
    description,
  });
}

/** 处理风险（标记已处理 / 已解除） */
export function updateRisk(riskId: string, body: { status: string; handle_note?: string }) {
  return patch<RiskAlert>(`/opportunities/risks/${riskId}`, body);
}
