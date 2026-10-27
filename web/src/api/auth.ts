import { get, patch, post } from './client';
import type { DataScope, LoginResp, Role, User } from '@/types';

export interface LoginBody {
  username: string;
  password: string;
}

/** 登录：管理员建号，不开放注册 */
export function login(body: LoginBody) {
  return post<LoginResp>('/auth/login', body);
}

/** 当前登录用户 */
export function fetchMe() {
  return get<User>('/auth/me');
}

/** 修改密码 */
export function changePassword(body: { old_password: string; new_password: string }) {
  return post<{ ok: boolean }>('/auth/change-password', body);
}

export interface UserUpsertBody {
  username?: string;
  name: string;
  role: Role;
  data_scope: DataScope;
  department: string;
  phone: string;
  email: string;
  is_active: boolean;
  password?: string;
}

/** 用户列表（管理员页） */
export function listUsers(params?: { page?: number; page_size?: number; keyword?: string }) {
  return get<{ items: User[]; total: number; page: number; page_size: number }>('/users', params);
}

export function createUser(body: UserUpsertBody) {
  return post<User>('/users', body);
}

export function updateUser(id: string, body: Partial<UserUpsertBody>) {
  return patch<User>(`/users/${id}`, body);
}

/** 重置密码（管理员） */
export function resetPassword(id: string, new_password: string) {
  return post<{ ok: boolean }>(`/users/${id}/reset-password`, { new_password });
}

/** 部门列表 / 新建 */
export function listDepartments() {
  return get<{ items: { id: string; name: string; parent_id?: string | null }[]; total: number }>(
    '/departments',
  );
}

export function createDepartment(body: { name: string; parent_id?: string | null }) {
  return post<{ id: string; name: string }>('/departments', body);
}
