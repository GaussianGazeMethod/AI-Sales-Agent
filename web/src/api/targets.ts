import { get, post } from './client';
import type { AchievementResp, ListResp, Target, TargetMetric } from '@/types';

/** 目标列表（按周期） */
export function listTargets(params: { period: string; user_id?: string }) {
  return get<ListResp<Target>>('/targets', params);
}

/** 设定目标：metric ∈ valid_lead_count | new_lead_count | valid_rate */
export function createTarget(body: {
  period_type: 'month';
  period: string;
  user_id: string;
  metric: TargetMetric;
  target_value: number;
}) {
  return post<{ item: Target }>('/targets', body);
}

/** 达成情况（按人聚合指标） */
export function fetchAchievement(period: string) {
  return get<AchievementResp>('/targets/achievement', { period });
}
