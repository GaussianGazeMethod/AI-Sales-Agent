import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Empty, Space, Tag, message } from 'antd';
import { assistant } from '@/api/assistant';
import { describeError } from '@/api/client';

export default function AssistantActions({ leadId, embedded = false, canComplete = true }: {
  leadId?: string; embedded?: boolean; canComplete?: boolean;
}) {
  const qc = useQueryClient();
  const { data, isError, error, refetch } = useQuery({
    queryKey: ['assistant-actions', leadId], queryFn: () => assistant.actions(leadId),
  });
  const body = isError ? <Alert type="error" message="行动读取失败" description={describeError(error).detail}
    action={<Button onClick={() => void refetch()}>重试</Button>} /> : !data?.length ?
    embedded ? null : <Empty description="销售明确的下一步会自动进入这里" /> : data.map(a =>
    <div key={a.id} style={{ padding: '12px 0', borderBottom: '1px solid #edf0f5', display: 'flex', justifyContent: 'space-between', gap: 16 }}>
      <div><Space><Tag color={a.status === 'done' ? 'green' : 'blue'}>{a.status === 'done' ? '已做' : '待做'}</Tag><strong>{a.content}</strong></Space>
        <p style={{ color: '#8994a5', fontSize: 12 }}>{!leadId && `${a.customer} · `}{a.due_at?.replace('T', ' ') || '时间待确认'}{a.customer_confirmed ? ' · 客户已约定' : ''}</p>
        {a.effect && <p>效果：{a.effect}</p>}
      </div>
      {canComplete && a.status !== 'done' && <Button onClick={async () => {
        try {
          await assistant.complete(a.id);
          for (const key of ['assistant-actions', 'assistant-dossier', 'reminders', 'workbench']) void qc.invalidateQueries({ queryKey: [key] });
        } catch (error) { message.error(describeError(error).detail); }
      }}>已做</Button>}
    </div>);
  return embedded ? <div>{body}</div> : <Card title="销售下一步行动" extra={<span style={{ color: '#8994a5', fontSize: 12 }}>已做与效果分别记录</span>}>{body}</Card>;
}
