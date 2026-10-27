import { del, get, patch, post } from './client';
import type {
  AdapterStatus,
  DepartmentNode,
  DepartmentTreeResp,
  ListResp,
  MyCapabilitiesResp,
  NotificationTemplate,
  OperationLog,
  Reminder,
  RoleMatrixResp,
  StageStandard,
} from '@/types';

/* ---- 待办动作 ---- */

/** 单条完成 */
export function doneReminder(id: string) {
  return post<{ ok: boolean }>(`/reminders/${id}/done`);
}

/** 批量完成 */
export function doneRemindersBatch(ids: string[]) {
  return post<{ ok: boolean; affected: number }>('/reminders/done-batch', { ids });
}

/** 手工触发提醒扫描（管理端） */
export function scanReminders() {
  return post<{ ok: boolean; created: number }>('/reminders/scan');
}

/* ---- 通知动作 ---- */

export function readNotification(id: string) {
  return post<{ ok: boolean }>(`/notifications/${id}/read`);
}

export function readAllNotifications() {
  return post<{ ok: boolean }>('/notifications/read-all');
}

/* ---- 操作日志 ---- */

export function listLogs(params: {
  page?: number;
  page_size?: number;
  action?: string;
  operator_id?: string;
}) {
  return get<ListResp<OperationLog>>('/logs', params);
}

/** 导出全部待办（备用：供待办中心一次性拉取） */
export function listAllPendingReminders() {
  return get<ListResp<Reminder>>('/reminders', { status: 'pending', page_size: 200 });
}


/* ══════════════ 收尾页：6.2 / 6.3 / 6.5 / 6.8 / 6.10 ══════════════ */

/* ---- 6.2 角色与权限 ---- */

/** 能力矩阵 + 各角色账号数（仅管理员） */
export function fetchRoleMatrix() {
  return get<RoleMatrixResp>('/roles');
}

/** 当前账号拥有的能力（前端菜单兜底，不是授权依据） */
export function fetchMyCapabilities() {
  return get<MyCapabilitiesResp>('/roles/me');
}

/* ---- 6.3 部门管理 ---- */

/** 部门树（含人数与「有账号但未建部门」清单） */
export function fetchDepartmentTree() {
  return get<DepartmentTreeResp>('/departments');
}

export function createDepartment(body: {
  name: string;
  parent_id?: string | null;
  sort_order?: number;
  leader_name?: string | null;
  remark?: string | null;
}) {
  return post<DepartmentTreeResp>('/departments', body);
}

/**  改名会同步账号上的部门名（后端在同一事务里做） */
export function updateDepartment(
  departmentId: string,
  body: {
    name?: string;
    parent_id?: string | null;
    sort_order?: number;
    leader_name?: string | null;
    remark?: string | null;
  },
) {
  return patch<DepartmentTreeResp>(`/departments/${departmentId}`, body);
}

/** 有下级部门或仍有账号归属时返回 409（后端会在 detail 里说明） */
export function deleteDepartment(departmentId: string) {
  return del<DepartmentTreeResp>(`/departments/${departmentId}`);
}

/** 部门树拍平成「上级部门」下拉选项（带层级缩进） */
export function flattenDepartments(
  nodes: DepartmentNode[],
  depth = 0,
): { value: string; label: string; depth: number }[] {
  return nodes.flatMap((node) => [
    { value: node.id, label: `${'\u3000'.repeat(depth)}${node.name}`, depth },
    ...flattenDepartments(node.children ?? [], depth + 1),
  ]);
}

/* ---- 6.5 环节标准卡配置 ---- */

export function listStageStandards(stage?: string) {
  return get<{ items: StageStandard[]; total: number }>('/stage-standards', { stage });
}

export function createStageStandard(body: {
  stage: string;
  seq?: string;
  content: string;
  is_hard_gate?: boolean;
  duration_days?: number;
  evidence_hint?: string | null;
  sort_order?: number;
}) {
  return post<StageStandard>('/stage-standards', body);
}

/** 写操作仅管理员（后端 require_admin）；市场部管理可读不可写 */
export function updateStageStandard(
  standardId: string,
  body: {
    seq?: string;
    content?: string;
    is_hard_gate?: boolean;
    duration_days?: number;
    evidence_hint?: string | null;
    sort_order?: number;
    is_active?: boolean;
  },
) {
  return patch<StageStandard>(`/stage-standards/${standardId}`, body);
}

/* ---- 6.8 通知模板 ---- */

export function fetchNotificationTemplates(params?: { channel?: string; event?: string }) {
  return get<{ items: NotificationTemplate[]; total: number }>('/notification-templates', params);
}

export function createNotificationTemplate(body: {
  code: string;
  name: string;
  channel?: string;
  event?: string;
  title_tpl: string;
  content_tpl?: string;
  is_active?: boolean;
  remark?: string | null;
}) {
  return post<NotificationTemplate>('/notification-templates', body);
}

export function updateNotificationTemplate(
  templateId: string,
  body: {
    name?: string;
    channel?: string;
    event?: string;
    title_tpl?: string;
    content_tpl?: string;
    is_active?: boolean;
    remark?: string | null;
  },
) {
  return patch<NotificationTemplate>(`/notification-templates/${templateId}`, body);
}

/** 渲染预览：缺失变量渲染为空串（不报错） */
export function previewNotificationTemplate(
  templateId: string,
  variables: Record<string, string>,
) {
  return post<{ title: string; content: string; variables: string[]; provided: string[] }>(
    `/notification-templates/${templateId}/preview`,
    { variables },
  );
}

/* ---- 6.10 适配配置（只读） ---- */

export function fetchAdapterStatus() {
  return get<AdapterStatus>('/adapters');
}
