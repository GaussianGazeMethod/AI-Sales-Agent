import { get } from './client';
import type { Dicts } from '@/types';

/** 全部字典（来源/类型/状态/跟进方式/下一步/风险/提醒类型/环节/角色/数据范围/省份） */
export function fetchDicts() {
  return get<Dicts>('/dicts');
}
