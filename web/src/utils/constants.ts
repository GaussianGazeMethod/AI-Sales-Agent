/** 业务常量（后端返回 value 时前端展示兜底，字典接口优先） */

export const ROLE_LABEL: Record<string, string> = {
  admin: '管理员',
  market_staff: '销售人员',
  market_manager: '管理人员',
  delivery_staff: '运营交付专员',
  delivery_manager: '运营部管理人员',
  executive: '管理层',
  rnd: '产研',
  biz_support: '方案商务部门',
};

export const STATUS_LABEL: Record<string, string> = {
  unassigned: '未指派',
  assigned: '已指派',
  following: '跟进中',
  valid: '有效商机',
  invalid: '无效商机',
  lost: '已丢单',
  signed: '已签约',
};

export const METRIC_LABEL: Record<string, string> = {
  valid_lead_count: '有效商机数',
  new_lead_count: '新增线索数',
  valid_rate: '有效率',
};

export const METRIC_OPTIONS = [
  { value: 'valid_lead_count', label: '有效商机数' },
  { value: 'new_lead_count', label: '新增线索数' },
  { value: 'valid_rate', label: '有效率（0-1）' },
];

export const ASSIGN_ACTION_LABEL: Record<string, string> = {
  assign: '指派',
  reassign: '改派',
  recycle: '回收',
  claim: '重新领取',
};

/** 空态文案 */
export const EMPTY_TEXT = '暂无数据';
