/**
 * 客户-项目两层模型接口。
 *
 * 口径提醒（页面必须就地展示，判据 C3）：
 * * **过程中价值** = 未签约项目金额合计；已签约按**合同额**计；已丢单金额已剔除。
 * * 金额一律**整数分**（`*_fen`），展示统一走 `utils/format.ts` 的 `fenToWan`/`formatYuan`。
 * * 客户级跟进**不进任何项目环节、不计环节时长**，与项目级跟进是两套东西。
 */
import { get, patch, post } from './client';
import type {
  CustomerDetailResp,
  CustomerFollowUp,
  CustomerFollowUpCreateBody,
  CustomerListResp,
  CustomerOfLeadResp,
  CustomerUpdateBody,
  Customer,
} from '@/types';

/** 客户列表（含 summary 合计与 value_note 口径说明） */
export function listCustomers(params: {
  keyword?: string;
  only_mine?: boolean;
  page?: number;
  page_size?: number;
}) {
  return get<CustomerListResp>('/customers', params);
}

/**
 * 客户详情聚合：项目清单 / 按阶段金额 / 跨项目跟进 / 风险 / 合同 / 联系人。
 *
 * 说明：该接口在开发过程中一度对「名下有未处理风险的客户」返回 500（后端读了 RiskAlert 上
 * 不存在的 `level` 字段），后端已修（风险块改为下发 status/trigger_rule/description/handle_note）。
 * 2026-10-10 实测 20/20 客户详情全部 200，故前端**不再做降级兜底**，失败即按错误态处理
 * （页面给"为什么 + 怎么办"），避免多打一次列表接口、也避免两套渲染口径。
 */
export function getCustomerDetail(customerId: string) {
  return get<CustomerDetailResp>(`/customers/${customerId}`);
}

/** 该项目属于哪个客户 + 同客户其他项目（商机详情页用） */
export function getCustomerOfLead(leadId: string) {
  return get<CustomerOfLeadResp>(`/customers/of-lead/${leadId}`);
}

/**
 * 新增**客户级**跟进。
 *
 *  `followed_at` 只传 `YYYY-MM-DD`（调用方用 `dayjs(v).format('YYYY-MM-DD')`）。
 * 本仓踩过「`toISOString()` 造成北京时间差一天」的坑，禁止用它提交业务日期。
 */
export function createCustomerFollowup(customerId: string, body: CustomerFollowUpCreateBody) {
  return post<{ ok: boolean; followup: CustomerFollowUp }>(
    `/customers/${customerId}/followups`,
    body,
  );
}

/**
 * 维护客户信息（等级 / 备注 / 负责人）。
 *
 *  后端该接口用的是 `require_admin`（仅管理员）；非管理员点保存会拿到 403 提示。
 * 页面按此把入口只给管理员，避免"点了才知道不行"（判据 C2）。
 */
export function updateCustomer(customerId: string, body: CustomerUpdateBody) {
  return patch<{ ok: boolean; customer: Customer }>(`/customers/${customerId}`, body);
}
