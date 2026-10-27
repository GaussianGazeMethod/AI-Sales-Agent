import { client, del, get, patch, post } from './client';
import type {
  Assignment,
  Contact,
  DuplicateWarning,
  FollowUp,
  Lead,
  LeadCreateBody,
  LeadCreateResp,
  LeadDetailResp,
  LeadQuery,
  ListResp,
  LoadViewRow,
  OperationLog,
} from '@/types';

/** 线索列表（多子视图 + 多维筛选，服务端分页） */
export function listLeads(params: LeadQuery) {
  return get<ListResp<Lead>>('/leads', params);
}

/** 单条线索 */
export function getLead(id: string) {
  return get<LeadDetailResp>(`/leads/${id}`).then((detail) => detail.lead);
}

/** 线索详情聚合（含联系人/跟进/分配/提醒/日志） */
export function getLeadDetail(id: string) {
  return get<LeadDetailResp>(`/leads/${id}`);
}

/** 新增线索，返回撞单提示 */
export function createLead(body: LeadCreateBody) {
  return post<LeadCreateResp>('/leads', body);
}

/** 编辑线索（任意可改字段） */
export function updateLead(id: string, body: Partial<Lead>) {
  return patch<Lead>(`/leads/${id}`, body);
}

/** 软删除（仅管理员） */
export function deleteLead(id: string) {
  return del<{ ok: boolean }>(`/leads/${id}`);
}

/** 无效商机复活为跟进中 */
export function reviveLead(id: string, remark: string) {
  return post<{ lead: Lead }>(`/leads/${id}/revive`, { remark });
}

/** 录入时实时查重（客户名称 + 省市区） */
export function checkDuplicate(body: {
  name: string;
  province?: string;
  city?: string;
  district?: string;
}) {
  return post<DuplicateWarning>('/leads/check-duplicate', body);
}

/** 疑似重复池 */
export function listDuplicates() {
  return get<{ items: Lead[]; total: number }>('/leads/duplicates');
}

/** 裁决：保留 / 合并 */
export function resolveDuplicate(body: {
  keep_id: string;
  merge_id: string;
  action: 'keep' | 'merge';
  remark: string;
}) {
  return post<{ ok: boolean }>('/leads/duplicates/resolve', body);
}

/** 负载视图（各人手上条数） */
export function loadView() {
  return get<{ items: LoadViewRow[]; total: number }>('/leads/load-view');
}

/** 批量指派 / 改派 */
export function assignLeads(body: { lead_ids: string[]; owner_id: string; remark: string }) {
  return post<{ ok: boolean; affected: number }>('/leads/assign', body);
}

/** 批量回收至公海 */
export function recycleLeads(body: { lead_ids: string[]; remark: string }) {
  return post<{ ok: boolean; affected: number }>('/leads/recycle', body);
}

/** 公海重新领取 */
export function claimLead(id: string) {
  return post<{ lead: Lead }>(`/leads/${id}/claim`);
}

/* ---- 联系人 ---- */

export function listContacts(leadId: string) {
  return get<ListResp<Contact>>(`/leads/${leadId}/contacts`);
}

export function createContact(leadId: string, body: Partial<Contact>) {
  return post<Contact>(`/leads/${leadId}/contacts`, body);
}

export function updateContact(id: string, body: Partial<Contact>) {
  return patch<Contact>(`/contacts/${id}`, body);
}

export function deleteContact(id: string) {
  return del<{ ok: boolean }>(`/contacts/${id}`);
}

/* ---- 跟进记录 ---- */

export function listFollowups(leadId: string) {
  return get<ListResp<FollowUp>>(`/leads/${leadId}/followups`);
}

/**
 * 新增跟进：后端自动判定有效性
 * validity.is_valid 为判定结果，reason 为判定原因
 */
export function createFollowup(
  leadId: string,
  body: {
    method: string;
    followed_at: string;
    content: string;
    is_valid: 'valid' | 'invalid';
    next_action: string;
    /** 「客户信息是否核实正确」——判有效的前提，必须显式传（后端不再默认 true） */
    info_verified: boolean;
    next_follow_at?: string | null;
  },
) {
  return post<{
    followup: FollowUp;
    lead: Lead;
    validity: { is_valid: boolean; reason: string };
  }>(`/leads/${leadId}/followups`, body);
}

/* ---- 分配记录 / 日志（详情页聚合里也有，单独接口做兜底） ---- */

export function listAssignments(leadId: string) {
  return get<ListResp<Assignment>>(`/leads/${leadId}/assignments`);
}

export function listLeadLogs(leadId: string) {
  return get<ListResp<OperationLog>>(`/leads/${leadId}/logs`);
}

/* ---- 导入 ---- */

export interface ImportBatch {
  id: string;
  file_name: string;
  importer_name?: string | null;
  total: number;
  success: number;
  failed: number;
  batch_no?: string;
  created_at: string;
  errors?: { row: number; message: string }[];
}

export function listImportBatches(params?: { page?: number; page_size?: number }) {
  return get<ListResp<ImportBatch>>('/imports/batches', params);
}

/** Excel 导入（multipart，字段名 file） */
export async function importLeads(file: File) {
  const form = new FormData();
  form.append('file', file);
  const { data } = await client.post<{
    batch: ImportBatch;
    total: number;
    success: number;
    failed: number;
    errors: { row: number; message: string }[];
  }>('/imports/leads', form, { headers: { 'Content-Type': 'multipart/form-data' } });
  return data;
}

/** 模板下载地址（可直接 <a href> 打开） */
export const IMPORT_TEMPLATE_URL = '/api/v1/imports/template';
