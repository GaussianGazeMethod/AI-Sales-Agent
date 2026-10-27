import { get, post } from './client';
import type { FollowUp, Reminder } from '@/types';

export interface SalesAction extends Reminder {
  source_diagnosis_id?: string | null;
  source_action_index?: number | null;
  source_suggestion?: string | null;
  completion_followup_id?: string | null;
  feedback?: FollowUp | null;
}
export function listSalesActions(leadId: string) {
  return get<{ items: SalesAction[]; total: number }>('/sales-actions', { lead_id: leadId });
}
export function createSalesAction(body: {
  lead_id: string; content: string; remind_at: string; request_id: string;
  diagnosis_id?: string; action_index?: number;
}) {
  return post<SalesAction>('/sales-actions', body);
}
export function feedbackSalesAction(id: string, body: {
  method: string; content: string; followed_at: string; next_follow_at?: string;
}) {
  return post<{ action: SalesAction; followup: FollowUp }>(`/sales-actions/${id}/feedback`, body);
}
