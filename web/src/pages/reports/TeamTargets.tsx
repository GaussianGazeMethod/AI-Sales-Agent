import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
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
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  AlertOutlined,
  CheckCircleOutlined,
  FileTextOutlined,
  PercentageOutlined,
  ReloadOutlined,
  RiseOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { fetchAchievement, listTargets } from '@/api/targets';
import PageHeader from '@/components/PageHeader';
import KpiCard from '@/components/KpiCard';
import { Footnote, NextStepGuide } from '@/components/Guide';
import { METRIC_LABEL, METRIC_OPTIONS } from '@/utils/constants';
import { fmtPercent } from '@/utils/format';
import type { AchievementMetric, AchievementUser } from '@/types';

/** 行：一个人 + 他的各指标达成情况 */
interface UserRow {
  user_id: string;
  user_name: string;
  metrics: Record<string, AchievementMetric>;
  /** 该人所有指标达成率的算术平均（用于排序与概览） */
  avg_rate: number;
  /** 是否所有指标都 ≥100% */
  achieved: boolean;
}

/** 达成率 → 颜色：<80% 红，80–100% 橙，≥100% 绿 */
function rateColor(v: number): string {
  if (v < 0.8) return '#cf1322';
  if (v < 1) return '#fa8c16';
  return '#3f8600';
}

/**
 * 4.7 团队目标达成（管理人员 / 管理员，只读）。
 * 设目标是 1.8「市场目标设定」的职责，本页只做「人 × 指标」的达成横向对比。
 */
