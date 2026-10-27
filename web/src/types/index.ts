/**
 * 全系统接口类型定义（唯一来源）
 * 约定：金额字段一律为整数分（*_fen），时间字段为 ISO8601 字符串。
 */

/* ============ 通用 ============ */

/** 列表统一响应 */
export interface ListResp<T> {
  items: T[];
  total: number;
  page?: number;
  page_size?: number;
}

/** 字典项 */
export interface DictItem {
  value: string;
  label: string;
}

/** 全部字典（GET /dicts） */
export interface Dicts {
  lead_sources: DictItem[];
  org_types: DictItem[];
  lead_status: DictItem[];
  follow_methods: DictItem[];
  next_actions: DictItem[];
  risk_types: DictItem[];
  reminder_types: DictItem[];
  stages: DictItem[];
  roles: DictItem[];
  data_scopes: DictItem[];
  provinces: string[];
  /** 以下为后端实际返回的补充字典（可选，避免与旧版本不兼容） */
  assignment_actions?: DictItem[];
  dedup_status?: DictItem[];
  risk_status?: DictItem[];
  metrics?: DictItem[];
  /** 后端下发的环节基准（真实结构，勿当扁平 Record 用） */
  stage_baseline?: StageBaseline;
  /** P1-c：预警种类（业务风险 / 卡单 / 总时长 / 节奏窗口 / 转化率） */
  alert_kinds?: DictItem[];
  /** P1-d：评审卡 5 看定义 */
  review_items?: ReviewItemDef[];
  /** P1-d：真执行判定（能力问题 / 态度问题 / 正常） */
  review_verdicts?: DictItem[];
  /** P1-d：5 看单项结论（通过 / 有风险 / 不通过） */
  review_results?: DictItem[];
  /** P1-d：回款方式 / 期次 */
  payment_methods?: DictItem[];
  payment_periods?: DictItem[];
  /** P2 闭环三：合同状态 / 交付 10 阶段 / 运营动作类型 / 运营记录状态（后端可能尚未下发，故可选） */
  contract_status?: DictItem[];
  delivery_phases?: DictItem[];
  operation_action_types?: DictItem[];
  record_status?: DictItem[];
  /** P2 闭环三（台账 / 投诉 / 验收 / 续费 / 指标）：后端已下发，全部放开为可选 */
  metric_period_types?: DictItem[];
  issue_types?: DictItem[];
  issue_status?: DictItem[];
  handler_sides?: DictItem[];
  issue_sources?: DictItem[];
  complaint_reasons?: DictItem[];
  complaint_status?: DictItem[];
  acceptance_status?: DictItem[];
  renewal_status?: DictItem[];
  renewal_willingness?: DictItem[];
  alert_domains?: DictItem[];
}

/** 评审卡 5 看定义（GET /dicts → review_items） */
export interface ReviewItemDef {
  key: string;
  code: string;
  label: string;
  hint: string;
}

/** 环节基准（GET /dicts → stage_baseline） */
export interface StageBaseline {
  /** 环节 → 基准停留天数 */
  duration_days?: Record<string, number>;
  /** 环节 → 赢率 */
  win_probability?: Record<string, number>;
  /** 阶段转化基准 */
  conversion_baseline?: Record<string, number>;
  /** 7 环节合计基准天数 */
  total_days?: number;
}

/* ============ 用户与认证 ============ */

export type Role =
  | 'admin'
  | 'market_staff'
  | 'market_manager'
  | 'delivery_staff'
  | 'delivery_manager'
  | 'executive'
  | 'rnd'
  | 'biz_support';

export type DataScope = 'self' | 'department' | 'all';

export interface User {
  id: string;
  username: string;
  name: string;
  role: Role;
  data_scope: DataScope;
  department: string;
  phone: string;
  email: string;
  is_active: boolean;
  created_at: string;
}

export interface LoginResp {
  access_token: string;
  token_type: string;
  user: User;
}

export interface Department {
  id: string;
  name: string;
  parent_id?: string | null;
  created_at?: string;
}

/* ============ 线索（闭环一） ============ */

export type LeadStatus =
  | 'unassigned'
  | 'assigned'
  | 'following'
  | 'valid'
  | 'invalid'
  | 'lost'
  | 'signed';

/** 线索列表子视图 */
export type LeadView =
  | 'all'
  | 'unassigned'
  | 'mine'
  | 'pool'
  | 'duplicate'
  | 'invalid';

export interface Lead {
  contact_name?: string; contact_position?: string;
  next_action?: string; next_action_at?: string | null;
  appointment_waiting?: boolean; dossier_revision?: number;
  next_node?: string | null; next_node_time?: string | null;
  id: string;
  /** 客户名（两层模型里这条记录代表「项目」，name 仍是客户名称） */
  name: string;
  /**
   * **项目名**（客户-项目两层模型新增）。
   * 「客户 / 项目」标题与项目清单都用它；为空串时退回只显示客户名。历史数据可能为空。
   */
  project_name?: string;
  /** 所属客户 id（历史数据可能未回填） */
  customer_id?: string | null;
  /** 所属客户名称（后端口径 = lead.name，仅供展示） */
  customer_name?: string;
  source: string;
  province: string;
  city: string;
  district: string;
  org_type: string;
  status: LeadStatus;
  owner_id: string | null;
  owner_name: string | null;
  creator_id: string | null;
  creator_name: string | null;
  parent_id: string | null;
  is_suspect_duplicate: boolean;
  is_deleted: boolean;
  budget_amount_fen: number | null;
  contract_amount_fen: number | null;
  paid_amount_fen: number | null;
  unpaid_amount_fen: number | null;
  expected_sign_date: string | null;
  /** 「本环节预计完成时间」：R5 项目节奏不清的判定依据之一（专员填/客户确认） */
  /** 节奏是否清晰：本环节预计完成时间或预计签约时间有其一即为 true */
  rhythm_clear?: boolean;
  current_stage: string | null;
  stage_entered_at: string | null;
  stage_stay_days: number | null;
  stage_overdue: boolean;
  /** 当前环节的基准停留天数（后端按标准卡计算） */
  stage_base_days?: number | null;
  win_probability: number | null;
  weighted_amount_fen: number | null;
  risk_types: string[];
  lost_reason_types: string[];
  assign_at: string | null;
  first_follow_at: string | null;
  last_follow_at: string | null;
  valid_at: string | null;
  sign_date: string | null;
  deliver_handoff_status: string | null;
  remark: string;
  channel_unit: string;
  referrer: string;
  created_at: string;
  updated_at: string;
  /* ---- P1-d 丢单（登记后 status = 'lost'） ---- */
  lost_at?: string | null;
  lost_remark?: string | null;
  /** 丢单原因中文标签（R1–R5） */
  lost_reason_labels?: string[];
  /* ---- 后端 serializers.lead_out 附带的展示字段（全部可选，按需使用） ---- */
  source_label?: string;
  org_type_label?: string;
  status_label?: string;
  current_stage_label?: string;
  risk_labels?: string[];
  duplicate_hint?: string | null;
  dedup_status?: string | null;
  /** 首次跟进截止时刻（后端按 1 个工作日算好，权威） */
  first_follow_deadline?: string | null;
  /** 7 个工作日未跟进回收的截止时间（后端按工作日算好，权威） */
  recycle_deadline?: string | null;
}

export interface Contact {
  id: string;
  lead_id: string;
  name: string;
  position: string;
  phone: string;
  wechat: string;
  is_primary: boolean;
  remark: string;
  created_at: string;
}

export interface FollowUp {
  id: string;
  lead_id: string;
  user_id: string;
  user_name: string;
  method: string;
  followed_at: string;
  content: string;
  is_valid: string;
  next_action: string;
  next_follow_at: string | null;
  stage: string | null;
  related_standard_id: string | null;
  /** manual 手工录入 / ai_confirmed AI 生成并确认 */
  source: string;
  created_at: string;
  /* ---- 展示字段 ---- */
  method_label?: string;
  stage_label?: string;
  next_action_label?: string;
}

export interface Assignment {
  id: string;
  lead_id: string;
  action: string;
  from_owner_id: string | null;
  from_owner_name: string | null;
  to_owner_id: string | null;
  to_owner_name: string | null;
  operator_id: string | null;
  operator_name: string | null;
  remark: string;
  created_at: string;
}

export interface OperationLog {
  id: string;
  operator_id: string | null;
  operator_name: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  target_name: string;
  detail: string;
  ip: string;
  created_at: string;
}

/** 撞单查重命中项 */
export interface DuplicateBrief {
  id: string;
  name: string;
  owner_name: string | null;
  province: string;
  city: string;
  district: string;
  status: LeadStatus;
}

export interface DuplicateWarning {
  suspect: boolean;
  duplicates: DuplicateBrief[];
}

