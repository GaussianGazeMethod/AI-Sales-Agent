/**
 * 闭环三 · 交付与客户成功接口
 * 覆盖：合同（3.1/3.2）、交付交接单（3.3）、交付物清单（3.4）、
 *      交付工作台 10 阶段（3.5）、交付阶段产出材料配置（6.6）、运营记录（3.2）。
 *
 * 约定：ID 一律 string，金额一律整数分（*_fen）。
 */
import { del, get, patch, post } from './client';
import type {
  ContractCreateBody,
  ContractOut,
  ContractQuery,
  ContractUpdateBody,
  DeliveryItem,
  DeliveryItemCreateBody,
  DeliveryItemGenerateResp,
  DeliveryItemUpdateBody,
  DeliveryOverview,
  DeliveryPhaseConfigItem,
  DeliveryPhaseStandard,
  DeliveryPhaseStandardBody,
  DeliveryPhaseStandardUpdateBody,
  DeliveryStageCompleteResp,
  DeliveryStageProgress,
  DeliveryStageToggleResp,
  DeliveryStandardItem,
  HandoverConfirmBody,
  HandoverCreateBody,
  HandoverOut,
  HandoverResp,
  ListResp,
  OperationRecordCreateBody,
  OperationRecordOut,
  UserOption,
} from '@/types';

/* ==================== 下拉选项 ==================== */

/** 运营交付专员下拉（客户级归属一人，不区分合同数） */
export function listDeliveryOwnerOptions() {
  return get<{ items: UserOption[]; total: number }>('/users/options', {
    roles: 'delivery_staff,delivery_manager',
  });
}

/** 全部人员下拉（交接单的移交人 = 销售 / 接收人） */
export function listUserOptions(roles?: string) {
  return get<{ items: UserOption[]; total: number }>('/users/options', roles ? { roles } : undefined);
}

/* ==================== 3.1 / 3.2 合同 ==================== */

/** 合同（客户）列表：支持关键词 / 状态 / 运营交付专员 / 到期天数筛选 */
export function listContracts(params: ContractQuery) {
  return get<ListResp<ContractOut>>('/contracts', params);
}

/** 补录合同（签约在环节七，此接口用于历史合同补录） */
export function createContract(body: ContractCreateBody) {
  return post<ContractOut>('/contracts', body);
}

/** 修改合同（含指派/调整运营交付专员 delivery_owner_id） */
export function updateContract(id: string, body: ContractUpdateBody) {
  return patch<ContractOut>(`/contracts/${id}`, body);
}

/* ==================== 3.3 交付交接单（唯一一次交接） ==================== */

/** 交接单（未创建时 handover 为 null；后端返回体为 {handover: ...}） */
export function getHandover(contractId: string) {
  return get<HandoverResp>(`/contracts/${contractId}/handover`);
}

/** 新建 / 更新交接单（后端 upsert）：销售 → 运营交付部 */
export function createHandover(contractId: string, body: HandoverCreateBody) {
  return post<HandoverOut>(`/contracts/${contractId}/handover`, body);
}

/** 双方确认（role = sales 销售方 / delivery 交付方），两方都确认后 is_confirmed = true */
export function confirmHandover(handoverId: string, body: HandoverConfirmBody) {
  return post<HandoverOut>(`/handovers/${handoverId}/confirm`, body);
}

/* ==================== 3.4 交付物清单 ==================== */

export function listDeliveryItems(contractId: string) {
  return get<ListResp<DeliveryItem>>(`/contracts/${contractId}/delivery-items`);
}

export function createDeliveryItem(contractId: string, body: DeliveryItemCreateBody) {
  return post<DeliveryItem>(`/contracts/${contractId}/delivery-items`, body);
}

export function updateDeliveryItem(id: string, body: DeliveryItemUpdateBody) {
  return patch<DeliveryItem>(`/delivery-items/${id}`, body);
}

export function deleteDeliveryItem(id: string) {
  return del<{ ok: boolean }>(`/delivery-items/${id}`);
}

/** 一键生成标准《项目服务清单》项（幂等，已存在的不重复生成） */
export function generateStandardItems(contractId: string) {
  return post<DeliveryItemGenerateResp>(`/contracts/${contractId}/delivery-items/generate-standard`);
}

/** 标准《项目服务清单》模板项 */
export function listStandardItems() {
  return get<ListResp<DeliveryStandardItem>>('/delivery-standard-items');
}

/* ==================== 3.5 交付工作台（10 阶段） ==================== */

/** 交付总览（3.2 客户档案详情 + 3.5 工作台共用） */
export function getDeliveryOverview(contractId: string) {
  return get<DeliveryOverview>(`/deliveries/${contractId}`);
}

/** 单阶段明细 */
export function getDeliveryStage(contractId: string, phase: number | string) {
  return get<DeliveryStageProgress>(`/deliveries/${contractId}/stages/${phase}`);
}

/**
 * 勾选 / 取消某条产出材料。
 *  is_done = true 时 evidence 必填，后端缺证据返回 422。
 */
export function toggleStageMaterial(
  contractId: string,
  phase: number | string,
  standardId: string,
  body: { is_done: boolean; evidence?: string; remark?: string },
) {
  return post<DeliveryStageToggleResp>(
    `/deliveries/${contractId}/stages/${phase}/materials/${standardId}/toggle`,
    body,
  );
}

/** 完成本阶段（必填材料未完成时后端返回 409，detail 为提示语） */
export function completeStage(contractId: string, phase: number | string, remark?: string) {
  return post<DeliveryStageCompleteResp>(
    `/deliveries/${contractId}/stages/${phase}/complete`,
    { remark },
  );
}

/** 重新打开本阶段 */
export function reopenStage(contractId: string, phase: number | string, remark?: string) {
  return post<DeliveryStageCompleteResp>(
    `/deliveries/${contractId}/stages/${phase}/reopen`,
    { remark },
  );
}

/* ==================== 6.6 交付阶段产出材料配置 ==================== */

/** 产出材料清单（可按阶段过滤） */
export function listPhaseStandards(phase?: number) {
  return get<ListResp<DeliveryPhaseStandard>>(
    '/delivery-phase-standards',
    phase ? { phase } : undefined,
  );
}

/** 新增产出材料（仅 admin / delivery_manager） */
export function createPhaseStandard(body: DeliveryPhaseStandardBody) {
  return post<DeliveryPhaseStandard>('/delivery-phase-standards', body);
}

/** 修改产出材料（内容 / 是否必填 / 停留时长基准 / 证据提示 / 启停） */
export function updatePhaseStandard(id: string, body: DeliveryPhaseStandardUpdateBody) {
  return patch<DeliveryPhaseStandard>(`/delivery-phase-standards/${id}`, body);
}

/** 10 阶段汇总（阶段名称 + 停留时长基准 + 材料条数） */
export function getPhaseConfig() {
  return get<ListResp<DeliveryPhaseConfigItem>>('/delivery-phase-config');
}

/* ==================== 3.2 运营记录 ==================== */

/** 新增运营记录（回访 / 培训 / 数据报告 / 活动运营 / 需求收集 / 投诉跟进） */
export function createOperationRecord(body: OperationRecordCreateBody) {
  return post<OperationRecordOut>('/operation-records', body);
}
