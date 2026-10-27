/**
 * AI 销售助手接口（5.1 对话与录入 / 5.2 我的待确认 / 5.3 诊断报告历史）。
 *
 *  核心规则：AI 只产出草稿，**必须由项目负责人确认后才生效**。
 * 路径前缀统一 /ai/...，金额一律整数分（*_fen）。
 */
import { LONG_TIMEOUT, get, post } from './client';
import type { Lead, ListResp } from '@/types';

/* ==================== 契约类型 ==================== */

/** AI 能力状态 GET /ai/status */
export interface AiStatus {
  llm_configured: boolean;
  model: string | null;
  /** 恒为 'none'：AI 不可用时直接失败，不做规则降级 */
  fallback_mode: string;
  note: string;
  /** 失败策略：fail_closed = 模型不可用就报错，不做规则降级 */
  degrade_policy?: string;
  provider?: string | null;
  settings_file?: string | null;
  /** 各用途（extract / diagnose / compose）的模型配置投影（不含密钥） */
  profiles?: Record<string, { model?: string; base_url?: string; auth_present?: boolean }> | null;
}

/** 草稿里的跟进记录建议 */
export interface AiDraftFollowup {
  method?: string;
  content?: string;
  followed_at?: string;
}

/** 草稿里的完成标准勾选建议 */
export interface AiDraftStandard {
  standard_id: string;
  content: string;
  evidence?: string;
  is_hard_gate?: boolean;
}

/** 草稿里的金额修正建议（before_fen 可能为 null） */
export interface AiDraftAmount {
  amount_fen: number;
  before_fen: number | null;
  reason?: string;
}

/** 草稿负载：确认时后端按这些键落库 */
export interface AiDraftPayload {
  followup?: AiDraftFollowup | null;
  standards?: AiDraftStandard[];
  amount?: AiDraftAmount | null;
  risks?: string[];
  advance_stage?: boolean;
  lead_stage?: string | null;
  /** 未定位到客户时，前端可在 payload_override 里补 lead_id */
  lead_id?: string | null;
  /** 负责人审查后写入统一项目档案，允许暂定及区间。 */
  schedule?: { next_node?: string | null; next_node_time?: string | null } | null;
}

/**
 * 未定位到客户时的候选商机。
 *
 *  后端只下发**模型可见的候选编号** `key`（C1..Cn）与展示字段，
 * **不含 lead_id**（key→lead_id 的映射只留在后端单次请求内）。
 * 因此前端不能把候选当成 lead_id 用 —— 需要客户时按 `name` 反查或让用户在下拉里选。
 */
export interface AiMatchCandidate {
  key: string;
  name: string;
  city: string;
  owner_name: string | null;
  status_label: string;
}

/** 确认后的落库结果 */
export interface AiDraftApplyResult {
  applied: {
    followup: string | null;
    standards: string[];
    amount: string | null;
    risks: string[];
    stage: string | null;
  };
  notes: string[];
}

/** AI 更新草稿 */
export interface AiDraft {
  id: string;
  source_message: string;
  channel: string;
  lead_id: string | null;
  lead_name: string | null;
  match_confidence: number;
  match_candidates: AiMatchCandidate[];
  summary: string;
  payload: AiDraftPayload;
  status: string;
  created_by: string | null;
  created_by_name: string | null;
  confirmed_by_name: string | null;
  confirmed_at: string | null;
  apply_result: AiDraftApplyResult | null;
  /** llm=Agently 模型解析（历史数据里可能有 rule） */
  parse_mode: string;
  model_used: string | null;
  created_at: string;
}

/** 诊断里的「缺失的关键动作」：标准编号 + 为什么缺 */
export interface AiDiagnosisMissingAction {
  standard_key: string;
  why: string;
}

/** 诊断里的风险发现：R1–R5 + 判定依据 */
export interface AiDiagnosisRiskFinding {
  risk_type: string;
  evidence: string;
}

/** 诊断里逐条标准的完成快照 */
export interface AiDiagnosisStandard {
  key: string;
  content: string;
  is_hard_gate: boolean;
  evidence_hint?: string;
  is_done: boolean;
  evidence?: string;
}