export interface LeadCreateBody {
  name: string;
  /**
   * 项目名（选填）：同一客户可有多个项目商机，用项目名区分
   * （如「教学平台试点项目」）。留空则该商机只显示客户名。
   */
  project_name?: string | null;
  source: string;
  province: string;
  city: string;
  district: string;
  org_type: string;
  contact_name?: string;
  contact_phone?: string;
  contact_wechat?: string;
  contact_position?: string;
  remark?: string;
  channel_unit?: string;
  referrer?: string;
}

export interface LeadCreateResp {
  lead: Lead;
  duplicate_warning: DuplicateWarning;
}

/** 线索详情聚合响应 */
export interface LeadDetailResp {
  lead: Lead;
  contacts: Contact[];
  followups: FollowUp[];
  assignments: Assignment[];
  reminders: Reminder[];
  logs: OperationLog[];
}

export interface LeadQuery {
  view?: LeadView;
  lifecycle?: 'lead' | 'opportunity';
  active_only?: boolean;
  keyword?: string;
  status?: string;
  source?: string;
  org_type?: string;
  province?: string;
  city?: string;
  owner_id?: string;
  page?: number;
  page_size?: number;
  sort?: 'recent' | 'overdue';
  is_deleted?: boolean;
}

/** 负载视图行 */
export interface LoadViewRow {
  user_id: string;
  name: string;
  unassigned_count: number;
  assigned_count: number;
  following_count: number;
  total_count: number;
}

/* ============ 待办 / 通知 ============ */

export type ReminderStatus = 'pending' | 'done';

export interface Reminder {
  id: string;
  lead_id: string | null;
  lead_name: string | null;
  type: string;
  content: string;
  remind_at: string;
  receiver_id: string | null;
  receiver_name: string | null;
  status: ReminderStatus;
  done_at: string | null;
  created_at: string;
}

export interface Notification {
  id: string;
  title: string;
  content: string;
  type: string;
  is_read: boolean;
  created_at: string;
}

/* ============ 目标与看板 ============ */

/** 目标指标：有效商机数 / 新增线索数 / 有效率(0-1) */
export type TargetMetric = 'valid_lead_count' | 'new_lead_count' | 'valid_rate';

export interface Target {
  id: string;
  period_type: string;
  period: string;
  user_id: string;
  user_name: string;
  metric: TargetMetric;
  target_value: number;
  actual_value: number;
  achievement_rate: number;
}

export interface AchievementMetric {
  metric: TargetMetric | string;
  label?: string;
  target_value: number;
  actual_value: number;
  achievement_rate: number;
}

export interface AchievementUser {
  user_id: string;
  user_name: string;
  metrics: AchievementMetric[];
}

export interface AchievementResp {
  period: string;
  items: AchievementUser[];
}

/** 工作台按角色返回 */
export interface WorkbenchFunnelItem {
  stage: string;
  label: string;
  count: number;
  amount_fen: number;
  weighted_amount_fen: number;
}

export interface TeamOverview {
  new_valid_count: number;
  total_valid_count: number;
  weighted_amount_fen: number;
  signed_amount_fen: number;
  paid_amount_fen: number;
  stage_distribution: WorkbenchFunnelItem[];
  stuck_items: Lead[];
}

export interface WorkbenchResp {
  role: Role | string;
  todo_count: number;
  todo_items: Reminder[];
  funnel: WorkbenchFunnelItem[];
  stuck_items: Lead[];
  pending_drafts: number;
  my_achievement: AchievementMetric[];
  team_overview: TeamOverview | null;
  unassigned_count: number;
  risk_items: unknown[];
}

export interface LeadBoardResp {
  source_distribution: {
    source: string;
    label: string;
    count: number;
    valid_count: number;
    valid_rate: number;
  }[];
  status_funnel: { status: string; label: string; count: number }[];
  per_person: {
    user_id: string;
    user_name: string;
    new_lead_count: number;
    valid_count: number;
    valid_rate: number;
  }[];
  follow_timeliness: {
    avg_first_follow_hours: number;
    on_time_rate: number;
    overdue_count: number;
  };
  trend: { date: string; new_count: number; valid_count: number }[];
}

/* ============ 导入 ============ */

export interface ImportBatch {
  id: string;
  file_name: string;
  importer_id?: string | null;
  importer_name?: string | null;
  total: number;
  success: number;
  failed: number;
  errors?: { row: number; message: string }[];
  batch_no?: string;
  created_at: string;
}

export interface ImportResultResp {
  batch: ImportBatch;
  total: number;
  success: number;
  failed: number;
  errors: { row: number; message: string }[];
}

/* ============ 搜索 ============ */

export interface SearchResp {
  leads: Lead[];
  contacts: Contact[];
}

/* ============ 闭环二 · 商机（环节推进 / 金额 / 风险 / 流转） ============ */

/** 环节完成标准卡（6.5 配置，前端只读消费） */
export interface StageStandard {
  id: string;
  stage: string;
  stage_label: string;
  /** 环节内序号，后端为字符串 */
  seq: string;
  content: string;
  is_hard_gate: boolean;
  duration_days: number;
  evidence_hint: string | null;
  sort_order: number;
  is_active: boolean;
}

/** 某条标准在该商机上的完成情况 */
export interface StageCompletion {
  id: string;
  lead_id: string;
  stage: string;
  standard_id: string;
  standard_content: string;
  is_done: boolean;
  evidence: string | null;
  done_by: string | null;
  done_by_name: string | null;
  done_at: string | null;
  remark: string | null;
  source: string;
  /** 来源中文标签（人工勾选 / 按跟进信息自动判定并采纳 …），后端下发 */
  source_label?: string;
}

/** 标准 + 完成情况成对返回 */
export interface StageProgressItem {
  standard: StageStandard;
  completion: StageCompletion;
}

/** 当前环节进度 */
export interface StageProgress {
  stage: string | null;
  stage_label: string;
  standards: StageProgressItem[];
  hard_gate_passed: boolean;
  missing_hard_gates: string[];
  stay_days: number | null;
  base_days: number;
  overdue: boolean;
  done_count: number;
  total_count: number;
}

/**
 * **指定环节**的进度与达成依据（`GET /opportunities/{id}/stages/{stage}/progress`）。
 *
 * 与 `StageProgress` 的区别：可以查**历史环节**与**还没开始的环节**
 * （步骤条点任意环节看当时的完成标准与证据）。
 */
export interface StageDetailProgress {
  stage: string;
  stage_label: string;
  /** 是不是当前环节（当前环节的改动仍只在「环节进度」Tab 里做，抽屉只读） */
  is_current: boolean;
  /** 进入该环节的时间（未进入过 → null） */
  entered_at: string | null;
  /** 离开该环节的时间（当前环节 / 未进入 → null） */
  left_at: string | null;
  /** 停留自然日：当前环节算到今天，历史环节算到离开 */
  stay_days: number | null;
  base_days: number;
  overdue: boolean;
  done_count: number;
  total_count: number;
  hard_gate_passed: boolean;
  missing_hard_gates: string[];
  standards: StageProgressItem[];
  transitions: StageTransition[];
}

/** 金额变更留痕 */
export interface AmountChange {
  id: string;
  lead_id: string;
  before_fen: number | null;
  after_fen: number | null;
  delta_fen: number | null;
  reason: string;
  stage: string | null;
  stage_label: string;
  operator_name: string | null;
  source: string;
  created_at: string;
}

/** P1-c 预警种类 */
export type AlertKind =
  | 'business_risk'
  | 'stuck'
  | 'total_duration'
  /** 项目节奏不清：既无本环节预计时间，也无客户确认时间（R5 主口径） */
  | 'rhythm_unclear'
  /** 年度节奏窗口参照：仅长期无时间承诺且正处关键窗口时叠加 */
  | 'rhythm_window'
  | 'conversion';

/** 预警状态 */
export type RiskStatus = 'pending' | 'processing' | 'resolved';

/** R1–R5 风险 / 四类系统预警 */
export interface RiskAlert {
  id: string;
  lead_id: string;
  /** 客户名（后端批量回填，清单里不必显示 UUID） */
  lead_name?: string | null;
  /** P1-c 新增：预警种类 */
  kind: AlertKind | string;
  kind_label: string;
  risk_type: string;
  risk_label: string;
  /** P1-c 新增：责任人（后端按商机归属冗余） */
  owner_id: string | null;
  owner_name: string | null;
  trigger_rule: string;
  description: string;
  status: RiskStatus | string;
  status_label: string;
  handler_name: string | null;
  handled_at: string | null;
  handle_note: string | null;
  source: string;
  created_at: string;
}

/** 预警状态计数（GET /risks/counts） */
export interface RiskCounts {
  pending: number;
  processing: number;
  resolved: number;
  total: number;
}

