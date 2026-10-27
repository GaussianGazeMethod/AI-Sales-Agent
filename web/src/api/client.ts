import axios, { AxiosError } from 'axios';
import { message } from 'antd';

/** 与后端约定的统一前缀 */
export const API_BASE = '/api/v1';

/** token 本地存储 key，供 axios 拦截器直接读取（避免与 store 循环依赖） */
const TOKEN_KEY = 'mkt-auth';

/** 读取持久化的 token（zustand persist 结构） */
export function readToken(): string | null {
  try {
    const raw = localStorage.getItem(TOKEN_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { state?: { token?: string | null } };
    return parsed?.state?.token ?? null;
  } catch {
    return null;
  }
}

/** 清空登录态并跳转登录页（401 时使用） */
function forceLogout() {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* 忽略存储异常 */
  }
  if (!window.location.pathname.startsWith('/login')) {
    window.location.href = '/login';
  }
}

/**
 * 默认超时（毫秒）。
 * 普通接口毫秒级返回即可；**AI 诊断类接口实测要 30 秒以上**（模型思考 + 重试），
 * 因此单独提供 LONG_TIMEOUT，由 api/ai.ts 显式传入，避免浏览器侧提前超时。
 */
export const DEFAULT_TIMEOUT = 60000;
export const LONG_TIMEOUT = 180000;

export const client = axios.create({
  baseURL: API_BASE,
  timeout: DEFAULT_TIMEOUT,
});

client.interceptors.request.use((config) => {
  const token = readToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

client.interceptors.response.use(
  (resp) => resp,
  (error: AxiosError<{ detail?: string | { msg?: string }[]; message?: string }>) => {
    const status = error.response?.status;
    if (status === 401) {
      message.error('登录已过期，请重新登录');
      forceLogout();
      return Promise.reject(error);
    }
    const data = error.response?.data;
    let detail = '';
    if (typeof data?.detail === 'string') {
      detail = data.detail;
    } else if (Array.isArray(data?.detail)) {
      detail = data.detail.map((d) => d?.msg ?? '').join('；');
    } else if (typeof data?.message === 'string') {
      detail = data.message;
    }
    if (status === 403) {
      message.error(detail || '没有操作权限');
    } else if (status && status >= 500) {
      message.error(detail || '服务异常，请稍后重试');
    } else {
      message.error(detail || error.message || '请求失败');
    }
    return Promise.reject(error);
  },
);

/**
 * 统一把后端异常翻成**可展示的原因**（判据 C2：错误也要说清"为什么 + 怎么办"）。
 *
 * - 业务异常：后端统一返回 `{detail: "中文提示"}`（见 backend/app/core/errors.py）
 * - AI 不可用：503 + detail（本仓不做规则降级，失败就明确失败）
 * - 网络/超时：error.message（如 "timeout of 180000ms exceeded"）
 */
export function describeError(e: unknown): { status?: number; detail: string } {
  const err = e as AxiosError<{ detail?: string | { msg?: string }[]; message?: string }>;
  if (!err?.response) {
    return {
      detail:
        err?.code === 'ECONNABORTED'
          ? '请求超时（模型本次没有在约定时间内返回）'
          : err?.message || '网络异常，请检查网络后重试',
    };
  }
  const data = err.response.data;
  let detail = '';
  if (typeof data?.detail === 'string') detail = data.detail;
  else if (Array.isArray(data?.detail)) detail = data.detail.map((d) => d?.msg ?? '').join('；');
  else if (typeof data?.message === 'string') detail = data.message;
  return { status: err.response.status, detail: detail || '请求失败，请稍后重试' };
}

/** 统一取出 data 的辅助函数 */
export async function get<T>(url: string, params?: unknown): Promise<T> {
  const { data } = await client.get<T>(url, { params: params as object });
  return data;
}

export async function post<T>(
  url: string,
  body?: unknown,
  config?: { timeout?: number; params?: Record<string, string | number> },
): Promise<T> {
  const { data } = await client.post<T>(url, body, config);
  return data;
}

export async function patch<T>(url: string, body?: unknown): Promise<T> {
  const { data } = await client.patch<T>(url, body);
  return data;
}

export async function del<T>(url: string): Promise<T> {
  const { data } = await client.delete<T>(url);
  return data;
}