/** 诊断报告的结构化摘要（后端把模型的结论也结构化落库，便于逐块呈现） */
export interface AiDiagnosisStructured {
  stay_days: number;
  base_days: number;
  overdue: boolean;
  missing_hard_gates: string[];
  done_count: number;
  total_count: number;
  /** 一句话诊断（卡在哪里） */
  stage_diagnosis?: string;
  /** 缺失的标准动作（spec/06 C1：缺失动作要说清哪一条、为什么） */
  missing_actions?: AiDiagnosisMissingAction[];
  /** 风险提示（R1–R5 + 依据） */
  risk_findings?: AiDiagnosisRiskFinding[];
  /** 具体动作建议 */
  suggested_actions?: string[];
  /** 话术 */
  talk_tracks?: string[];
  /** 下周计划 */
  next_week_plan?: string[];
  /** 诊断时点的标准完成快照 */
  standards?: AiDiagnosisStandard[];
  warnings?: string[];
}

/** AI 诊断报告 */
export interface AiDiagnosis {
  id: string;
  lead_id: string | null;
  lead_name: string | null;
  stage: string | null;
  stage_label: string;
  trigger: string;
  /** Markdown 正文（前端用轻量渲染，不引 Markdown 库） */
  content: string;
  structured: AiDiagnosisStructured | null;
  adopted: boolean | null;
  created_by_name: string | null;
  parse_mode: string;
  /** 本次实际使用的模型（后端下发，用于口径可查） */
  model_used?: string | null;
  created_at: string;
}

/** 确认草稿响应 */
export interface ConfirmDraftResp {
  draft: AiDraft;
  applied: AiDraftApplyResult['applied'];
  notes: string[];
  lead: Lead;
}

/** 批量确认响应 */
export interface ConfirmBatchResp {
  ok: boolean;
  confirmed: number;
  failed: { id: string; message: string }[];
}

/** 确认时允许修正的键（只需放改动的键） */
export interface AiDraftOverride {
  schedule?: { next_node: string | null; next_node_time: string | null };
  lead_id?: string;
  followup?: AiDraftFollowup;
  standards?: AiDraftStandard[];
  amount?: AiDraftAmount;
  risks?: string[];
}

/* ==================== 请求函数 ==================== */

/** AI 能力状态：未配 key 时 AI 功能直接不可用（不做规则降级） */
export function aiStatus() {
  return get<AiStatus>('/ai/status');
}

/** 5.1 发一段结构化信息 → 生成更新草稿（lead_id 可空，由后端定位） */
export function aiIntake(body: { message: string; lead_id?: string | null; channel?: string }) {
  // 大模型解析通常几秒，但网络抖动 + 重试可能接近 1 分钟 → 用长超时
  return post<AiDraft>('/ai/intake', body, { timeout: LONG_TIMEOUT });
}

/** 5.2 草稿列表（默认只看本人的 pending） */
export function listDrafts(params?: {
  status?: string;
  mine_only?: boolean;
  /** 只看某个商机的草稿（2.2 商机详情用：AI 抽到的时间一键填入） */
  lead_id?: string;
  page?: number;
  page_size?: number;
}) {
  return get<ListResp<AiDraft>>('/ai/drafts', { status: 'pending', ...params });
}

/** 草稿详情 */
export function getDraft(id: string) {
  return get<AiDraft>(`/ai/drafts/${id}`);
}

/**  确认草稿（可带 payload_override 修正 + remark 备注），确认后才落库生效 */
export function confirmDraft(
  id: string,
  body?: { payload_override?: AiDraftOverride; remark?: string },
) {
  return post<ConfirmDraftResp>(`/ai/drafts/${id}/confirm`, body ?? {});
}

/** 忽略草稿 */
export function ignoreDraft(id: string, body?: { remark?: string }) {
  return post<{ ok: boolean }>(`/ai/drafts/${id}/ignore`, body ?? {});
}

/** 批量确认（逐条独立，失败返回 failed 明细） */
export function confirmDraftsBatch(ids: string[]) {
  return post<ConfirmBatchResp>('/ai/drafts/confirm-batch', { ids });
}

/** 5.1/S2-4 让 AI 诊断这个项目 */
export function aiDiagnose(body: { lead_id: string; question?: string }) {
  // 实测开启思考后单次诊断约 30 秒以上 → 必须用长超时
  return post<AiDiagnosis>('/ai/diagnose', body, { timeout: LONG_TIMEOUT });
}

/** 5.3 诊断报告历史 */
export function listDiagnoses(params?: { lead_id?: string; page?: number; page_size?: number }) {
  return get<ListResp<AiDiagnosis>>('/ai/diagnoses', params);
}

/** 解析方式标签：新数据恒为「Agently 模型解析」；rule 仅存在于历史数据 */
export const PARSE_MODE_LABEL: Record<string, string> = {
  llm: 'Agently 模型解析',
  rule: '历史规则解析',
};

/** 解析方式标签颜色 */
export const PARSE_MODE_COLOR: Record<string, string> = {
  llm: 'geekblue',
  rule: 'orange',
};