/** 预警清单查询参数 */
export interface RiskQuery {
  status?: string;
  kind?: string;
  lead_id?: string;
  mine?: boolean;
  page?: number;
  page_size?: number;
}

/** 手工触发扫描返回（POST /risks/scan） */
export interface RiskScanResp {
  ok: boolean;
  scanned: number;
  created: { stuck: number; total_duration: number; rhythm_unclear: number };
  total_created: number;
  conversion_notified: number;
  breached: string[];
}

/** 预警规则配置项（GET /risk-rules） */
export interface RuleConfig {
  key: string;
  value: number | string;
  value_raw: string;
  /** int / float */
  value_type: string;
  label: string;
  /** risk / review */
  group: string;
  unit: string;
  description: string;
  sort_order: number;
}

/* ---- 转化率分析（GET /dashboard/conversion） ---- */

export interface ConversionPair {
  path: string;
  from_stage: string;
  from_stage_label: string;
  to_stage: string;
  to_stage_label: string;
  from_count: number;
  to_count: number;
  rate: number;
  baseline: number;
  warn_threshold: number;
  gap: number;
  alert: boolean;
}

export interface ConversionPerson {
  user_id: string;
  user_name: string;
  valid_count: number;
  advanced_count: number;
  rate: number;
  baseline: number;
  alert: boolean;
}

export interface ConversionResp {
  start: string | null;
  end: string | null;
  /** 'team' 或负责人 user_id */
  owner_scope: string;
  warn_ratio: number;
  min_sample: number;
  pairs: ConversionPair[];
  per_person: ConversionPerson[];
  breached: string[];
}

/* ---- 丢单分析（GET /dashboard/lost-analysis） ---- */

export interface LostReasonRow {
  risk_type: string;
  label: string;
  count: number;
  rate: number;
}

export interface LostStageRow {
  stage: string;
  label: string;
  count: number;
}

export interface LostOwnerRow {
  user_id: string | null;
  user_name: string;
  count: number;
}

export interface LostAnalysisResp {
  start: string | null;
  end: string | null;
  total_lost: number;
  lost_amount_fen: number;
  by_reason: LostReasonRow[];
  by_stage: LostStageRow[];
  by_owner: LostOwnerRow[];
  top_reason: LostReasonRow | null;
  items: Lead[];
}

/** 环节流转记录 */
export interface StageTransition {
  id: string;
  lead_id: string;
  from_stage: string | null;
  from_stage_label: string;
  to_stage: string;
  to_stage_label: string;
  transition_at: string;
  operator_name: string | null;
  stay_days: number;
  basis: string;
  remark: string | null;
}

/** 商机详情聚合响应 GET /opportunities/{lead_id} */
export interface OpportunityDetailResp {
  lead: Lead;
  stage_progress: StageProgress | null;
  amount_changes: AmountChange[];
  risks: RiskAlert[];
  transitions: StageTransition[];
  followups: FollowUp[];
}

/**
 * 复合标准的一个固定条件分项（宿主共享规则，`spec/doing/20` §12）。
 *
 * 前端只**透传**它，不据此判定整项达成：`satisfied` 与 AND 组合一律由服务器按同一
 * 规则修订计算；`state_source` 说明这条状态来自本轮逐字原文还是项目档案累计。
 */
export interface StandardCondition {
  condition_id: string;
  state: string;
  quote?: string;
  source_key?: string;
  state_source?: string;
}

/** 单条标准的 AI 判定结果（POST /opportunities/{id}/standards/auto-judge） */
export interface StandardJudgement {
  standard_id: string;
  seq: string;
  content: string;
  is_hard_gate: boolean;
  evidence_hint: string | null;
  satisfied: boolean;
  confidence: number | null;
  /** 引用的是**该商机真实跟进记录里的原文片段**（服务端已逐字核对） */
  evidence_quote: string | null;
  followup_id: string | null;
  followup_at: string | null;
  reason: string | null;
  /** 并列交付物标准的固定条件分项（采纳时必须原样回传，见 auto-apply） */
  conditions?: StandardCondition[];
  /** 分项状态（condition_id → state），由宿主 AND 规则计算整项达成时使用 */
  condition_states?: Record<string, string>;
  rule_revision?: string | null;
}

/** AI 按跟进信息自动判定完成标准（只判定、不写库） */
export interface AutoJudgeResp {
  judgement_id?: string | null;
  dossier_revision?: number | null;
  stage: string;
  stage_label: string;
  judged_at: string | null;
  model_used: string | null;
  parse_mode: string | null;
  items: StandardJudgement[];
  already_done: string[];
  hard_gate_passed: boolean;
  missing_hard_gates: string[];
  followup_scope?: number | null;
  warnings?: string[];
  /** 本次判定使用的固定条件共享规则修订（助手与 PC 同一版本，交接随之携带） */
  condition_rule_revision?: string | null;
}

/**
 * 一次 AI 判定记录（`GET /opportunities/{id}/standards/judgement`）。
 *
 * 由**系统自动**在跟进写入后产生（人不需要点按钮）；`status=pending` 表示
 * 「待项目负责人确认」，点「采纳」写入完成标准后才变 `accepted`。
 */
export interface StandardJudgementRecord {
  id: string;
  lead_id: string;
  stage: string;
  stage_label: string;
  judged_at: string | null;
  model_used: string | null;
  parse_mode: string | null;
  items: StandardJudgement[];
  already_done: string[];
  hard_gate_passed: boolean;
  missing_hard_gates: string[];
  /** 判定所依据的最新一条跟进时间：晚于它的跟进 = 还没判定过 */
  basis_followup_at: string | null;
  status: 'pending' | 'accepted' | 'ignored' | string;
  created_at: string | null;
}

/** 最新判定 + 是否已过期（stale=true 表示有新跟进还没判定过 → 前端自动重判） */
export interface StandardJudgementResp {
  judgement: StandardJudgementRecord | null;
  stale: boolean;
  latest_followup_at: string | null;
}

export interface SalesStageConfirmationRequest {
  judgement_id: string;
  stage: string;
  expected_dossier_revision: number | null;
  idempotency_key: string;
  confirmation_note: string;
  items: Array<{
    standard_id: string;
    confirmation_text: string;
    followup_id?: string | null;
    source_quote?: string | null;
  }>;
  corrections?: Array<{
    fact_key: 'contract_arrangement_status' | 'contract_plan';
    before_value: string | null;
    after_value: string;
    followup_id: string;
    source_quote: string;
  }>;
}

export interface SalesStageConfirmationResp {
  confirmation_id: string;
  idempotent_replay: boolean;
  judgement_id: string;
  stage: string;
  confirmed_by: string;
  confirmed_by_name: string;
  confirmed_at: string;
  dossier_revision: number | null;
  applied_count: number;
  items: Array<{
    standard_id: string;
    content: string;
    is_done: boolean;
    evidence: string;
    source: 'sales_confirmed';
    done_by: string | null;
    done_by_name: string | null;
    done_at: string | null;
  }>;
  corrections: SalesStageConfirmationRequest['corrections'];
  message: string;
  stage_progress: StageProgress;
}

/** 采纳 AI 判定（POST .../standards/auto-apply，原子：任一条引文核对不过整批 422） */
export interface AutoApplyResp {
  items: Array<{
    standard_id: string;
    applied: boolean;
    reason?: string | null;
    /** 采纳回执：各固定条件的状态与其**自己的**来源跟进/原话（宿主重算后的结果） */
    condition_sources?: Array<{
      condition_id: string;
      state: string;
      quote?: string | null;
      followup_id?: string | null;
      state_source?: string;
    }> | null;
    rule_revision?: string | null;
    remark?: string | null;
  }>;
  applied_count: number;
  rejected_count: number;
  stage_progress: StageProgress;
}

/** 勾选完成标准响应 */
export interface ToggleStandardResp {
  completion: StageCompletion;
  stage_progress: StageProgress;
}

/** 推进 / 回退环节响应 */
export interface StageMoveResp {
  lead: Lead;
  stage_progress: StageProgress;
}

/** 金额修正响应 */
export interface AmountChangeResp {
  lead: Lead;
  change: AmountChange | null;
}

/** 登记风险响应 */
export interface RiskCreateResp {
  items: RiskAlert[];
  total: number;
  lead: Lead;
}

/* ---- 报表：漏斗 / 风险中心 ---- */

export interface StaffOption {
  user_id: string;
  name: string;
  role: string;
}

export interface FunnelItem {
  stage: string;
  label: string;
  count: number;
  amount_fen: number;
  weighted_amount_fen: number;
}

export interface FunnelResp {
  /** 'team' 或负责人 user_id */
  owner_scope: string;
  funnel: FunnelItem[];
  total_amount_fen: number;
  weighted_amount_fen: number;
  baseline: Record<string, number>;
}

/** 年度节奏窗口（无窗口时后端返回 null） */
export interface RhythmWindow {
  month: number;
  title: string;
  hint: string;
}

