import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Empty, Modal, Space, Tag, Typography, message } from 'antd';
import { autoApplyStandards, reviewCompletedStandards, toggleStandard, type CompletedReview } from '@/api/opportunities';

export default function CompletedStandardReview({ leadId }: { leadId: string }) {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const review = useMutation({ mutationFn: () => reviewCompletedStandards(leadId) });
  const save = useMutation({
    mutationFn: async ({ item, replace }: { item: CompletedReview['items'][number]; replace: boolean }) => {
      if (replace && item.followup_id && item.evidence_quote) {
        // 复核后替换依据时同样原样回传固定条件分项（服务器重算 AND，前端不自报达成）
        await autoApplyStandards(leadId, [{
          standard_id: item.standard_id,
          followup_id: item.followup_id,
          evidence_quote: item.evidence_quote,
          conditions: item.conditions ?? [],
        }]);
      } else {
        await toggleStandard(leadId, item.standard_id, { is_done: false, remark: '负责人核对 AI 复核意见后取消完成' });
      }
    },
    onSuccess: () => {
      message.success('已保存你的确认');
      qc.invalidateQueries({ queryKey: ['opportunity-detail', leadId] });
      qc.invalidateQueries({ queryKey: ['stage-progress'] });
      qc.invalidateQueries({ queryKey: ['standard-judgement', leadId] });
      for (const key of ['opportunities', 'workbench', 'leads', 'customer-of-lead', 'customer-detail', 'customers']) {
        qc.invalidateQueries({ queryKey: [key] });
      }
      review.reset();
      setOpen(false);
    },
    onError: () => message.error('保存失败，请刷新项目后重试'),
  });
  const confirm = (item: CompletedReview['items'][number], replace: boolean) => Modal.confirm({
    title: replace ? '替换这项标准的达成依据？' : '取消这项标准的完成状态？',
    content: replace ? item.evidence_quote : '当前阶段保持不变，这项标准将恢复为未完成，原依据仍保留。',
    okText: '确认修改', cancelText: '保留原状',
    onOk: () => save.mutateAsync({ item, replace }),
  });
  return <>
    <Button size="small" onClick={() => { review.reset(); setOpen(true); review.mutate(); }}>复核已完成依据</Button>
    <Modal title="复核已完成标准" open={open} width={850} footer={null} onCancel={() => setOpen(false)} destroyOnClose>
      <Alert type="info" showIcon message="对比现有依据与 AI 复核意见。只有你确认修改后才生效。" style={{ marginBottom: 16 }} />
      {review.isPending && <Typography.Paragraph>正在根据本项目跟进复核，请稍候…</Typography.Paragraph>}
      {review.isError && <Alert type="error" message="复核失败，原状态未改变" action={<Button onClick={() => review.mutate()}>重试</Button>} />}
      {review.data && !review.data.items.length && <Empty description="当前环节没有已完成标准可复核" />}
      <Space direction="vertical" style={{ width: '100%' }} size={16}>
        {review.data?.items.map(item => <div key={item.standard_id} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 16 }}>
          <Typography.Text strong>{item.content}</Typography.Text>
          <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>现有依据：{item.current_evidence || '未登记'}</Typography.Paragraph>
          <Tag color={item.satisfied ? 'green' : 'orange'}>{item.satisfied ? '跟进支持达成' : '需人工核对'}</Tag>
          <Typography.Paragraph style={{ marginTop: 8 }}>{item.reason}</Typography.Paragraph>
          {item.evidence_quote && <Typography.Paragraph>复核引用：{item.evidence_quote}<br /><Typography.Text type="secondary">跟进日期：{item.followup_at?.slice(0, 10)}</Typography.Text></Typography.Paragraph>}
          {item.satisfied && item.evidence_quote !== item.current_evidence
            ? <Button size="small" loading={save.isPending} onClick={() => confirm(item, true)}>确认替换依据</Button>
            : !item.satisfied && <Button danger size="small" loading={save.isPending} onClick={() => confirm(item, false)}>确认取消完成</Button>}
        </div>)}
      </Space>
    </Modal>
  </>;
}
