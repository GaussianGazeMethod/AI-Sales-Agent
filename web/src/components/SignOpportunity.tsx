import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, DatePicker, Form, InputNumber, Modal, message } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { signOpportunity } from '@/api/opportunities';

export default function SignOpportunity({ leadId, amountFen }: { leadId: string; amountFen: number }) {
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<{ amount: number; date: Dayjs }>();
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (values: { amount: number; date: Dayjs }) => signOpportunity(leadId, Math.round(values.amount * 100), values.date.format('YYYY-MM-DD')),
    onSuccess: async () => {
      message.success('已完成签约并生成交付交接');
      setOpen(false);
      for (const key of ['opportunity-detail', 'opportunities', 'leads', 'customers', 'customer-detail', 'customer-of-lead', 'contracts', 'workbench']) {
        qc.invalidateQueries({ queryKey: [key] });
      }
      await qc.invalidateQueries({ queryKey: ['assistant-dossier', leadId] });
    },
    onError: () => message.error('签约未完成，请检查硬门槛或刷新后重试'),
  });
  return <>
    <Button type="primary" onClick={() => { form.setFieldsValue({ amount: amountFen / 100, date: dayjs() }); setOpen(true); }}>完成签约</Button>
    <Modal title="登记已完成的合同签约" open={open} confirmLoading={save.isPending} okText="确认登记签约" cancelText="取消" onCancel={() => setOpen(false)} onOk={() => { form.validateFields().then(values => save.mutate(values)).catch(() => {}); }}>
      <Alert showIcon type="info" message="请核对已签合同的金额与日期。登记后项目变为已签约，并生成交付交接。" style={{ marginBottom: 16 }} />
      <Form form={form} layout="vertical">
        <Form.Item label="合同金额（元）" name="amount" rules={[{ required: true, message: '请填写合同金额' }]}><InputNumber min={0.01} precision={2} style={{ width: '100%' }} /></Form.Item>
        <Form.Item label="合同签约日期" name="date" rules={[{ required: true, message: '请选择签约日期' }]}><DatePicker /></Form.Item>
      </Form>
    </Modal>
  </>;
}