export interface RiskCenterResp {
  stuck: Lead[];
  long_running: Lead[];
  rhythm_window: RhythmWindow | null;
  total_duration_warning_days: number;
  /* ---- P1-c 新增 ---- */
  /** 待处理预警（最多 50 条，后端已按角色过滤） */
  alerts: RiskAlert[];
  alert_total: number;
  counts: RiskCounts;
  conversion: ConversionResp;
}

/* ============ P1-d · 周报 / 周会视图 / 项目评审卡 / 回款 ============ */

/** 卡单条目（已拍平，周报与周会视图共用） */
export interface StuckItem {
  lead_id: string;
  lead_name: string;
  stage?: string | null;
  stage_label: string;
  stay_days: number;
  base_days: number;
  /** 仅周会视图逐人卡点返回 */
  missing_hard_gates?: string[];
  done_count?: number;
  total_count?: number;
}

/** 本周推进的完成标准（含证据） */
export interface AdvancedStandard {
  lead_id: string;
  lead_name: string;
  stage: string;
  stage_label: string;
  standard_content: string;
  evidence: string | null;
  done_at: string | null;
  done_by_name: string | null;
}

/** 周报指标汇总 */
export interface WeeklyMetrics {
  period_start: string;
  period_end: string;
  new_valid_count: number;
  new_lead_count: number;
  stage_distribution: FunnelItem[];
  in_progress_count: number;
  weighted_amount_fen: number;
  total_amount_fen: number;
  stuck_count: number;
  stuck_items: StuckItem[];
  signed_amount_fen: number;
  paid_amount_fen: number;
  /** 与 conversion.pairs 同构 */
  conversion: ConversionPair[];
  advanced_standards: AdvancedStandard[];
}

/** 周报正文生成方式 */
export type ReportGeneratedBy = 'pending' | 'ai' | 'rule' | 'manual';