export default function TeamTargets() {
  const navigate = useNavigate();
  const [period, setPeriod] = useState(dayjs().format('YYYY-MM'));

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['targets-achievement', period],
    queryFn: () => fetchAchievement(period),
  });

  // 目标清单仅用于口径核对（条数应对得上），不提供任何写操作
  const { data: targetList } = useQuery({
    queryKey: ['targets', period],
    queryFn: () => listTargets({ period }),
    retry: false,
  });

  const items: AchievementUser[] = data?.items ?? [];

  /** 指标列顺序：按 METRIC_OPTIONS 固定顺序，最后追加后端额外下发的指标 */
  const metricKeys = useMemo(() => {
    const seen: string[] = [];
    METRIC_OPTIONS.forEach((o) => {
      if (items.some((u) => u.metrics.some((m) => m.metric === o.value))) seen.push(o.value);
    });
    items.forEach((u) =>
      u.metrics.forEach((m) => {
        if (!seen.includes(m.metric)) seen.push(m.metric);
      }),
    );
    return seen;
  }, [items]);

  /** 行数据：按平均达成率升序（最需要关注的人排最前） */
  const rows: UserRow[] = useMemo(
    () =>
      items
        .map((u) => {
          const metrics: Record<string, AchievementMetric> = {};
          u.metrics.forEach((m) => {
            metrics[m.metric] = m;
          });
          const rates = u.metrics.map((m) => m.achievement_rate ?? 0);
          const avg = rates.length ? rates.reduce((a, b) => a + b, 0) / rates.length : 0;
          return {
            user_id: u.user_id,
            user_name: u.user_name,
            metrics,
            avg_rate: avg,
            achieved: u.metrics.length > 0 && rates.every((r) => r >= 1),
          };
        })
        .sort((a, b) => a.avg_rate - b.avg_rate || a.user_name.localeCompare(b.user_name)),
    [items],
  );

  /** 概览统计 */
  const overview = useMemo(() => {
    const metricCount = rows.reduce((a, r) => a + Object.keys(r.metrics).length, 0);
    const allRates = rows.flatMap((r) => Object.values(r.metrics).map((m) => m.achievement_rate ?? 0));
    const avgRate = allRates.length ? allRates.reduce((a, b) => a + b, 0) / allRates.length : 0;
    const achievedCount = rows.filter((r) => r.achieved).length;
    const lowRateCount = rows.reduce(
      (a, r) => a + Object.values(r.metrics).filter((m) => (m.achievement_rate ?? 0) < 0.8).length,
      0,
    );
    return {
      userCount: rows.length,
      metricCount,
      avgRate,
      achievedCount,
      missedCount: rows.length - achievedCount,
      lowRateCount,
    };
  }, [rows]);

  /** 指标值展示：有效率是 0-1 小数，其余是整数 */
  const renderValue = (metric: string, value: number) =>
    metric === 'valid_rate' ? fmtPercent(value) : value;

  const columns: ColumnsType<UserRow> = [
    {
      title: '姓名',
      dataIndex: 'user_name',
      width: 120,
      fixed: 'left',
      render: (v: string, r) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{v || '-'}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            平均达成 {fmtPercent(r.avg_rate)}
          </Typography.Text>
        </Space>
      ),
    },
    ...metricKeys.map((key) => ({
      title: (
        <Space size={4}>
          <span>{METRIC_LABEL[key] ?? key}</span>
          {key === 'valid_rate' ? <Tag color="default">0-1</Tag> : null}
        </Space>
      ),
      key,
      children: [
        {
          title: '目标',
          key: `${key}-target`,
          width: 110,
          align: 'right' as const,
          render: (_: unknown, r: UserRow) => {
            const m = r.metrics[key];
            return m ? renderValue(key, m.target_value) : '-';
          },
        },
        {
          title: '实际',
          key: `${key}-actual`,
          width: 110,
          align: 'right' as const,
          render: (_: unknown, r: UserRow) => {
            const m = r.metrics[key];
            return m ? renderValue(key, m.actual_value) : '-';
          },
        },
        {
          title: '达成率',
          key: `${key}-rate`,
          width: 190,
          render: (_: unknown, r: UserRow) => {
            const m = r.metrics[key];
            if (!m) return <Typography.Text type="secondary">未设目标</Typography.Text>;
            const v = m.achievement_rate ?? 0;
            return (
              <Progress
                percent={Math.min(Math.max(Math.round(v * 100), 0), 100)}
                size="small"
                strokeColor={rateColor(v)}
                format={() => fmtPercent(v)}
              />
            );
          },
        },
      ],
    })),
  ];

  const isEmpty = !isLoading && items.length === 0;
  const metricRowCount = rows.reduce((a, r) => a + Object.keys(r.metrics).length, 0);

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {/* 判据 C2：一句话说清"这一页先干什么"；周期筛选与直达入口收进 actions */}
      <PageHeader
        title="4.7 团队目标达成"
        subtitle="先看团队概览找出缺口最大的人，再看「人 × 指标」明细定位是哪一项没达成；目标的新增 / 修改请回「1.8 市场目标设定」。"
        demo
        meta={[
          { label: '统计周期', value: period },
          { label: '已设目标人数', value: overview.userCount },
          { label: '排序', value: '按平均达成率升序（最需要关注的人在最上面）' },
        ]}
        actions={
          <>
            <DatePicker
              picker="month"
              allowClear={false}
              value={dayjs(period, 'YYYY-MM')}
              onChange={(d) => d && setPeriod(d.format('YYYY-MM'))}
            />
            <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
              刷新
            </Button>
            <Button onClick={() => navigate('/targets')}>前往 1.8 市场目标设定</Button>
          </>
        }
      />

      {/* 团队概览：判据 C3 —— 每个数字都能就地看到口径 */}
      <Card size="small" title={`团队概览（${period}）`}>
        <Row gutter={[12, 12]}>
          <Col xs={12} md={8} lg={4}>
            <KpiCard
              label="已设目标人数"
              value={overview.userCount}
              icon={<TeamOutlined />}
              tone="brand"
              rateText={`目标清单 ${targetList?.total ?? '-'} 条`}
              hint="口径：当期存在目标记录的人数（按人去重），与「1.8 市场目标设定」的目标清单条数对应。"
            />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <KpiCard
              label="总指标条数"
              value={overview.metricCount}
              icon={<FileTextOutlined />}
              tone="purple"
              hint="口径：所有成员的指标记录条数之和（人 × 指标）；某成员只设了部分指标时，只统计已设的指标。"
            />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <KpiCard
              label="平均达成率"
              value={fmtPercent(overview.avgRate)}
              icon={<PercentageOutlined />}
              tone={overview.avgRate < 0.8 ? 'red' : overview.avgRate < 1 ? 'amber' : 'green'}
              progress={Math.min(Math.round(overview.avgRate * 100), 100)}
              hint="口径：达成率 = 实际 ÷ 目标；平均达成率 = 所有「人 × 指标」达成率的算术平均（仅用于概览与排序，不代表个人考核结论）。"
            />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <KpiCard
              label="达标（≥100%）人数"
              value={overview.achievedCount}
              total={`/ ${overview.userCount} 人`}
              icon={<CheckCircleOutlined />}
              tone="green"
              progress={overview.userCount ? (overview.achievedCount / overview.userCount) * 100 : 0}
              hint="口径：该成员当期所有已设指标的达成率都 ≥100% 才计入达标人数；只设了 1 项指标也按此判定。"
            />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <KpiCard
              label="未达标人数"
              value={overview.missedCount}
              total={`/ ${overview.userCount} 人`}
              icon={<AlertOutlined />}
              tone={overview.missedCount > 0 ? 'red' : 'gray'}
              danger={overview.missedCount > 0}
              hint="口径：至少有一项已设指标达成率 <100% 的人数（= 已设目标人数 − 达标人数）。"
            />
          </Col>
          <Col xs={12} md={8} lg={4}>
            <KpiCard
              label="低于 80% 的指标数"
              value={overview.lowRateCount}
              total={`/ ${overview.metricCount} 条`}
              icon={<RiseOutlined />}
              tone={overview.lowRateCount > 0 ? 'amber' : 'gray'}
              hint="口径：达成率 <80% 的「人 × 指标」条数；80% 是关注线（不是考核线），用于提示优先辅导对象。"
            />
          </Col>
        </Row>
      </Card>

      <Card
        size="small"
        title={`达成明细（人 × 指标，${period}）`}
        extra={
          <Space size={12}>
            <Tag color="red">低于 80%</Tag>
            <Tag color="orange">80%–100%</Tag>
            <Tag color="green">≥100%</Tag>
          </Space>
        }
      >
        {isEmpty ? (
          <NextStepGuide
            title={`${period} 尚未设定目标`}
            description="本页只做达成对比，不提供写操作。目标在「1.8 市场目标设定」里按人 × 指标配置；设好目标后本页自动出现达成率，实际值由系统按当期数据汇总。"
            actions={
              <Space>
                <Button type="primary" onClick={() => navigate('/targets')}>
                  去 1.8 设定目标
                </Button>
                <Button onClick={() => setPeriod(dayjs().subtract(1, 'month').format('YYYY-MM'))}>
                  看上一个月的
                </Button>
              </Space>
            }
          />
        ) : (
          <>
            <Table<UserRow>
              rowKey="user_id"
              size="small"
              loading={isLoading}
              columns={columns}
              dataSource={rows}
              pagination={false}
              scroll={{ x: 900 }}
              locale={{
                emptyText: (
                  <NextStepGuide
                    compact
                    title="暂无达成数据"
                    description="接口未返回该周期的达成明细；可换一个月份，或先去「1.8 市场目标设定」核对目标是否已配到人。"
                    actions={
                      <Button size="small" onClick={() => navigate('/targets')}>
                        去 1.8 市场目标设定
                      </Button>
                    }
                  />
                ),
              }}
            />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              共 {rows.length} 人 / {metricRowCount} 条指标；目标清单 {targetList?.total ?? '-'} 条。
              两者应保持一致，若不同通常是该成员已填实际但未设目标，或当期数据尚未汇总。
            </Typography.Text>
          </>
        )}
      </Card>

      <Card size="small" title="「目标 vs 实际」口径说明">
        <Space direction="vertical" size={6} style={{ width: '100%' }}>
          <Typography.Text>
            <Typography.Text strong>有效商机数</Typography.Text>
            ：当期系统判定为「有效商机」的线索数（状态 = 有效商机，按有效时间归属当期）。
          </Typography.Text>
          <Typography.Text>
            <Typography.Text strong>新增线索数</Typography.Text>
            ：当期录入系统的线索条数（按创建时间归属当期），含后续被判无效的线索。
          </Typography.Text>
          <Typography.Text>
            <Typography.Text strong>有效率</Typography.Text>
            ：有效商机数 ÷ 新增线索数，填目标时用 0–1 小数（如 0.35）。
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            达成率 = 实际值 ÷ 目标值（有效率同样是两个比率的比值）；达成率 ≥100% 记为达标，
            80%–100% 为需关注，&lt;80% 标红。达成率按各指标独立计算，人的「平均达成」是各指标达成率的算术平均，仅用于排序与概览。
          </Typography.Text>
          <Footnote>
            本页为只读达成看板：① 达成率 = 实际 ÷ 目标；② 有效商机数 = 当期状态为「有效商机」的线索数（按有效时间归属当期）；
            ③ 新增线索数 = 当期录入系统的线索条数（按创建时间归属当期，含后续被判无效的线索）；
            ④ 有效率 = 有效商机数 ÷ 新增线索数，填目标时用 0–1 小数（如 0.35）。
            「80% 关注线 / 100% 达标线」只用于提示辅导优先级，不作为个人考核结论；目标与实际的写入口都在「1.8 市场目标设定」。
          </Footnote>
        </Space>
      </Card>
    </Space>
  );
}
