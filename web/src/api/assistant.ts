import { client, LONG_TIMEOUT } from './client';

export interface AssistantAction {
  id: string; lead_id: string; content: string; due_at: string | null;
  customer_confirmed: boolean; status: string; effect: string | null; customer?: string;
}
export interface AssistantReport {
  requirements_status?: 'ready' | 'draft' | 'not_applicable'; requirement_gaps?: string[];
  id: string; sections: { title: string; content: string; evidence_keys: string[] }[];
  suggested_actions: { content: string; due_at: string | null }[];
}
export interface AssistantResult {
  overall_status?: 'succeeded' | 'partial' | 'failed';
  operation_receipts?: { operation: string; status: 'succeeded' | 'failed' | 'skipped'; record_id?: string; retryable?: boolean; error?: string | null }[];
  turn_id: string; reply: string; business_executed: boolean; question?: string | null;
  task: { lead_id?: string; skill_id?: string; state?: string; pending_questions?: string[] };
  record?: Record<string, string | null>; actions?: AssistantAction[];
  stage?: { before: string; after: string; status: string; gaps: string[] };
  report?: AssistantReport; dossier?: { facts: Record<string, unknown>; customer: string; revision: number };
}
export interface AssistantTurn { requested_lead_id?: string; id: string; request_key: string; content: string; status: string; result?: AssistantResult; error?: string }
export interface Conversation { id: string; lead_id?: string | null; turns?: AssistantTurn[] }
export interface DossierProof {
  quote?: string; occurred_at?: string; value?: unknown; turn_id?: string;
  confirmed_at?: string; confirmation?: { quote?: string; occurred_at?: string; turn_id?: string };
  superseded?: DossierProof[];
}
export interface Dossier {
  customer: string; revision: number | null; facts: Record<string, unknown>;
  record: Record<string, string>; evidence?: Record<string, DossierProof>;
  history?: { id: string; content: string; occurred_at: string; recorded_at: string;
    changes?: Record<string, unknown> }[];
  followups?: { key: string; content: string; occurred_at?: string }[];
}
export const assistant = {
  create: async (lead_id?: string) => (await client.post<Conversation>('/ai/conversations', { lead_id })).data,
  read: async (id: string) => (await client.get<Conversation>(`/ai/conversations/${id}`)).data,
  list: async () => (await client.get<Conversation[]>('/ai/assistant/conversations')).data,
  send: async (id: string, request_key: string, content: string, lead_id?: string) => (await client.post<AssistantResult>(`/ai/conversations/${id}/messages`, { request_key, content, lead_id }, { timeout: LONG_TIMEOUT })).data,
  dossier: async (id: string, details = false) => (await client.get<Dossier>(`/ai/assistant/projects/${id}/dossier`, { params: { details } })).data,
  actions: async (lead_id?: string) => (await client.get<AssistantAction[]>('/ai/assistant/actions', { params: { lead_id } })).data,
  complete: async (id: string, effect?: string) => (await client.post<AssistantAction>(`/ai/assistant/actions/${id}/complete`, { effect })).data,
  adopt: async (id: string, action_index: number) => (await client.post<AssistantAction>(`/ai/assistant/reports/${id}/adopt`, { action_index })).data,
  word: async (id: string) => {
    const response = await client.get(`/ai/assistant/reports/${id}/word`, { responseType: 'blob' });
    const url = URL.createObjectURL(response.data); const a = document.createElement('a');
    a.href = url; a.download = '项目报告.docx'; a.click(); URL.revokeObjectURL(url);
  },
};
/** 后续语音接入只负责转写，转写文本沿用同一消息接口。 */
export interface VoiceTranscriber { transcribe(blob: Blob): Promise<{ text: string }> }