export interface WeeklyReport {
  id: string;
  period: string;
  period_label: string;
  period_start: string;
  period_end: string;
  user_id: string;
  user_name: string;
  metrics: WeeklyMetrics;
  body: string | null;
  problems: string | null;
  next_plan: string | null;
  is_submitted: boolean;
  submitted_at: string | null;
  generated_by: ReportGeneratedBy | string;
  model_used: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/** GET /reports/weekly/current */
export interface WeeklyCurrentResp {
  period: string;
  period_label: string;
  item: WeeklyReport | null;
}

/** GET /reports/weekly/team */
export interface WeeklyTeamResp {
  period: string;
  period_label: string;
  items: WeeklyReport[];
  submitted: number;
  total: number;
}

/* ---- 4.5 周会视图 ---- */

/** 团队目标达成（周会视图内的精简口径） */
export interface TeamTargetRow {
  user_id: string;
  user_name: string;
  metric: string;
  target_value: number;
  actual_value: number;
  achievement_rate: number;
}

export interface PerPersonStage {
  stage: string;
  label: string;
  count: number;
  amount_fen: number;
  stay_days_avg: number;
  overdue_count: number;
}

export interface AiSuggestion {
  lead_id: string;
  lead_name: string;
  stage_label: string;
  content: string;
  created_at: string | null;
}

export interface PerPersonRhythm {
  user_id: string;
  user_name: string;
  total_count: number;
  weighted_amount_fen: number;
  stages: PerPersonStage[];
  advanced_standards: AdvancedStandard[];
  stuck: StuckItem[];
  ai_suggestions: AiSuggestion[];
}

/** 团队概览 = 周报 metrics 去掉 advanced_standards + 三个补充块 */
export interface MeetingTeamOverview {
  period_start: string;
  period_end: string;
  new_valid_count: number;
  new_lead_count: number;
  stage_distribution: FunnelItem[];
  in_progress_count: number;
  weighted_amount_fen: number;
  total_amount_fen: number;
  stuck_count: number;
  stuck_items: StuckItem[];
  signed_amount_fen: number;
  paid_amount_fen: number;
  conversion: ConversionPair[];
  overdue_first_follow: Lead[];
  team_targets: TeamTargetRow[];
  risk_counts: RiskCounts;
}

export interface WeeklyMeetingResp {
  period: { start: string; end: string; label: string };
  team_overview: MeetingTeamOverview;
  per_person: PerPersonRhythm[];
  pending_reviews: PendingReview[];
}

/* ---- 2.7 项目评审卡 ---- */

/** 评审卡 5 看的单项结论 */
export type ReviewResult = 'pass' | 'risk' | 'fail';
/** 真执行判定 */
export type ReviewVerdict = 'ability' | 'attitude' | 'normal';

/** 评审结果项（返回体） */
export interface ReviewItem {
  key: string;
  code: string;
  label: string;
  hint: string;
  result: ReviewResult | string | null;
  result_label: string;
  note: string;
}

/** 评审卡提交体里的单项 */
export interface ReviewItemInput {
  result: ReviewResult | string;
  note?: string;
}

export interface ProjectReview {
  id: string;
  lead_id: string;
  lead_name: string;
  stage: string | null;
  stage_label: string;
  reviewer_id?: string | null;
  reviewer_name: string;
  review_at: string;
  items: ReviewItem[];
  verdict: ReviewVerdict | string;
  verdict_label: string;
  conclusion: string | null;
  next_review_at: string | null;
  remark: string | null;
  created_at?: string | null;
}

/** 待评审清单项 */
export interface PendingReview {
  lead_id: string;
  lead_name: string;
  owner_name: string | null;
  stage: string | null;
  stage_label: string;
  stay_days: number;
  reason: string;
  last_review_at: string | null;
  last_verdict: ReviewVerdict | string | null;
  last_verdict_label: string | null;
  last_conclusion: string | null;
}

export interface PendingReviewResp {
  items: PendingReview[];
  total: number;
}

/** 提交评审卡 */
export interface ReviewCreateBody {
  lead_id: string;
  m1_decision_maker?: ReviewItemInput;
  m2_intent_budget?: ReviewItemInput;
  m3_demand_confirm?: ReviewItemInput;
  m4_stakeholders?: ReviewItemInput;
  m5_unknown_competitor?: ReviewItemInput;
  verdict: ReviewVerdict | string;
  conclusion?: string;
  next_review_at?: string;
  remark?: string;
}

/* ---- 2.6 回款 ---- */

export interface PaymentRecord {
  id: string;
  lead_id: string;
  lead_name: string;
  amount_fen: number;
  paid_date: string | null;
  method: string | null;
  method_label: string;
  period_no: string | null;
  period_label: string;
  remark: string | null;
  operator_name: string | null;
  created_at: string | null;
}

export interface PaymentUnpaidItem {
  lead_id: string;
  lead_name: string;
  owner_name: string | null;
  contract_amount_fen: number;
  paid_amount_fen: number;
  unpaid_amount_fen: number;
  sign_date: string | null;
  status: string;
  status_label: string;
}

export interface PaymentSummary {
  contracted_fen: number;
  paid_fen: number;
  unpaid_fen: number;
  collection_rate: number;
  period_paid_fen: number;
  period_count: number;
  unpaid_list: PaymentUnpaidItem[];
  signed_count: number;
}

export interface PaymentCreateBody {
  lead_id: string;
  amount_fen: number;
  paid_date?: string;
  method?: string;
  period_no?: string;
  remark?: string;
}

export interface PaymentQuery {
  lead_id?: string;
  owner_id?: string;
  start?: string;
  end?: string;
  page?: number;
  page_size?: number;
}

/* ============ P2 · 闭环三：交付与客户成功 ============ */

/** `/users/options` 返回的下拉项（id 为 uuid 字符串） */
export interface UserOption {
  id: string;
  name: string;
  username: string;
  role: string;
  role_label: string;
  department: string;
}

/* ---- 3.1 合同 ---- */

export interface ContractQuery {
  keyword?: string;
  status?: string;
  /** 运营交付专员（客户级一人，不区分合同数） */
  delivery_owner_id?: string;
  /** 到期天数阈值（15/30/60/90 天内到期） */
  expiring_days?: number;
  page?: number;
  page_size?: number;
}

export interface ContractOut {
  id: string;
  contract_no: string;
  lead_id: string;
  customer_name: string;
  delivery_owner_id: string | null;
  delivery_owner_name: string | null;
  sales_owner_name: string | null;
  /** 合同金额（整数分） */
  amount_fen: number | null;
  sign_date: string | null;
  service_start: string | null;
  service_end: string | null;
  /** 距服务期结束的剩余天数（负数表示已过期） */
  days_to_expire: number | null;
  acceptance_standard: string | null;
  has_hardware: boolean;
  status: string;
  status_label: string;
  /** 交付 10 阶段：当前阶段（1~10） */
  current_phase: number | string | null;
  current_phase_label: string | null;
  stage_done_count: number;
  stage_total_count: number;
  /** 交付进度（后端口径待确认，页面进度条按 stage_done/total 计算） */
  delivery_progress: number | null;
  handover_status: string;
  handover_status_label: string;
  renew_status: string;
  renew_status_label: string;
  latest_activity_rate: number | null;
  open_issue_count: number;
  open_complaint_count: number;
}

export interface ContractCreateBody {
  lead_id: string;
  contract_no: string;
  amount_fen: number;
  sign_date: string;
  service_start: string;
  service_end: string;
  acceptance_standard?: string;
  has_hardware?: boolean;
  attachment?: string;
  remark?: string;
}

export interface ContractUpdateBody {
  amount_fen?: number;
  sign_date?: string;
  service_start?: string;
  service_end?: string;
  acceptance_standard?: string;
  has_hardware?: boolean;
  attachment?: string;
  remark?: string;
  status?: string;
  delivery_owner_id?: string;
}

/* ---- 3.3 交付交接单（唯一一次真正的交接） ---- */

export interface HandoverOut {
  id: string;
  contract_id: string;
  customer_name: string;
  /** 移交人 = 销售 */
  handover_user_id: string | null;
  handover_user_name: string | null;
  receiver_id: string | null;
  receiver_name: string | null;
  delivery_owner_id: string | null;
  delivery_owner_name: string | null;
  handover_at: string | null;
  /** ① 标准《项目服务清单》核对说明 */
  standard_note: string | null;
  /** ② 合同非标内容澄清（三段式核心） */
  nonstandard_note: string | null;
  sales_confirmed: boolean;
  sales_confirmed_at: string | null;
  delivery_confirmed: boolean;
  delivery_confirmed_at: string | null;
  is_confirmed: boolean;
  remark: string | null;
  items: DeliveryItem[];
}

/** POST 为部分更新（upsert）：只传需要变更的字段即可，未传字段保持原值 */
export interface HandoverCreateBody {
  handover_user_id?: string;
  receiver_id?: string;
  delivery_owner_id?: string;
  handover_at?: string;
  standard_note?: string;
  nonstandard_note?: string;
  remark?: string;
}

export interface HandoverConfirmBody {
  role: 'sales' | 'delivery';
  remark?: string;
}

export interface HandoverResp {
  handover: HandoverOut | null;
}

/* ---- 3.4 交付物清单 ---- */

export interface DeliveryItem {
  id: string;
  contract_id: string;
  name: string;
  /**  后端为字符串（如 "3" / "1200 人"），前端按字符串原样展示 */
  quantity: string | null;
  unit: string | null;
  due_date: string | null;
  acceptance_standard: string | null;
  /** 是否标准《项目服务清单》项（false = 合同非标项） */
  is_standard: boolean;
  is_standard_label: string;
  sort_order?: number;
  remark: string | null;
  created_at: string | null;
}

export interface DeliveryItemCreateBody {
  name: string;
  /** 字符串（后端 schema 为 str，传数字会 422） */
  quantity?: string;
  unit?: string;
  due_date?: string;
  acceptance_standard?: string;
  /** 手工新增默认为非标项 */
  is_standard?: boolean;
  sort_order?: number;
  remark?: string;
}

export interface DeliveryItemUpdateBody extends Partial<DeliveryItemCreateBody> {}

/** 标准《项目服务清单》模板项 */
export interface DeliveryStandardItem {
  id?: string;
  name: string;
  unit: string | null;
  quantity?: string | null;
  acceptance_standard: string | null;
  sort_order: number;
}

export interface DeliveryItemGenerateResp {
  items: DeliveryItem[];
  /** 本次新生成的条数（幂等：已存在的不重复生成） */
  created: number;
}

/* ---- 3.5 交付工作台（10 阶段） ---- */

/** 6.6 交付阶段产出材料配置项 */
export interface DeliveryPhaseStandard {
  id: string;
  /** 1~10 */
  phase: number;
  phase_label: string;
  seq: string;
  content: string;
  is_required: boolean;
  /** 停留时长基准（天）；0 = 未设基准，不报交付延期 */
  duration_days: number;
  evidence_hint: string | null;
  sort_order: number;
  is_active: boolean;
}

export interface DeliveryPhaseStandardBody {
  phase: number;
  seq?: string;
  content: string;
  is_required?: boolean;
  duration_days?: number;
  evidence_hint?: string;
}

/** PATCH 结构：不含 phase（后端不支持改所属阶段），额外支持 sort_order / is_active */
export interface DeliveryPhaseStandardUpdateBody {
  seq?: string;
  content?: string;
  is_required?: boolean;
  duration_days?: number;
  evidence_hint?: string;
  sort_order?: number;
  is_active?: boolean;
}

/** 6.6 阶段汇总配置 */
export interface DeliveryPhaseConfigItem {
  phase: number;
  phase_label: string;
  duration_days: number;
  /** 是否已设停留时长基准（duration_days > 0） */
  has_baseline?: boolean;
  material_count: number;
}

/** 某条产出材料在某项目上的完成情况 */
export interface DeliveryStageOut {
  id: string;
  contract_id: string;
  phase: number;
  phase_label: string;
  standard_id: string;
  standard_content: string;
  is_done: boolean;
  evidence: string | null;
  done_by_name: string | null;
  done_at: string | null;
  remark: string | null;
  source: string;
}

export interface DeliveryMaterialItem {
  standard: DeliveryPhaseStandard;
  completion: DeliveryStageOut;
}

/** 单阶段进度（后端 phase_detail） */
export interface DeliveryStageProgress {
  phase: number;
  phase_label: string;
  /** 是否为当前阶段 */
  is_current?: boolean;
  stay_days: number;
  /** 停留时长基准；0 = 未设基准 */
  base_days: number;
  /** 是否已设基准（后端下发，等价于 base_days > 0） */
  has_baseline?: boolean;
  overdue: boolean;
  is_done: boolean;
  done_count: number;
  total_count: number;
  /** 是否已满足完成条件（必填材料是否齐全） */
  can_complete?: boolean;
  missing_required: string[];
  materials: DeliveryMaterialItem[];
}

/** 10 阶段汇总（详情页阶段条；后端 stages_summary） */
export interface DeliveryStageSummary {
  phase: number;
  phase_label: string;
  is_current?: boolean;
  is_done: boolean;
  /** 已完成的产出材料数 */
  done_count: number;
  /** 产出材料总数 */
  total_count: number;
  stay_days: number;
  base_days: number;
  has_baseline?: boolean;
  overdue: boolean;
}

export interface DeliveryStageToggleResp {
  completion: DeliveryStageOut;
  stage: DeliveryStageProgress;
}

/** 完成 / 重新打开阶段响应（stage 为操作后的「当前阶段」明细） */
export interface DeliveryStageCompleteResp {
  stage: DeliveryStageProgress;
  contract: ContractOut;
}

/* ---- 3.2 客户档案详情聚合 ---- */

export interface OperationRecordOut {
  id: string;
  lead_id: string;
  customer_name: string | null;
  contract_id: string | null;
  record_date: string | null;
  action_type: string;
  action_type_label: string;
  content: string;
  executor_id?: string | null;
  executor_name: string | null;
  next_plan: string | null;
  status: string;
  status_label: string;
  /** 后端为字符串（如 "5" / "满意"），原样展示 */
  satisfaction: string | null;
  created_at: string | null;
}

export interface OperationRecordCreateBody {
  lead_id: string;
  contract_id?: string;
  record_date: string;
  action_type: string;
  content: string;
  next_plan?: string;
  status?: string;
  /**  必须是字符串（后端 schema str，传数字会 422） */
  satisfaction?: string;
}

/** 运营指标（活跃率 = 登陆人数 ÷ 总账号数） */
export interface OperationMetric {
  id: string;
  lead_id?: string | null;
  contract_id?: string | null;
  customer_name?: string | null;
  /** day / week / month */
  period_type: string;
  period_type_label?: string | null;
  period: string;
  total_accounts: number;
  /** 登陆人数 */
  login_count: number;
  /** 活跃率（0-1 小数） */
  activity_rate: number;
  upload_count?: number | null;
  download_count?: number | null;
  file_count?: number | null;
  high_quality_file_count?: number | null;
  source?: string | null;
  batch_no?: string | null;
  imported_by_name?: string | null;
  created_at?: string | null;
}

/** 产品问题与销售承诺台账（3.7 只读回显） */
export interface ProductIssueOut {
  id: string;
  lead_id?: string | null;
  contract_id: string | null;
  customer_name: string | null;
  /** product_issue 产品问题 / promise 销售承诺 */
  type: string;
  type_label: string;
  content: string;
  source?: string | null;
  source_label?: string | null;
  proposer?: string | null;
  registrar_name?: string | null;
  registered_at?: string | null;
  /** 负责方：rnd 产研 / sales 销售 */
  handler_side?: string | null;
  handler_side_label?: string | null;
  assignee_id?: string | null;
  assignee_name?: string | null;
  due_date?: string | null;
  status: string;
  status_label: string;
  promised_date?: string | null;
  resolved_at?: string | null;
  replied_at?: string | null;
  resolve_hours?: number | null;
  is_overdue?: boolean;
  is_renewal_risk?: boolean;
  solution?: string | null;
  reply_note?: string | null;
  /**
   * 回复渠道（后端为自由文本 String(16)，**没有渠道字典**，故按界面中文原样存储）。
   * 老数据 / 未回复时为 null。
   */
  reply_channel?: string | null;
  /** 回复对象，如「教务处刘主任」 */
  reply_target?: string | null;
  /** 回复证据（必填留痕：消息截图说明 / 纪要编号 / 邮件主题） */
  reply_evidence?: string | null;
  remark?: string | null;
}

/** 投诉记录 */
export interface ComplaintOut {
  id: string;
  lead_id?: string | null;
  contract_id: string | null;
  customer_name: string | null;
  complainant?: string | null;
  receiver_id?: string | null;
  receiver_name?: string | null;
  received_at?: string | null;
  content: string;
  /** product 产品问题 / promise 承诺没兑现 / other 其他 */
  reason: string;
  reason_label: string;
  status: string;
  status_label: string;
  handle_result?: string | null;
  closed_at?: string | null;
  handle_hours?: number | null;
  is_overdue?: boolean;
  escalated?: boolean;
  remark?: string | null;
}

/** 验收记录 */
export interface AcceptanceOut {
  id: string;
  contract_id: string | null;
  contract_no?: string | null;
  customer_name?: string | null;
  status: string;
  status_label: string;
  apply_date?: string | null;
  meeting_date?: string | null;
  /** 验收结果 */
  result?: string | null;
  signed_date?: string | null;
  attachment?: string | null;
  rectify_note?: string | null;
  service_end?: string | null;
  days_to_expire?: number | null;
  remark?: string | null;
  created_at?: string | null;
}

/** 续费 / 增购评估（本期只读回显） */
export interface RenewalOut {
  id: string;
  contract_id: string | null;
  lead_id?: string | null;
  customer_name?: string | null;
  owner_name?: string | null;
  service_end?: string | null;
  days_to_expire?: number | null;
  assess_date?: string | null;
  activity_note?: string | null;
  satisfaction?: string | null;
  acceptance_result?: string | null;
  /** 续费意愿：high / medium / low */
  willingness?: string | null;
  willingness_label?: string | null;
  plan_note?: string | null;
  conclusion?: string | null;
  /** 预计续费/增购金额（整数分） */
  expected_amount_fen?: number | null;
  status: string;
  status_label: string;
  assessed_by_name?: string | null;
  created_at?: string | null;
}

/** GET /deliveries/{contract_id} 交付总览 */
export interface DeliveryOverview {
  contact_rhythm: { confirmed_at: string | null; last_contact_at: string | null;
    idle_days: number | null; threshold_days: number; overdue: boolean };
  contract: ContractOut;
  handover: HandoverOut | null;
  items: DeliveryItem[];
  progress: DeliveryStageProgress | null;
  stages_summary: DeliveryStageSummary[];
  operation_records: OperationRecordOut[];
  metrics: OperationMetric[];
  issues: ProductIssueOut[];
  complaints: ComplaintOut[];
  acceptances: AcceptanceOut[];
  renewals: RenewalOut[];
}

/* ============ P2-b · 闭环三：运营与客户成功（3.6~3.12 + 4.6） ============ */

/* ---- 通用分组计数（投诉原因 / 问题类型 / 来源 / 负责方分布） ---- */

export interface GroupCount {
  key: string;
  label: string;
  count: number;
}

/** 带占比的分组计数（投诉原因 / 流失原因 / 来源分布） */
export interface GroupCountRate extends GroupCount {
  rate: number;
}

/* ---- 3.7  产品问题与承诺台账 ---- */

export interface IssueQuery {
  contract_id?: string;
  lead_id?: string;
  /** product_issue 产品问题 / promise 销售承诺 */
  type?: string;
  status?: string;
  /** rnd 产研 / sales 销售 */
  handler_side?: string;
  assignee_id?: string;
  is_renewal_risk?: boolean;
  keyword?: string;
  /** 只看派给我的（产研默认只看自己的派单） */
  mine?: boolean;
  page?: number;
  page_size?: number;
}

/** 台账 TOP 客户（按登记条数倒序，最多 10 条） */
export interface IssueTopCustomer {
  customer_name: string;
  count: number;
  /** 其中未闭环（待处理 + 处理中）条数 */
  open: number;
}

export interface IssueStats {
  start: string | null;
  end: string | null;
  total: number;
  /** 待处理 + 处理中 */
  open_count: number;
  resolved_count: number;
  replied_count: number;
  renewal_risk_count: number;
  overdue_count: number;
  /** 超期未解决清单（最多 20 条，红色清单数据源） */
  overdue_items: ProductIssueOut[];
  avg_resolve_hours: number;
  by_type: GroupCount[];
  by_status: GroupCount[];
  by_source: GroupCount[];
  by_handler_side: GroupCount[];
  top_customers: IssueTopCustomer[];
}

export interface IssueCreateBody {
  lead_id: string;
  contract_id?: string;
  type: 'product_issue' | 'promise';
  content: string;
  source: 'complaint' | 'visit' | 'group' | 'acceptance';
  proposer?: string;
  /** 承诺兑现日期（type=promise 时后端必填，缺失返回 422） */
  promised_date?: string;
  is_renewal_risk?: boolean;
  remark?: string;
}

/** PATCH 为部分更新：只传需要变更的字段 */
export interface IssueUpdateBody {
  content?: string;
  source?: string;
  proposer?: string;
  handler_side?: string;
  due_date?: string;
  promised_date?: string;
  is_renewal_risk?: boolean;
  remark?: string;
}

export interface IssueAssignBody {
  handler_side: 'rnd' | 'sales';
  assignee_id?: string;
  /** 处理时限；不传则后端按负责方默认天数计算 */
  due_date?: string;
  remark?: string;
}

export interface IssueResolveBody {
  solution: string;
  remark?: string;
}

export interface IssueReplyBody {
  reply_note: string;
  /** 回复渠道（后端为自由文本 String(16)，无字典约束；界面提交中文原值） */
  reply_channel?: string;
  /** 回复对象 */
  reply_target?: string;
  /** 回复证据：**必填**（后端 min_length=1，缺失返回 422） */
  reply_evidence: string;
  is_renewal_risk?: boolean;
  remark?: string;
}

/* ---- 3.8 投诉管理 ---- */

export interface ComplaintQuery {
  contract_id?: string;
  lead_id?: string;
  status?: string;
  reason?: string;
  page?: number;
  page_size?: number;
}

export interface ComplaintStats {
  total: number;
  /** 待处理 + 处理中 */
  open_count: number;
  closed_count: number;
  escalated_count: number;
  /** 后端固定返回 product / commitment / other 三项（含 0 值） */
  by_reason: GroupCountRate[];
}

export interface ComplaintCreateBody {
  lead_id: string;
  contract_id?: string;
  complainant?: string;
  content: string;
  reason: 'product' | 'commitment' | 'other';
  /** ISO8601；不传则后端取当前时间 */
  received_at?: string;
  /** 接到人（默认 = 登记人） */
  handler_id?: string;
}

export interface ComplaintUpdateBody {
  content?: string;
  reason?: string;
  handle_result?: string;
  status?: string;
  remark?: string;
}

export interface ComplaintCloseBody {
  handle_result: string;
  remark?: string;
}

/* ---- 3.9 运营指标（活跃率） ---- */

export interface OperationMetricQuery {
  lead_id?: string;
  contract_id?: string;
  period_type?: string;
  period?: string;
  page?: number;
  page_size?: number;
}

export interface OperationMetricOverview {
  customer_count: number;
  total_accounts: number;
  /** 登陆人数合计 */
  total_login: number;
  /** 平均活跃率（0-1 小数） */
  avg_activity_rate: number;
  high_quality_file_count: number;
}

/** 排名行 = 指标行 + 名次 */
export interface OperationMetricRankRow extends OperationMetric {
  rank: number;
}

export interface OperationMetricTrendPoint {
  period: string;
  avg_activity_rate: number;
  login_count: number;
  customer_count: number;
}

export interface OperationMetricDashboard {
  period_type: string;
  period_type_label: string;
  period: string | null;
  overview: OperationMetricOverview;
  ranking: OperationMetricRankRow[];
  trend: OperationMetricTrendPoint[];
}

export interface OperationMetricCreateBody {
  lead_id: string;
  contract_id?: string;
  period_type: 'day' | 'week' | 'month';
  period: string;
  total_accounts: number;
  login_count: number;
  upload_count?: number;
  download_count?: number;
  file_count?: number;
  high_quality_file_count?: number;
}

/** PATCH：改数或人工标记高质量文件数量 */
export interface OperationMetricUpdateBody {
  total_accounts?: number;
  login_count?: number;
  upload_count?: number;
  download_count?: number;
  file_count?: number;
  high_quality_file_count?: number;
}

/** POST /operation-metrics/import 返回 */
export interface MetricImportError {
  row: number;
  message: string;
}

export interface MetricImportResult {
  batch_no: string;
  total: number;
  success: number;
  failed: number;
  errors: MetricImportError[];
}

/* ---- 3.10 回访记录（运营记录） ---- */

export interface OperationRecordQuery {
  lead_id?: string;
  contract_id?: string;
  action_type?: string;
  status?: string;
  start?: string;
  end?: string;
  page?: number;
  page_size?: number;
}

export interface OperationRecordUpdateBody {
  record_date?: string;
  action_type?: string;
  content?: string;
  next_plan?: string;
  status?: string;
  /**  字符串（后端 schema 为 str） */
  satisfaction?: string;
}

/* ---- 3.6 服务支持：FAQ ---- */

export interface FaqOut {
  id: string;
  question: string;
  answer: string;
  category: string | null;
  /** 标准版（官方口径） */
  is_standard: boolean;
  tags: string[];
  view_count: number;
  updated_by_name: string | null;
  updated_at: string | null;
}

export interface FaqCreateBody {
  question: string;
  answer?: string;
  category?: string;
  is_standard?: boolean;
  tags?: string[];
}

export interface FaqUpdateBody {
  question?: string;
  answer?: string;
  category?: string;
  is_standard?: boolean;
  tags?: string[];
}

/* ---- 3.11 验收管理 ---- */

export interface AcceptanceQuery {
  contract_id?: string;
  status?: string;
  page?: number;
  page_size?: number;
}

/** GET /acceptances/due 项（与 AcceptanceOut 不同，字段更精简） */
export interface AcceptanceDueItem {
  contract_id: string;
  contract_no: string | null;
  customer_name: string;
  delivery_owner_name: string | null;
  service_end: string;
  /** 剩余自然日（可为负） */
  days_left: number;
  /** 剩余工作日（已过期返回 0） */
  workdays_left: number;
  status: string;
  status_label: string;
  apply_date: string | null;
  /** 已有验收记录 id；为 null 时「发起验收」走 POST /acceptances */
  acceptance_id: string | null;
  /** 剩余工作日 ≤5（需标红） */
  urgent: boolean;
}

export interface AcceptancePassBody {
  /** 客户签署确认书日期（必填） */
  signed_date: string;
  meeting_date?: string;
  attachment?: string;
  remark?: string;
}

export interface AcceptanceFailBody {
  rectify_note: string;
}

export interface AcceptanceUpdateBody {
  apply_date?: string;
  meeting_date?: string;
  signed_date?: string;
  attachment?: string;
  rectify_note?: string;
  remark?: string;
}

/* ---- 3.12 续费与增购 ---- */

export interface RenewalQuery {
  contract_id?: string;
  status?: string;
  page?: number;
  page_size?: number;
}

export interface RenewalStats {
  assessed_count: number;
  renewed_count: number;
  lost_count: number;
  in_progress_count: number;
  pending_count: number;
  /** 续费率 = 已续费 ÷（已续费 + 已流失），无结论时为 0 */
  renew_rate: number;
  /** SaaS 基准 0.85 */
  baseline: number;
  /** 与基准的差距（可为负） */
  gap: number;
  expected_amount_fen: number;
}

/** GET /renewals/due 项（与 RenewalOut 不同：附带活跃率 / 验收 / 未闭环问题数） */
export interface RenewalDueItem {
  contract_id: string;
  contract_no: string | null;
  customer_name: string;
  owner_name: string | null;
  service_end: string;
  days_left: number;
  /** 合同金额（整数分） */
  amount_fen: number | null;
  activity_rate: number | null;
  acceptance_status: string;
  acceptance_status_label: string;
  open_issue_count: number;
  /** 当前口径与 open_issue_count 相同（后端占位） */
  risk_count: number;
  willingness: string | null;
  willingness_label: string | null;
  status: string;
  status_label: string;
  /** 已有评估 id；为 null 时「评估」走 POST /renewals */
  renewal_id: string | null;
  expected_amount_fen: number;
  /** 剩余天数 ≤30 */
  urgent: boolean;
}

export interface RenewalCreateBody {
  contract_id: string;
  assess_date?: string;
  activity_note?: string;
  satisfaction?: string;
  acceptance_result?: string;
  willingness: 'high' | 'mid' | 'low';
  plan_note?: string;
  conclusion?: string;
  /** 整数分 */
  expected_amount_fen?: number;
  status?: string;
}

export interface RenewalUpdateBody {
  assess_date?: string;
  activity_note?: string;
  satisfaction?: string;
  acceptance_result?: string;
  willingness?: string;
  plan_note?: string;
  conclusion?: string;
  expected_amount_fen?: number;
  status?: string;
}

export interface RenewalConcludeBody {
  status: 'pending' | 'in_progress' | 'renewed' | 'lost';
  conclusion?: string;
  expected_amount_fen?: number;
}

/* ---- 4.6 运营复盘 ---- */

/** 交付延期行（阶段停留超期；基准未设时为空） */
export interface DeliveryDelayRow {
  contract_id: string;
  customer_name: string;
  phase: number | string | null;
  phase_label: string;
  stay_days: number;
  base_days: number;
}

export interface OperationReviewActivity {
  customer_count: number;
  avg_activity_rate: number;
  /** 低活跃客户（后端按 20% 口径取，最多 20 条，仅展示不告警） */
  low_activity: OperationMetric[];
  note: string;
}

export interface OperationReviewResp {
  period: string;
  renewal: RenewalStats;
  /** R1–R5 流失原因（按条数倒序） */
  churn_reasons: GroupCountRate[];
  lost_lead_count: number;
  complaints: ComplaintStats;
  product_issues: IssueStats;
  delivery_delay: DeliveryDelayRow[];
  /** 非空时用 Alert 展示（基准未设说明） */
  delivery_delay_note: string;
  activity: OperationReviewActivity;
}

/* ============ 收尾页 · 4.1 经营大盘 ============ */

/** 大盘待办：管理层最该先看的几件事（可点进对应页面处理） */
export interface OverviewTodo {
  kind: 'acceptance_due' | 'renewal_due' | 'stuck' | 'issue_overdue' | string;
  kind_label: string;
  level: 'error' | 'warning' | 'info';
  subject: string;
  detail: string;
  owner: string | null;
  lead_id?: string | null;
  /** 前端跳转路径 */
  route: string;
}

/** 大盘头条指标 */
export interface OverviewHeadline {
  lead_total: number;
  new_leads: number;
  new_leads_week: number;
  /** 0–1 */
  valid_rate: number;
  in_progress_count: number;
  total_amount_fen: number;
  total_amount_wan: number;
  weighted_amount_fen: number;
  weighted_amount_wan: number;
  signed_amount_fen: number;
  signed_amount_wan: number;
  paid_amount_fen: number;
  paid_amount_wan: number;
  paid_rate: number;
  executing_contracts: number;
  stuck_count: number;
  pending_alerts: number;
  avg_activity_rate: number;
}

/** 4.1 经营大盘响应（口径与各明细页一致） */
export interface BusinessOverview {
  generated_at: string;
  window_days: number;
  window_start: string;
  headline: OverviewHeadline;
  leads: {
    total: number;
    new_in_window: number;
    new_week: number;
    invalid: number;
    valid_rate: number;
    by_source: { source: string; label: string; count: number }[];
  };
  opportunities: {
    funnel: FunnelItem[];
    total_amount_fen: number;
    weighted_amount_fen: number;
    baseline: Record<string, number>;
    stuck: Lead[];
    long_running: Lead[];
    alert_total: number;
    conversion: ConversionResp;
    advanced_standards_week: number;
  };
  revenue: {
    signed_amount_fen: number;
    signed_amount_wan: number;
    paid_amount_fen: number;
    paid_amount_wan: number;
    paid_rate: number;
    signed_count: number;
  };
  delivery: {
    executing_contracts: number;
    by_phase: { phase: number; label: string; count: number }[];
    acceptance_due: AcceptanceDueItem[];
    renewal_due: RenewalDueItem[];
    renewal_stats: RenewalStats;
    alert_count: number;
  };
  operation: {
    customer_count: number;
    total_accounts: number;
    total_login: number;
    avg_activity_rate: number;
    open_issues: number;
    renewal_risk_issues: number;
    open_complaints: number;
    avg_resolve_hours: number | null;
    top_issue_customers: { customer_name: string; count: number; open?: number }[] | null;
  };
  alerts: { pending_total: number; sales: number; delivery: number };
  todos: OverviewTodo[];
  todo_total: number;
  /** 距年末还剩多少个工作日（按库里的节假日/调休算） */
  working_days_to_year_end: number | null;
  note: string;
}

/* ============ 收尾页 · 6.2 角色与权限 ============ */

/** 一条能力（含每个角色是否允许的完整矩阵） */
export interface RoleCapability {
  code: string;
  name: string;
  note: string;
  roles: string[];
  matrix: Record<string, boolean>;
}

export interface RoleSummary {
  role: string;
  label: string;
  default_scope: string;
  default_scope_label: string;
  capability_count: number;
  user_count: number;
}

export interface RoleMatrixResp {
  roles: RoleSummary[];
  capabilities: RoleCapability[];
  role_columns: { role: string; label: string; user_count: number }[];
  scope_summary: { scope: string; label: string; user_count: number }[];
  total_users: number;
  note: string;
}

export interface MyCapabilitiesResp {
  role: string;
  data_scope: string;
  capabilities: string[];
}

/* ============ 收尾页 · 6.3 部门管理 ============ */

export interface DepartmentNode {
  id: string;
  name: string;
  parent_id: string | null;
  sort_order: number;
  leader_name: string | null;
  remark: string | null;
  user_count: number;
  children: DepartmentNode[];
  created_at: string | null;
}

export interface DepartmentTreeResp {
  items: DepartmentNode[];
  total: number;
  total_users: number;
  /** 有账号但未建部门的名字（据此补建） */
  unmatched_departments: string[];
  note: string;
}

/* ============ 收尾页 · 6.8 通知模板 ============ */

export interface NotificationTemplate {
  id: string;
  code: string;
  name: string;
  channel: string;
  channel_label: string;
  /** 绑定的业务事件（与后端 notify 的 type_ 对齐） */
  event: string;
  event_label: string;
  title_tpl: string;
  content_tpl: string;
  /** 模板里用到的变量名（自动抽取） */
  variables: string[];
  /**
   * 该事件的模板是否**真的影响发送**（由后端权威清单 `WIRED_NOTIFY_EVENTS` 判定，有测试守护）。
   * true = 该事件发站内通知时会套用本条模板；false = 该事件当前不发站内通知（模板属预留）。
   */
  wired: boolean;
  /** 后端给出的、可直接展示的说明（已接线 / 预留的原因），页面照用，避免与后端漂移 */
  wired_note: string;
  is_active: boolean;
  remark: string | null;
  updated_at: string | null;
}

/* ============ 收尾页 · 6.10 适配配置 ============ */

export interface AdapterStatus {
  model: {
    provider: string | null;
    settings_file: string | null;
    profiles: Record<string, { model: string | null; base_url: string | null; auth_present: boolean }> | null;
    configured: boolean;
    degrade_policy: string | null;
    note: string;
  };
  im_adapters: { code: string; label: string; configured: boolean; env: string }[];
  im_ready: boolean;
  embedding: { configured: boolean; model: string; dimension: number };
  knowledge_base: {
    configured: boolean;
    collection: string;
    persist_dir: string;
    retrieval_top_k: number;
  };
  upload: { max_size_mb: number; extensions: string[] };
  note: string;
}

/* ============ 客户-项目两层模型 ============ */

/**
 * 客户级指标（后端 `customer_service.metrics_for` 输出，列表行与详情 `metrics` 同结构）。
 *
 * 口径（写在数值旁边，界面上必须可见）：
 * * `in_progress_value_fen` = **过程中价值** = 未签约项目金额合计（「已丢单」剔除，不算价值）
 * * `signed_value_fen` = 已签约按**合同额**计（合同额为空回落到预算额）
 * * `lost_value_fen` = 已丢单金额，**仅展示**，不计入任何价值口径
 */
export interface CustomerMetrics {
  project_count: number;
  in_progress_count: number;
  signed_count: number;
  lost_count: number;
  in_progress_value_fen: number;
  signed_value_fen: number;
  lost_value_fen: number;
  pending_risk_count: number;
  /** 按「当前环节」拆分的金额（含已签约，`signed` 标记该环节项目是否全部签约） */
  stages: CustomerStageBucket[];
}

/** 客户详情里的按阶段金额拆分（`metrics.stages`） */
export interface CustomerStageBucket {
  /** 环节 code，如 `s2_customer_reached`；未进入环节时为 `not_in_stage` */
  stage: string;
  /** 后端下发的环节中文名（如「二 客户触达」） */
  stage_label: string;
  amount_fen: number;
  project_count: number;
  signed: boolean;
}

/** 客户（学校 / 教育局 / 机构）：一个客户名下可挂多个项目商机 */
export interface Customer extends CustomerMetrics {
  id: string;
  name: string;
  province: string;
  city: string;
  district: string;
  /** 省市区拼接（后端直接下发，避免前端各省市拼接口径不一致） */
  region: string;
  org_type: string;
  org_type_label: string;
  owner_id: string | null;
  owner_name: string | null;
  /** 客户等级 A/B/C（选填） */
  level: string | null;
  remark: string | null;
  created_at: string | null;
}

/** 客户列表响应（含合计 summary 与口径说明 value_note） */
export interface CustomerListResp {
  items: Customer[];
  total: number;
  page: number;
  page_size: number;
  summary: {
    customer_count: number;
    project_count: number;
    in_progress_value_fen: number;
    signed_value_fen: number;
    value_note: string;
  };
  value_note: string;
}

/** 客户详情里的一个项目（= 一条商机，各自走 7 环节、各有金额） */
export interface CustomerProject {
  id: string;
  /** 客户名（= 该商机的 lead.name） */
  name: string;
  /** 项目名，可能为空串（历史数据） */
  project_name: string;
  status: string;
  status_label: string;
  current_stage: string | null;
  current_stage_label: string;
  /** 该项目的金额：已签约用合同额，其余用预算额（整数分） */
  amount_fen: number;
  budget_amount_fen: number;
  contract_amount_fen: number;
  /** true = 未签约 → 计入「过程中价值」 */
  is_process_value: boolean;
  /** 「过程中价值」/「已签约」/「已丢单」（后端口径，前端照用） */
  value_label: string;
  owner_name: string | null;
  city: string;
  district: string;
  source_label: string;
}

/** 客户级跟进（不进任何项目环节、不计环节时长） */
export interface CustomerFollowUp {
  id: string;
  /** 固定为 `customer`（与项目级跟进区分） */
  scope?: string;
  /** 「客户级（不进环节）」 */
  scope_label?: string;
  user_name: string;
  method: string;
  method_label: string;
  content: string;
  followed_at: string | null;
  next_follow_at: string | null;
  /** 仅 POST 返回的说明：客户级记录不推进任何项目环节 */
  note?: string;
}

/** 客户详情里的风险条目（按 `GET /customers/{id}` 实际下发字段建模） */
export interface CustomerRisk {
  id: string;
  /** 该风险属于哪个项目（lead id） */
  lead_id: string;
  kind: string;
  kind_label: string;
  risk_type: string;
  risk_type_label: string;
  status: string;
  trigger_rule: string | null;
  /** 风险描述（后端 2026-10-10 20:07 起下发） */
  description?: string | null;
  /** 处理说明（已处理时有值） */
  handle_note?: string | null;
  created_at: string | null;
}

/** 客户详情里的合同条目 */
export interface CustomerContract {
  id: string;
  contract_no: string;
  amount_fen: number;
  status: string;
}

/** 客户详情聚合响应 `GET /customers/{id}` */
export interface CustomerDetailResp {
  customer: Customer;
  metrics: CustomerMetrics;
  projects: CustomerProject[];
  contacts: Contact[];
  followups: CustomerFollowUp[];
  followup_counts: {
    customer_level: number;
    /** key = 项目（lead）id */
    by_project: Record<string, number>;
  };
  risks: CustomerRisk[];
  contracts: CustomerContract[];
}

/** `GET /customers/of-lead/{lead_id}`：该项目属于哪个客户 + 同客户其他项目 */
export interface CustomerOfLeadResp {
  customer: Customer | null;
  metrics: CustomerMetrics | Record<string, never>;
  siblings: CustomerProject[];
}

/** 新增客户级跟进的请求体（content 必填；日期一律 `YYYY-MM-DD`，禁用 toISOString） */
export interface CustomerFollowUpCreateBody {
  method: string;
  content: string;
  followed_at?: string;
  next_follow_at?: string;
}

/** 维护客户信息（等级 / 备注 / 负责人） */
export interface CustomerUpdateBody {
  level?: string | null;
  remark?: string | null;
  owner_id?: string | null;
}
