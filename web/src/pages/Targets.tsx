import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Button,
  Card,
  Col,
  DatePicker,
  Form,
  InputNumber,
  Progress,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import { AimOutlined, TeamOutlined, TrophyOutlined } from '@ant-design/icons';
import { createTarget, fetchAchievement, listTargets } from '@/api/targets';
import { listUsers } from '@/api/auth';
import { METRIC_LABEL, METRIC_OPTIONS } from '@/utils/constants';
import { fmtPercent } from '@/utils/format';
import KpiCard from '@/components/KpiCard';
import PageHeader from '@/components/PageHeader';
import { Footnote, NextStepGuide, NotForAssessment } from '@/components/Guide';
import type { Target, TargetMetric } from '@/types';

/** 1.8 市场目标设定：按人 × 月设置 有效商机数 / 新增线索数 / 有效率，并展示达成率 */
export default function Targets() {
  const qc = useQueryClient();
  const [period, setPeriod] = useState(dayjs().format('YYYY-MM'));
  const [form] = Form.useForm<{
    user_id: string;
    metric: TargetMetric;
    target_value: number;
  }>();

  const { data, isLoading } = useQuery({
    queryKey: ['targets', period],
    queryFn: () => listTargets({ period }),
  });

  const { data: achievement } = useQuery({
    queryKey: ['targets-achievement', period],
    queryFn: () => fetchAchievement(period),
  });

  const { data: userData } = useQuery({
    queryKey: ['users', 'all'],
    queryFn: () => listUsers({ page: 1, page_size: 200 }),
    retry: false,
  });
  const userOptions = (userData?.items ?? []).map((u) => ({ value: u.id, label: u.name }));

  const create = useMutation({
    mutationFn: (v: { user_id: string; metric: TargetMetric; target_value: number }) =>
      createTarget({ period_type: 'month', period, ...v }),
    onSuccess: () => {
      message.success('目标已保存');
      form.resetFields();
      qc.invalidateQueries({ queryKey: ['targets', period] });
      qc.invalidateQueries({ queryKey: ['targets-achievement', period] });
    },
  });

  /** 达成率：有效率按 0-1 小数展示，其余按整数展示 */
  const renderValue = (metric: string, value: number) =>
    metric === 'valid_rate' ? fmtPercent(value) : value;

  const columns: ColumnsType<Target> = [
    { title: '成员', dataIndex: 'user_name', width: 130, render: (v) => v || '-' },
    {
      title: '指标',
      dataIndex: 'metric',
      width: 130,
      render: (v: string) => <Tag color="blue">{METRIC_LABEL[v] ?? v}</Tag>,
    },
    {
      title: '目标值',
      dataIndex: 'target_value',
      width: 120,
      render: (v: number, r) => renderValue(r.metric, v),
    },
    {
      title: '实际值',
      dataIndex: 'actual_value',
      width: 120,
      render: (v: number, r) => renderValue(r.metric, v),
    },
    {
      title: '达成率',
      dataIndex: 'achievement_rate',
      width: 200,
      render: (v: number) => (
        <Progress
          percent={Math.min(Math.round((v || 0) * 100), 100)}
          size="small"
          status={(v || 0) >= 1 ? 'success' : 'active'}
          format={() => fmtPercent(v)}
        />
      ),
    },
  ];

  const achievementRows = useMemo(
    () =>
      (achievement?.items ?? []).flatMap((u) =>
        u.metrics.map((m) => ({
          key: `${u.user_id}-${m.metric}`,
          user_name: u.user_name,
          metric: m.metric,
          target_value: m.target_value,
          actual_value: m.actual_value,
          achievement_rate: m.achievement_rate,
        })),
      ),
    [achievement],
  );

  const achievementColumns: ColumnsType<(typeof achievementRows)[number]> = [
    { title: '成员', dataIndex: 'user_name', width: 130 },
    {
      title: '指标',
      dataIndex: 'metric',
      width: 130,
      render: (v: string) => <Tag color="blue">{METRIC_LABEL[v] ?? v}</Tag>,
    },
    {
      title: '目标值',
      dataIndex: 'target_value',
      width: 110,
      render: (v: number, r) => renderValue(r.metric, v),
    },
    {
      title: '实际值',
      dataIndex: 'actual_value',
      width: 110,
      render: (v: number, r) => renderValue(r.metric, v),
    },
    {
      title: '达成率',
      dataIndex: 'achievement_rate',
      render: (v: number) => (
        <Progress percent={Math.min(Math.round((v || 0) * 100), 100)} size="small" format={() => fmtPercent(v)} />
      ),
    },
  ];

  const targetRows = data?.items ?? [];
  const achievedCount = achievementRows.filter((r) => (r.achievement_rate || 0) >= 1).length;

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title="市场目标设定"
        subtitle="先按「人 × 月 × 指标」定目标，达成率才有的看——目标没设，业绩页就不会出现达成率。"
        demo
        actions={
          <Space wrap>
            <Typography.Text type="secondary">周期</Typography.Text>
            <DatePicker
              picker="month"
              value={dayjs(period, 'YYYY-MM')}
              allowClear={false}
              onChange={(d) => d && setPeriod(d.format('YYYY-MM'))}
            />
          </Space>
        }
      />

      <Row gutter={[12, 12]}>
        <Col xs={24} sm={8}>
          <KpiCard
            label={`本期目标条数（${period}）`}
            value={targetRows.length}
            icon={<AimOutlined />}
            tone="brand"
            hint="目标按「人 × 月 × 指标」逐条设定：同一个人可以同时有有效商机数、新增线索数、有效率三个目标"
          />
        </Col>
        <Col xs={24} sm={8}>
          <KpiCard
            label="已设目标人数"
            value={new Set(targetRows.map((t) => t.user_id)).size}
            icon={<TeamOutlined />}
            tone="purple"
            hint="本期至少设定了一条目标的市场人员数（按 user_id 去重）"
          />
        </Col>
        <Col xs={24} sm={8}>
          <KpiCard
            label="达成（≥100%）条数"
            value={achievedCount}
            total={`/ ${achievementRows.length}`}
            icon={<TrophyOutlined />}
            tone={achievedCount > 0 ? 'green' : 'gray'}
            hint="达成率 = 实际值 / 目标值；有效率按 0–1 小数比较。达成条数只反映过程进度，不作为考核依据"
          />
        </Col>
      </Row>

      <Card title="设定目标">
        <Form
          form={form}
          layout="inline"
          onFinish={(v) => create.mutate(v)}
          initialValues={{ metric: 'valid_lead_count' }}
        >
          <Form.Item
            name="user_id"
            label="成员"
            rules={[{ required: true, message: '请选择成员' }]}
          >
            <Select
              style={{ width: 180 }}
              placeholder="请选择成员"
              options={userOptions}
              showSearch
              optionFilterProp="label"
            />
          </Form.Item>
          <Form.Item name="metric" label="指标" rules={[{ required: true }]}>
            <Select style={{ width: 180 }} options={METRIC_OPTIONS} />
          </Form.Item>
          <Form.Item
            name="target_value"
            label="目标值"
            rules={[{ required: true, message: '请输入目标值' }]}
            extra="有效率填 0-1 小数，如 0.35"
          >
            <InputNumber style={{ width: 160 }} min={0} step={1} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={create.isPending}>
              保存目标
            </Button>
          </Form.Item>
        </Form>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          目标 = 人 × 月 × 指标；重复设定同一个「人 + 指标 + 周期」会覆盖旧值。有效率填 0–1 小数（如 0.35）。
        </Typography.Text>
      </Card>

      <Row gutter={12}>
        <Col xs={24} lg={12}>
          <Card size="small" title={`目标清单（${period}）`}>
            <Table
              rowKey="id"
              size="small"
              loading={isLoading}
              columns={columns}
              dataSource={data?.items ?? []}
              pagination={{ pageSize: 10, showTotal: (t) => `共 ${t} 条` }}
              locale={{
                emptyText: (
                  <NextStepGuide
                    compact
                    title={`${period} 尚未设定目标`}
                    description="用上方表单选成员 + 指标 + 目标值保存即可；设好之后「我的业绩 / 我的达成」才会出现达成率。"
                  />
                ),
              }}
            />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title={`达成情况（${period}）`}>
            <Table
              rowKey="key"
              size="small"
              columns={achievementColumns}
              dataSource={achievementRows}
              pagination={false}
              locale={{
                emptyText: (
                  <NextStepGuide
                    compact
                    title="暂无达成数据"
                    description="达成情况只在「本期已设目标」的成员上计算；先把左侧的目标设起来。"
                  />
                ),
              }}
            />
          </Card>
        </Col>
      </Row>

      <Footnote>
        目标口径：按「人 × 月 × 指标」设定，指标有三类——有效商机数、新增线索数、有效率。
        **达成率 = 实际值 / 目标值**（有效率按 0–1 小数比较）；实际值来自线索与商机的实时统计，不在本页手工填写。
        目标与达成用于过程管理，不作为考核依据。
      </Footnote>
      <NotForAssessment />
    </Space>
  );
}
