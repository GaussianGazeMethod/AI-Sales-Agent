import { useQuery } from '@tanstack/react-query';
import { fetchDicts } from '@/api/dicts';
import type { Dicts } from '@/types';

/** 字典查询 hook：全局缓存，下拉选项统一从这里取 */
export function useDicts() {
  const query = useQuery({
    queryKey: ['dicts'],
    queryFn: fetchDicts,
    staleTime: 10 * 60 * 1000,
  });

  const dicts: Partial<Dicts> = query.data ?? {};

  /** 转成 antd Select/Menu 需要的 options */
  const options = (key: keyof Dicts) => {
    const list = dicts[key];
    return Array.isArray(list) ? list : [];
  };

  return { dicts, options, isLoading: query.isLoading, refetch: query.refetch };
}
