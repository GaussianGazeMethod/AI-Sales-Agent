import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Button,
  Card,
  Col,
  DatePicker,
  Progress,
  Row,
  Space,
  Table,
  Tag,
  Tooltip,
} from 'antd';
import { useNavigate } from 'react-router-dom';
import { AimOutlined, TrophyOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { fetchAchievement } from '@/api/targets';
import { METRIC_LABEL } from '@/utils/constants';
import { fmtPercent } from '@/utils/format';
import { useAuthStore } from '@/store/auth';
import KpiCard from '@/components/KpiCard';
import PageHeader from '@/components/PageHeader';
import { Footnote, NextStepGuide, NotForAssessment } from '@/components/Guide';

/** 7.2 我的目标与业绩：目标 vs 实际（有效商机数 / 新增线索数 / 有效率） */
export default function MyAchievement() {
  const navigate = useNavigate();
  const [period, setPeriod] = useState(dayjs().format('YYYY-MM'));
  const user = useAuthStore((s) => s.user);

  const { data, isLoading } = useQuery({
    queryKey: ['targets-achievement', period],
    queryFn: () => fetchAchievement(period),
  });

  const mine = data?.items?.find((i) => i.user_id === user?.id);
  const metrics = mine?.metrics ?? [];

  const renderValue = (metric: string, value: number) =>
    metric === 'valid_rate' ? fmtPercent(value) : value;

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title="我的目标与业绩"
        subtitle={`按人 × 月看目标达成明细（${period}）——口径与「我的业绩」一致：达成率 = 实际 / 目标。`}
        demo
        meta={[{ label: '成员', value: user?.name || '-' }, { label: '周期', value: period }]}
        actions={
          <Space wrap>
            <DatePicker
              picker="month"
              value={dayjs(period, 'YYYY-MM')}
              allowClear={false}
              onChange={(d) => d && setPeriod(d.format('YYYY-MM'))}
            />
            <Button type="primary" onClick={() => navigate('/me/performance')}>
              看我的业绩
            </Button>
          </Space>
        }
      />

        {!metrics.length ? (
          <NextStepGuide
            title={`${period} 尚未设定目标`}
            description="达成率只在已设目标指标上计算；没有目标就没有达成率（系统不虚构指标）。目标由管理者在「市场目标设定」里按人 × 月设定。"
            actions={
              <Space wrap>
                <Button type="primary" onClick={() => navigate('/targets')}>
                  去市场目标设定
                </Button>
                <Button onClick={() => navigate('/me/performance')}>看我的业绩</Button>
              </Space>
            }
          />
        ) : (
          <Row gutter={[12, 12]}>
            {metrics.map((m) => (
              <Col xs={24} md={8} key={m.metric}>
                <KpiCard
                  label={
                    <Tooltip title="达成率 = 实际值 / 目标值；有效率按 0–1 小数比较">
                      <span>{m.label ?? METRIC_LABEL[m.metric] ?? m.metric}</span>
                    </Tooltip>
                  }
                  value={renderValue(m.metric, m.actual_value)}
                  total={`/ 目标 ${renderValue(m.metric, m.target_value)}`}
                  icon={(m.achievement_rate || 0) >= 1 ? <TrophyOutlined /> : <AimOutlined />}
                  tone={(m.achievement_rate || 0) >= 1 ? 'green' : 'brand'}
                  progress={Math.min((m.achievement_rate || 0) * 100, 100)}
                  rateText={`达成 ${fmtPercent(m.achievement_rate)}`}
                  hint="实际值来自系统实时统计；达成率 = 实际 / 目标（有效率按 0–1 小数）。目标在 1.8 市场目标设定里设定"
                />
              </Col>
            ))}
          </Row>
        )}

      <Card size="small" title={`团队达成明细（${period}）`}>
        <Table
          rowKey={(r) => `${r.user_id}-${r.metric}`}
          size="small"
          loading={isLoading}
          pagination={{ pageSize: 10 }}
          dataSource={(data?.items ?? []).flatMap((u) =>
            u.metrics.map((m) => ({
              user_id: u.user_id,
              user_name: u.user_name,
              metric: m.metric,
              target_value: m.target_value,
              actual_value: m.actual_value,
              achievement_rate: m.achievement_rate,
            })),
          )}
          locale={{
            emptyText: (
              <NextStepGuide
                compact
                title="暂无达成数据"
                description="本期还没有任何成员设定目标；达成明细只在已设目标的成员上计算。"
                actions={
                  <Button size="small" type="primary" onClick={() => navigate('/targets')}>
                    去市场目标设定
                  </Button>
                }
              />
            ),
          }}
          columns={[
            {
              title: '成员',
              dataIndex: 'user_name',
              width: 140,
              render: (v: string, r) =>
                r.user_id === user?.id ? (
                  <Space>
                    {v}
                    <Tag color="blue">我</Tag>
                  </Space>
                ) : (
                  v
                ),
            },
            {
              title: '指标',
              dataIndex: 'metric',
              width: 130,
              render: (v: string) => METRIC_LABEL[v] ?? v,
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
              title: (
                <Tooltip title="达成率 = 实际值 / 目标值；仅用于过程管理，不作为考核依据">
                  <span>达成率</span>
                </Tooltip>
              ),
              dataIndex: 'achievement_rate',
              render: (v: number) => (
                <Progress
                  percent={Math.min(Math.round((v || 0) * 100), 100)}
                  size="small"
                  format={() => fmtPercent(v)}
                />
              ),
            },
          ]}
        />
      </Card>

      <Footnote>
        数据口径：有效商机数 = 当期系统判定为有效商机的线索数；新增线索数 = 当期录入的线索数；
        有效率 = 有效商机数 ÷ 新增线索数；达成率 = 实际值 / 目标值（有效率按 0–1 小数比较）。
        目标由管理者在 1.8 市场目标设定里按「人 × 月 × 指标」设定；没有目标就不会出现达成率（不虚构指标）。
        若需比较两个期间的差异，请用百分点表述（如「较上期 +3.2 个百分点」）。
      </Footnote>
      <NotForAssessment />
    </Space>
  );
}
