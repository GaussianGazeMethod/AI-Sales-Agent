import { Alert, Button, Card, Form, Input, Select } from 'antd';
import { useMutation } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { useDicts } from '@/hooks/useDicts';
import { client } from '@/api/client';
import type { LeadCreateResp } from '@/types';
import PageHeader from '@/components/PageHeader';
export default function LeadQuickCreate() {
  const navigate = useNavigate(); const { dicts } = useDicts();
  const save = useMutation({ mutationFn: async (body: { name: string; source: string; contact_name: string; contact_position: string }) => (await client.post<LeadCreateResp>('/leads', body)).data, onSuccess: data => navigate(`/leads/${data.lead.id}`) });
  return <div style={{ maxWidth: 720, margin: '24px auto' }}><PageHeader title="记录新线索" subtitle="四项资料即可开始联系与验证。" actions={<Button onClick={() => navigate('/ai/chat')}>让 AI 帮我录入</Button>} />{save.isError && <Alert type="error" showIcon message="保存未完成，请核对资料后重试" style={{ marginBottom: 16 }} />}<Card><Form layout="vertical" onFinish={body => save.mutate(body)}><Form.Item name="name" label="客户名称" rules={[{ required: true }]}><Input placeholder="学校或单位名称" maxLength={128} /></Form.Item><Form.Item name="source" label="来源" rules={[{ required: true }]}><Select options={dicts.lead_sources?.map(d => ({ value: d.value, label: d.label }))} placeholder="选择线索来源" /></Form.Item><Form.Item name="contact_name" label="联系人" rules={[{ required: true }]}><Input maxLength={64} placeholder="例如：李校长" /></Form.Item><Form.Item name="contact_position" label="职位" rules={[{ required: true }]}><Input maxLength={64} placeholder="例如：校长、信息主任" /></Form.Item><Button type="primary" htmlType="submit" loading={save.isPending}>保存，开始验证</Button></Form></Card></div>;
}
