import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  Button,
  Card,
  Form,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { listReminders } from '@/api/dashboard';
import { doneReminder, doneRemindersBatch, scanReminders } from '@/api/admin';
import PageHeader from '@/components/PageHeader';
import { Footnote, NextStepGuide, PanelFlag } from '@/components/Guide';
import { useDicts } from '@/hooks/useDicts';
import { fmtDateTime } from '@/utils/format';
import { ReminderTag } from '@/components/tags';
import { useAuthStore } from '@/store/auth';
import type { Reminder } from '@/types';
import AssistantActions from '@/components/AssistantActions';
import { ActionFeedbackButton } from '@/components/SalesActions';

interface FilterValues {
  type?: string;
  status?: string;
}

/** 0.3 待办中心（7.1 我的待办复用本页） */
export default function Todos() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { dicts } = useDicts();
  const role = useAuthStore((s) => s.user?.role);
  const [filters, setFilters] = useState<FilterValues>({ status: 'pending' });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [selected, setSelected] = useState<string[]>([]);

  const { data, isLoading } = useQuery({
    queryKey: ['reminders', filters, page, pageSize],
    queryFn: () => listReminders({ ...filters, page, page_size: pageSize }),
  });

  const refresh = () => {
    setSelected([]);
    qc.invalidateQueries({ queryKey: ['reminders'] });
    qc.invalidateQueries({ queryKey: ['workbench'] });
  };

  const doneOne = useMutation({
    mutationFn: (id: string) => doneReminder(id),
    onSuccess: () => {
      message.success('已完成该待办');
      refresh();
    },
  });

  const doneBatch = useMutation({
    mutationFn: (ids: string[]) => doneRemindersBatch(ids),
    onSuccess: (r) => {
      message.success(`已批量完成 ${r.affected} 条待办`);
      refresh();
    },
  });

  const scan = useMutation({
    mutationFn: scanReminders,
    onSuccess: (r) => {
      message.success(`扫描完成，新增 ${r.created} 条提醒`);
      refresh();
    },
  });

  const columns: ColumnsType<Reminder> = [
    {
      title: '客户名称',
      dataIndex: 'lead_name',
      width: 200,
      ellipsis: true,
      render: (v: string | null, r) =>
        v ? <a onClick={() => r.lead_id && navigate(r.type === 'sales_action' ? `/opportunities/${r.lead_id}` : `/leads/${r.lead_id}`)}>{v}</a> : '-',
    },
    {
      title: '提醒类型',
      dataIndex: 'type',
      width: 130,
      render: (v: string) => <ReminderTag type={v} />,
    },
    { title: '提醒内容', dataIndex: 'content', ellipsis: true },
    {
      title: '提醒时间',
      dataIndex: 'remind_at',
      width: 160,
      render: (v: string) => fmtDateTime(v),
    },
    {
      title: '接收人',
      dataIndex: 'receiver_name',
      width: 100,
      render: (v: string | null) => v || '-',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) => (v === 'done' ? <Tag color="success">已处理</Tag> : <Tag color="orange">待处理</Tag>),
    },
    {
      title: '操作',
      key: 'op',
      width: 90,
      render: (_, r) =>
        r.status === 'pending' && r.type === 'sales_action' ? <ActionFeedbackButton action={r} /> : r.status === 'pending' ? (
          <Button type="link" size="small" onClick={() => doneOne.mutate(r.id)}>
            标记完成
          </Button>
        ) : (
          <Typography.Text type="secondary">已完成</Typography.Text>
        ),
    },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <AssistantActions />
      <PageHeader
        title="待办中心"
        subtitle="按提醒时间处理待办；完成后从列表消失，逾期未处理会持续提醒。"
        meta={[
          { label: '当前筛选', value: `${filters.status === 'done' ? '已处理' : '待处理'}${filters.type ? ` · ${dicts.reminder_types?.find((d) => d.value === filters.type)?.label ?? filters.type}` : ''}` },
          { label: '共', value: `${data?.total ?? 0} 条` },
        ]}
        tags={<PanelFlag>系统提醒与已确认销售行动</PanelFlag>}
        actions={
          <Space>
            {(role === 'admin' || role === 'market_manager') && (
              <Button size="small" onClick={() => scan.mutate()} loading={scan.isPending}>
                手工触发扫描
              </Button>
            )}
            <Button
              size="small"
              type="primary"
              disabled={!selected.length}
              loading={doneBatch.isPending}
              onClick={() => doneBatch.mutate(selected)}
            >
              批量完成（{selected.length}）
            </Button>
          </Space>
        }
      />

      <Card>
        <Form layout="inline" style={{ marginBottom: 12 }}>
          <Form.Item label="提醒类型">
            <Select
              allowClear
              placeholder="全部类型"
              style={{ width: 180 }}
              value={filters.type}
              onChange={(v) => {
                setPage(1);
                setFilters((f) => ({ ...f, type: v }));
              }}
              options={dicts.reminder_types ?? []}
            />
          </Form.Item>
          <Form.Item label="状态">
            <Select
              style={{ width: 140 }}
              value={filters.status}
              onChange={(v) => {
                setPage(1);
                setFilters((f) => ({ ...f, status: v }));
              }}
              options={[
                { value: 'pending', label: '待处理' },
                { value: 'done', label: '已处理' },
              ]}
            />
          </Form.Item>
          <Form.Item>
            <Button
              onClick={() => {
                setPage(1);
                setFilters({ status: 'pending' });
              }}
            >
              重置
            </Button>
          </Form.Item>
        </Form>

        <Table
          rowKey="id"
          size="small"
          loading={isLoading}
          columns={columns}
          dataSource={data?.items ?? []}
          rowSelection={{
            selectedRowKeys: selected,
            onChange: (keys) => setSelected(keys.map((k) => String(k))),
            getCheckboxProps: (r) => ({ disabled: r.status !== 'pending' || r.type === 'sales_action' }),
          }}
          pagination={{
            current: page,
            pageSize,
            total: data?.total ?? 0,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, ps) => {
              setPage(p);
              setPageSize(ps);
            },
          }}
          locale={{
            emptyText: (
              <NextStepGuide
                title={filters.status === 'done' ? '暂无已处理待办' : '暂无待处理事项'}
                description="系统提醒自动生成；销售行动由项目负责人确认创建。请在商机详情安排下一步，执行后填写真实反馈。"
                actions={
                  <Space>
                    <Button size="small" onClick={() => navigate('/leads')}>
                      去线索池
                    </Button>
                    <Button size="small" onClick={() => navigate('/ai/drafts')}>
                      去待确认草稿
                    </Button>
                  </Space>
                }
              />
            ),
          }}
        />
      </Card>

      <Footnote>
        ⓘ 口径：首次跟进按<b>工作日</b>计时（指派后 1 个工作日内完成首触）；回收按 7 个工作日未跟进判定；
        提醒时间为系统生成提醒的时刻，精确截止时刻见商机详情。标记完成仅关闭提醒，不改动商机数据。
      </Footnote>
    </Space>
  );
}
