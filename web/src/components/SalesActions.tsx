import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, DatePicker, Form, Input, Modal, Select, Space, Tag, Typography, message } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { createSalesAction, feedbackSalesAction, listSalesActions, type SalesAction } from '@/api/salesActions';
import { describeError } from '@/api/client';
import { useAuthStore } from '@/store/auth';
import { useDicts } from '@/hooks/useDicts';
import { fmtDateTime } from '@/utils/format';
import type { AiDiagnosis } from '@/api/ai';
import AssistantActions from './AssistantActions';

function useRefreshActions() {
  const qc = useQueryClient();
  return () => {
    ['sales-actions', 'reminders', 'workbench', 'opportunity-detail', 'standard-judgement',
      'customer-of-lead', 'customer-detail', 'customers', 'leads'].forEach(
      key => qc.invalidateQueries({ queryKey: [key] }),
    );
  };
}

export function CreateActionButton({ leadId, suggestion, diagnosisId, actionIndex, disabled = false }: {
  leadId: string; suggestion?: string; diagnosisId?: string; actionIndex?: number; disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [requestId, setRequestId] = useState('');
  const [form] = Form.useForm<{ content: string; remind_at: Dayjs }>();
  const refresh = useRefreshActions();
  const create = useMutation({
    mutationFn: async (values: { content: string; remind_at: Dayjs }) => {
      return createSalesAction({ lead_id: leadId, content: values.content.trim(),
        remind_at: values.remind_at.format('YYYY-MM-DDTHH:mm:ss'), request_id: requestId,
        diagnosis_id: diagnosisId, action_index: actionIndex });
    },
    onSuccess: () => { setOpen(false); refresh(); message.success('已保存行动，可在商机详情或待办中心填写执行反馈'); },
  });
  return <>
    <Button size="small" disabled={disabled} onClick={() => {
      create.reset(); form.resetFields(); form.setFieldsValue({ content: suggestion ?? '' });
      setRequestId(crypto.randomUUID()); setOpen(true);
    }}>{suggestion ? '采纳为行动' : '安排下一步'}</Button>
    <Modal title="确认下一步行动" open={open} onCancel={() => setOpen(false)}
      onOk={() => { form.validateFields().then(values => create.mutate(values)).catch(() => {}); }} confirmLoading={create.isPending} okText="确认创建待办" destroyOnClose>
      <Typography.Paragraph type="secondary">
        行动由你决定。执行后填写实际结果和客户反馈，系统会保存为项目跟进。
      </Typography.Paragraph>
      {suggestion && <Alert type="info" message="AI 原建议" description={suggestion} style={{ marginBottom: 16 }} />}
      {create.isError && <Alert type="error" showIcon message="行动未保存"
        description={describeError(create.error).detail} style={{ marginBottom: 16 }} />}
      <Form form={form} layout="vertical">
        <Form.Item name="content" label="准备做什么" rules={[{ required: true, whitespace: true, message: '请填写具体行动' }]}>
          <Input.TextArea maxLength={255} showCount rows={3} placeholder="例如：联系校长，确认本期预算是否批复" />
        </Form.Item>
        <Form.Item name="remind_at" label="何时提醒我" rules={[{ required: true, message: '请选择提醒时间' }]}>
          <DatePicker showTime format="YYYY-MM-DD HH:mm" style={{ width: '100%' }} />
        </Form.Item>
      </Form>
    </Modal>
  </>;
}

export function ActionFeedbackButton({ action }: { action: SalesAction }) {
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<{ method: string; content: string; followed_at: Dayjs; next_follow_at?: Dayjs }>();
  const refresh = useRefreshActions();
  const { dicts } = useDicts();
  const save = useMutation({
    mutationFn: async (values: { method: string; content: string; followed_at: Dayjs; next_follow_at?: Dayjs }) => {
      return feedbackSalesAction(action.id, { method: values.method, content: values.content.trim(),
        followed_at: values.followed_at.format('YYYY-MM-DD'),
        next_follow_at: values.next_follow_at?.format('YYYY-MM-DD') });
    },
    onSuccess: () => { setOpen(false); refresh(); message.success('已保存项目跟进并完成行动；环节仍需核对标准后推进'); },
  });
  return <>
    <Button size="small" type="primary" onClick={() => {
      save.reset(); form.resetFields(); form.setFieldsValue({ method: 'phone', followed_at: dayjs() }); setOpen(true);
    }}>填写执行反馈</Button>
    <Modal title="记录执行结果" open={open} onCancel={() => setOpen(false)} onOk={() => { form.validateFields().then(values => save.mutate(values)).catch(() => {}); }}
      confirmLoading={save.isPending} okText="保存跟进并完成行动" destroyOnClose>
      <Alert type="info" message={action.content} description="只记录真实发生的沟通；未联系上或没有进展也如实记录。" style={{ marginBottom: 16 }} />
      {save.isError && <Alert type="error" message="反馈未保存" description={describeError(save.error).detail} style={{ marginBottom: 16 }} />}
      <Form form={form} layout="vertical">
        <Form.Item name="method" label="跟进方式" rules={[{ required: true }]}><Select options={dicts.follow_methods} /></Form.Item>
        <Form.Item name="followed_at" label="实际跟进日期" rules={[{ required: true }]}><DatePicker style={{ width: '100%' }} /></Form.Item>
        <Form.Item name="content" label="做了什么，客户怎么反馈，接下来怎么办" rules={[{ required: true, whitespace: true, message: '请填写真实执行结果' }]}>
          <Input.TextArea rows={5} maxLength={10000} showCount />
        </Form.Item>
        <Form.Item name="next_follow_at" label="下次跟进日期（可选）"><DatePicker style={{ width: '100%' }} /></Form.Item>
      </Form>
    </Modal>
  </>;
}

export function DiagnosisActions({ diagnosis, canWrite }: { diagnosis: AiDiagnosis; canWrite: boolean }) {
  const suggestions = diagnosis.structured?.suggested_actions ?? [];
  const saved = useQuery({
    queryKey: ['sales-actions', diagnosis.lead_id],
    queryFn: () => listSalesActions(diagnosis.lead_id as string),
    enabled: !!diagnosis.lead_id,
  });
  if (!suggestions.length || !diagnosis.lead_id) return null;
  const diagnosisLeadId = diagnosis.lead_id;
  return <Space direction="vertical" size={12} style={{ width: '100%' }}>
    <Typography.Text strong>选择本次要执行的行动</Typography.Text>
    {suggestions.map((suggestion, index) => {
      const existing = saved.data?.items.find(action => action.source_diagnosis_id === diagnosis.id && action.source_action_index === index);
      return <div key={`${diagnosis.id}-${index}`}>
      <Typography.Paragraph style={{ marginBottom: 8 }}>{suggestion}</Typography.Paragraph>
      {existing ? <Tag color={existing.status === 'done' ? 'success' : 'processing'}>{existing.status === 'done' ? '已反馈' : '已安排待办'}</Tag> : canWrite && <CreateActionButton leadId={diagnosisLeadId} suggestion={suggestion} diagnosisId={diagnosis.id} actionIndex={index} />}
    </div>; })}
  </Space>;
}

export default function SalesActions({ leadId, ownerId, active }: { leadId: string; ownerId: string | null; active: boolean }) {
  const user = useAuthStore(s => s.user);
  const canWrite = active && user?.id === ownerId && ['market_staff', 'market_manager', 'admin'].includes(user?.role ?? '');
  const actions = useQuery({ queryKey: ['sales-actions', leadId], queryFn: () => listSalesActions(leadId) });
  const items = actions.data?.items ?? [];
  return <Card size="small" title="下一步行动" extra={canWrite && <CreateActionButton leadId={leadId} />} loading={actions.isLoading}>
    <AssistantActions leadId={leadId} embedded canComplete={canWrite} />
    {actions.isError ? <Alert type="error" message="行动读取失败" description={describeError(actions.error).detail}
      action={<Button onClick={() => actions.refetch()}>重试</Button>} /> : !items.length ?
      <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>可通过 AI 助手补充行动和时间，也可以手动安排。</Typography.Paragraph> :
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        {items.map(action => <div key={action.id}>
          <Space wrap><Tag color={action.status === 'done' ? 'success' : 'processing'}>{action.status === 'done' ? '已反馈' : '待执行'}</Tag>
            <Typography.Text type="secondary">{fmtDateTime(action.remind_at)}</Typography.Text></Space>
          <Typography.Paragraph style={{ margin: '8px 0' }}>{action.content}</Typography.Paragraph>
          {action.source_suggestion && <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>原建议：{action.source_suggestion}</Typography.Paragraph>}
          {action.feedback && <Typography.Paragraph style={{ marginBottom: 0 }}>执行反馈：{action.feedback.content}</Typography.Paragraph>}
          {canWrite && action.receiver_id === user?.id && action.status === 'pending' && <ActionFeedbackButton action={action} />}
        </div>)}
      </Space>}
  </Card>;
}
