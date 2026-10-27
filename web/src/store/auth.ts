import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { Role, User } from '@/types';

interface AuthState {
  token: string | null;
  user: User | null;
  login: (token: string, user: User) => void;
  setUser: (user: User) => void;
  logout: () => void;
}

/** 登录态：token + 用户信息，持久化到 localStorage（key 与 axios 拦截器一致） */
export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      login: (token, user) => set({ token, user }),
      setUser: (user) => set({ user }),
      logout: () => set({ token: null, user: null }),
    }),
    { name: 'mkt-auth' },
  ),
);

/** 便捷读取当前角色 */
export function useRole(): Role | undefined {
  return useAuthStore((s) => s.user?.role);
}
