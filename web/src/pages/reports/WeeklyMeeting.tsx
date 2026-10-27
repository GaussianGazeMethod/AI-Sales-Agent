import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  DatePicker,
  Progress,
  Row,
  Segmented,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import {
  AlertOutlined,
  CheckCircleOutlined,
  FileTextOutlined,
  ReloadOutlined,
  RiseOutlined,
  TeamOutlined,
  WalletOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { Dayjs } from 'dayjs';
import { fetchWeeklyMeeting } from '@/api/reports';
import AiContent from '@/components/AiContent';
import PageHeader from '@/components/PageHeader';
import KpiCard from '@/components/KpiCard';
import { AiDisclaimer, Footnote, NextStepGuide } from '@/components/Guide';
import { useDicts } from '@/hooks/useDicts';
import {
  RISK_STATUS_COLOR,
  fenToWan,
  fmtDate,
  fmtDateTime,
  fmtPercent,
} from '@/utils/format';
import type {
  AdvancedStandard,
  AiSuggestion,
  ConversionPair,
  FunnelItem,
  Lead,
  PendingReview,
  PerPersonRhythm,
  PerPersonStage,
  StuckItem,
  TeamTargetRow,
} from '@/types';

/** 团队目标指标中文（后端 metric 为英文代码） */
const METRIC_LABEL: Record<string, string> = {
  valid_lead_count: '有效商机数',
  new_lead_count: '新增线索数',
  valid_rate: '有效率',
  sign_amount: '签单金额',
  paid_amount: '回款金额',
};

/** 小数差 → 「N 个百分点」（判据 C3：转化率差值单位一律用百分点，不写 %） */
function pointDiff(v?: number | null, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '-';
  return `${v >= 0 ? '+' : ''}${(v * 100).toFixed(digits)} 个百分点`;
}

/**
 * 4.5 周会视图（管理人员）
 * 三层：① 团队概览 ② 逐人项目节奏 ③ 待评审（可直接去 2.7 评审卡）。
 */
export default function WeeklyMeeting() {
  const navigate = useNavigate();
  const { dicts } = useDicts();
  const baseDays: Record<string, number> = dicts.stage_baseline?.duration_days ?? {};

  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const params = {
    start: range?.[0]?.format('YYYY-MM-DD'),
    end: range?.[1]?.format('YYYY-MM-DD'),
  };

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['weekly-meeting', params],
    queryFn: () => fetchWeeklyMeeting(params),
  });

  const team = data?.team_overview;
  const people = data?.per_person ?? [];
  const pendingReviews = data?.pending_reviews ?? [];

  /** 人员过滤（Segmented） */
  const [personFilter, setPersonFilter] = useState<string>('all');
  const segmentedOptions = useMemo(
    () => [
      { label: '全部', value: 'all' },
      ...people.map((p) => ({ label: `${p.user_name}（${p.total_count}）`, value: p.user_id })),
    ],
    [people],
  );
  const shownPeople = personFilter === 'all' ? people : people.filter((p) => p.user_id === personFilter);

  /* ---------------- 第一层：团队概览 ---------------- */
  const targetColumns: ColumnsType<TeamTargetRow> = [
    { title: '成员', dataIndex: 'user_name', width: 110 },
    {
      title: '指标',
      dataIndex: 'metric',
      width: 120,
      render: (v: string) => METRIC_LABEL[v] ?? v,
    },
    { title: '目标', dataIndex: 'target_value', width: 100, align: 'right' },
    { title: '实际', dataIndex: 'actual_value', width: 100, align: 'right' },
    {
      title: '达成率',
      dataIndex: 'achievement_rate',
      width: 220,
      render: (v: number) => (
        <Space size={8} style={{ width: '100%' }}>
          <Progress
            percent={Math.min(Math.round((v ?? 0) * 100), 100)}
            size="small"
            showInfo={false}
            strokeColor={v >= 1 ? '#389e0d' : v >= 0.7 ? '#1677ff' : '#cf1322'}
            style={{ width: 120 }}
          />
          <Typography.Text type={v >= 1 ? 'success' : v >= 0.7 ? undefined : 'danger'}>
            {fmtPercent(v, 0)}
          </Typography.Text>
        </Space>
      ),
    },
  ];

  const conversionColumns: ColumnsType<ConversionPair> = [
    {
      title: '阶段转化',
      width: 200,
      render: (_, r) => (
        <Space size={4}>
          <span>{r.from_stage_label}</span>
          <Typography.Text type="secondary">→</Typography.Text>
          <Typography.Text strong>{r.to_stage_label}</Typography.Text>
        </Space>
      ),
    },
    { title: '分子 / 分母', width: 110, align: 'right', render: (_, r) => `${r.to_count} / ${r.from_count}` },
    {
      title: '实际',
      dataIndex: 'rate',
      width: 100,
      align: 'right',
      render: (v: number, r) => (
        <Typography.Text type={r.alert ? 'danger' : undefined}>{fmtPercent(v)}</Typography.Text>
      ),
    },
    { title: '基准', dataIndex: 'baseline', width: 90, align: 'right', render: (v: number) => fmtPercent(v) },
    {
      title: '差值（百分点）',
      key: 'gap',
      width: 140,
      align: 'right',
      render: (_, r) => (
        <Typography.Text type={r.gap < 0 ? 'danger' : 'success'}>{pointDiff(r.gap)}</Typography.Text>
      ),
    },
    {
      title: '状态',
      width: 110,
      render: (_, r) => (r.alert ? <Tag color="red">低于预警线</Tag> : <Tag color="green">正常</Tag>),
    },
  ];

  const overview = (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {/* 判据 C3：每个数字都能就地看到口径 */}
      <Row gutter={[12, 12]}>
        <Col xs={12} md={8} lg={4}>
          <KpiCard
            label="新增有效商机"
            value={team?.new_valid_count ?? 0}
            suffix="个"
            icon={<RiseOutlined />}
            tone="green"
            rateText={`新增线索 ${team?.new_lead_count ?? 0} 条`}
            hint="口径：统计区间内状态变为「有效商机」的线索数；新增线索 = 区间内创建的全部线索（含后续被判无效的）。团队概览口径与 4.4 周报一致。"
          />
        </Col>
        <Col xs={12} md={8} lg={4}>
          <KpiCard
            label="在途商机"
            value={team?.in_progress_count ?? 0}
            suffix="个"
            icon={<FileTextOutlined />}
            tone="brand"
            rateText={`在途金额 ${fenToWan(team?.total_amount_fen)} 万`}
            hint="口径：当前处于 7 环节内的商机条数与金额之和（金额不乘成交概率）。"
          />
        </Col>
        <Col xs={12} md={8} lg={4}>
          <KpiCard
            label="加权预测签约额"
            value={fenToWan(team?.weighted_amount_fen)}
            suffix="万"
            icon={<RiseOutlined />}
            tone="purple"
            hint="口径：加权金额 = 环节金额 × 该环节成交概率，逐环节求和；只用于预测，不代表已签约。"
          />
        </Col>
        <Col xs={12} md={8} lg={4}>
          <KpiCard
            label="卡单"
            value={team?.stuck_count ?? 0}
            suffix="个"
            icon={<AlertOutlined />}
            tone={(team?.stuck_count ?? 0) > 0 ? 'red' : 'gray'}
            danger={(team?.stuck_count ?? 0) > 0}
            hint="口径：所处环节停留天数超过该环节停留时长基准的商机数；基准未设（0）的环节不参与判定，基准在「6.5 环节标准卡配置」维护。"
          />
        </Col>
        <Col xs={12} md={8} lg={4}>
          <KpiCard
            label="本周签约"
            value={fenToWan(team?.signed_amount_fen)}
            suffix="万"
            icon={<CheckCircleOutlined />}
            tone="green"
            hint="口径：统计区间内状态变为「已签约」的线索合同金额之和。"
          />
        </Col>
        <Col xs={12} md={8} lg={4}>
          <KpiCard
            label="本周回款"
            value={fenToWan(team?.paid_amount_fen)}
            suffix="万"
            icon={<WalletOutlined />}
            tone="amber"
            hint="口径：统计区间内登记的回款记录金额之和；回款记录表没有状态字段，全部记录都视为已确认回款。"
          />
        </Col>
      </Row>

      <Row gutter={[12, 12]}>
        <Col xs={12} md={8} lg={4}>
          <KpiCard
            label="待处理预警"
            value={team?.risk_counts?.pending ?? 0}
            total={`/ ${team?.risk_counts?.total ?? 0}`}
            icon={<AlertOutlined />}
            tone={(team?.risk_counts?.pending ?? 0) > 0 ? 'red' : 'gray'}
            danger={(team?.risk_counts?.pending ?? 0) > 0}
            hint="口径：状态为 pending 的预警条数 / 预警总条数。处理入口在「2.4 风险与卡单中心」。"
          />
        </Col>
        <Col xs={12} md={8} lg={4}>
          <KpiCard
            label="处理中预警"
            value={team?.risk_counts?.processing ?? 0}
            icon={<ReloadOutlined />}
            tone="amber"
            hint="口径：状态为 processing（已认领、尚未解除）的预警条数。"
          />
        </Col>
        <Col xs={12} md={8} lg={4}>
          <KpiCard
            label="已解除预警"
            value={team?.risk_counts?.resolved ?? 0}
            icon={<CheckCircleOutlined />}
            tone="green"
            hint="口径：状态为 resolved 的预警条数；未回复 / 未闭环不算解除。"
          />
        </Col>
        <Col xs={12} md={8} lg={4}>
          <KpiCard
            label="首次跟进超时"
            value={team?.overdue_first_follow?.length ?? 0}
            suffix="条"
            icon={<AlertOutlined />}
            tone={(team?.overdue_first_follow?.length ?? 0) > 0 ? 'red' : 'gray'}
            danger={(team?.overdue_first_follow?.length ?? 0) > 0}
            hint="口径：指派后超过首次跟进时限仍未跟进（未触达）的线索数；首次跟进按工作日计时，以指派时间为起点。"
          />
        </Col>
        <Col xs={12} md={8} lg={4}>
          <KpiCard
            label="待评审项目"
            value={pendingReviews.length}
            suffix="个"
            icon={<TeamOutlined />}
            tone={pendingReviews.length ? 'amber' : 'gray'}
            hint="口径：满足评审触发条件（如停留超基准）且尚无有效评审结论的项目数；评审入口在「2.7 项目评审（评审卡）」。"
          />
        </Col>
      </Row>

      <Row gutter={12}>
        <Col xs={24} lg={12}>
          <Card size="small" title="各环节商机数与金额">
            <Table<FunnelItem>
              rowKey="stage"
              size="small"
              loading={isLoading}
              dataSource={team?.stage_distribution ?? []}
              pagination={false}
              locale={{
                emptyText: (
                  <NextStepGuide
                    compact
                    title="暂无在途商机"
                    description="统计区间内没有处于 7 环节内的商机；先在「2.1 商机列表」推进线索到环节一。"
                    actions={
                      <Button size="small" href="/opportunities">
                        去 2.1 商机列表
                      </Button>
                    }
                  />
                ),
              }}
              columns={[
                { title: '环节', dataIndex: 'label' },
                { title: '商机数', dataIndex: 'count', width: 80, align: 'right' },
                {
                  title: '金额(万)',
                  dataIndex: 'amount_fen',
                  width: 100,
                  align: 'right',
                  render: (v: number) => fenToWan(v),
                },
                {
                  title: '加权(万)',
                  dataIndex: 'weighted_amount_fen',
                  width: 100,
                  align: 'right',
                  render: (v: number) => fenToWan(v),
                },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="转化率 vs 基准">
            <Table<ConversionPair>
              rowKey="path"
              size="small"
              loading={isLoading}
              dataSource={team?.conversion ?? []}
              columns={conversionColumns}
              pagination={false}
              rowClassName={(r) => (r.alert ? 'meeting-row-alert' : '')}
              locale={{
                emptyText: (
                  <NextStepGuide
                    compact
                    title="暂无转化率数据"
                    description="转化率需要环节之间有推进记录才会产生；推进商机后本表自动出现。差值单位为「百分点」。"
                    actions={
                      <Button size="small" href="/reports/opportunity-funnel">
                        看 4.3 商机漏斗与预测
                      </Button>
                    }
                  />
                ),
              }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={12}>
        <Col xs={24} lg={14}>
          <Card
            size="small"
            title={<Space>团队卡单清单<Tag color="red">{team?.stuck_items?.length ?? 0}</Tag></Space>}
          >
            <Table<StuckItem>
              rowKey="lead_id"
              size="small"
              loading={isLoading}
              dataSource={team?.stuck_items ?? []}
              pagination={false}
              locale={{
                emptyText: (
                  <NextStepGuide
                    compact
                    title="本周无卡单"
                    description="没有商机超过所处环节的停留时长基准；基准未设（0）的环节不参与判定。"
                    actions={
                      <Button size="small" href="/admin/stage-standards">
                        去核对环节基准
                      </Button>
                    }
                  />
                ),
              }}
              columns={[
                {
                  title: '客户',
                  dataIndex: 'lead_name',
                  render: (v: string, r) => (
                    <a onClick={() => navigate(`/opportunities/${r.lead_id}`)}>{v}</a>
                  ),
                },
                { title: '环节', dataIndex: 'stage_label', width: 150 },
                {
                  title: '停留 / 基准',
                  width: 130,
                  render: (_, r) => (
                    <Typography.Text type={r.stay_days > r.base_days ? 'danger' : undefined}>
                      {r.stay_days} / {r.base_days} 天
                    </Typography.Text>
                  ),
                },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card size="small" title="首次跟进超时（未触达）">
            <Table<Lead>
              rowKey="id"
              size="small"
              loading={isLoading}
              dataSource={team?.overdue_first_follow ?? []}
              pagination={{ pageSize: 5, showSizeChanger: false }}
              locale={{
                emptyText: (
                  <NextStepGuide
                    compact
                    title="无超时未跟进"
                    description="所有已指派线索都在首次跟进时限内完成了跟进；首次跟进按工作日计时。"
                    actions={
                      <Button size="small" href="/leads">
                        去 1.1 线索列表
                      </Button>
                    }
                  />
                ),
              }}
              columns={[
                {
                  title: '客户',
                  dataIndex: 'name',
                  render: (v: string, r) => <a onClick={() => navigate(`/leads/${r.id}`)}>{v}</a>,
                },
                { title: '负责人', dataIndex: 'owner_name', width: 90, render: (v: string | null) => v || '-' },
                {
                  title: '截止',
                  dataIndex: 'first_follow_deadline',
                  width: 150,
                  render: (v: string | null) => fmtDateTime(v),
                },
              ]}
            />
          </Card>
        </Col>
      </Row>

      <Card size="small" title="团队目标达成（本月）">
        <Table<TeamTargetRow>
          rowKey={(r) => `${r.user_id}-${r.metric}`}
          size="small"
          loading={isLoading}
          dataSource={team?.team_targets ?? []}
          columns={targetColumns}
          pagination={false}
          locale={{
            emptyText: (
              <NextStepGuide
                compact
                title="本月还没有设定目标"
                description="目标在「1.8 市场目标设定」里按人 × 指标配置；设好后本表自动出现达成率（达成率 = 实际 ÷ 目标）。"
                actions={
                  <Button size="small" onClick={() => navigate('/targets')}>
                    去 1.8 市场目标设定
                  </Button>
                }
              />
            ),
          }}
        />
      </Card>
    </Space>
  );

  /* ---------------- 第二层：逐人项目节奏 ---------------- */
  const personCard = (p: PerPersonRhythm) => (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}>
          <KpiCard
            label="在跟商机"
            value={p.total_count}
            suffix="个"
            icon={<FileTextOutlined />}
            tone="brand"
            hint="口径：该成员名下当前处于 7 环节内的商机条数。"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="加权预测"
            value={fenToWan(p.weighted_amount_fen)}
            suffix="万"
            icon={<RiseOutlined />}
            tone="purple"
            hint="口径：加权金额 = 环节金额 × 该环节成交概率，逐条汇总；只用于预测，不代表已签约。"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="卡点"
            value={p.stuck?.length ?? 0}
            suffix="个"
            icon={<AlertOutlined />}
            tone={(p.stuck?.length ?? 0) > 0 ? 'red' : 'gray'}
            danger={(p.stuck?.length ?? 0) > 0}
            hint="口径：停留超环节基准、或缺少硬门槛材料的商机数；基准未设（0）的环节不参与停留判定，但缺硬门槛仍会被列出。"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="本周推进标准"
            value={p.advanced_standards?.length ?? 0}
            suffix="条"
            icon={<CheckCircleOutlined />}
            tone="green"
            hint="口径：本周勾选完成（is_done）并留下证据的环节完成标准条数，代表真实推进。"
          />
        </Col>
      </Row>

      <Card size="small" title="环节分布（平均停留 vs 基准 · 超期数）">
        <Table<PerPersonStage>
          rowKey="stage"
          size="small"
          dataSource={p.stages ?? []}
          pagination={false}
          locale={{
            emptyText: (
              <NextStepGuide
                compact
                title="暂无在途商机"
                description="该成员当前没有处于 7 环节内的商机；可让本人先在「2.1 商机列表」推进线索到环节一。"
                actions={
                  <Button size="small" href="/opportunities">
                    去 2.1 商机列表
                  </Button>
                }
              />
            ),
          }}
          columns={[
            { title: '环节', dataIndex: 'label', width: 160 },
            { title: '商机数', dataIndex: 'count', width: 90, align: 'right' },
            {
              title: '金额(万)',
              dataIndex: 'amount_fen',
              width: 110,
              align: 'right',
              render: (v: number) => fenToWan(v),
            },
            {
              title: '平均停留 / 基准',
              width: 170,
              render: (_, r) => {
                const base = baseDays[r.stage];
                const over = base !== undefined && r.stay_days_avg > base;
                return (
                  <Typography.Text type={over ? 'danger' : undefined}>
                    {r.stay_days_avg ?? 0} / {base ?? '—'} 天
                  </Typography.Text>
                );
              },
            },
            {
              title: '超期数',
              dataIndex: 'overdue_count',
              width: 100,
              align: 'right',
              render: (v: number) => (
                <Typography.Text type={v > 0 ? 'danger' : undefined}>{v ?? 0}</Typography.Text>
              ),
            },
          ]}
        />
      </Card>

      <Card
        size="small"
        title={
          <Space>
            卡点（含缺失硬门槛）
            <Tag color="red">{p.stuck?.length ?? 0}</Tag>
          </Space>
        }
      >
        <Table<StuckItem>
          rowKey="lead_id"
          size="small"
          dataSource={p.stuck ?? []}
          pagination={false}
          locale={{
            emptyText: (
              <NextStepGuide
                compact
                title="无卡点"
                description="该成员的商机都没有超环节基准停留，也没有缺失硬门槛材料。"
              />
            ),
          }}
          columns={[
            {
              title: '客户',
              dataIndex: 'lead_name',
              width: 180,
              render: (v: string, r) => <a onClick={() => navigate(`/opportunities/${r.lead_id}`)}>{v}</a>,
            },
            { title: '环节', dataIndex: 'stage_label', width: 150 },
            {
              title: '停留 / 基准',
              width: 130,
              render: (_, r) => (
                <Typography.Text type={r.stay_days > r.base_days ? 'danger' : undefined}>
                  {r.stay_days} / {r.base_days} 天
                </Typography.Text>
              ),
            },
            {
              title: '完成标准',
              width: 110,
              render: (_, r) => `${r.done_count ?? 0} / ${r.total_count ?? 0}`,
            },
            {
              title: '缺失硬门槛',
              render: (_, r) =>
                r.missing_hard_gates?.length ? (
                  <Space direction="vertical" size={0}>
                    {r.missing_hard_gates.map((g) => (
                      <Typography.Text key={g} type="danger" style={{ fontSize: 12 }}>
                        · {g}
                      </Typography.Text>
                    ))}
                  </Space>
                ) : (
                  <Tag color="green">硬门槛已过</Tag>
                ),
            },
          ]}
        />
      </Card>

      <Card
        size="small"
        title={
          <Space>
            本周推进的标准与证据
            <Tag color="geekblue">{p.advanced_standards?.length ?? 0}</Tag>
          </Space>
        }
      >
        {p.advanced_standards?.length ? (
          <Space direction="vertical" size={10} style={{ width: '100%' }}>
            {p.advanced_standards.map((a: AdvancedStandard, idx: number) => (
              <Card size="small" key={`${a.lead_id}-${a.done_at ?? idx}`}>
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Space wrap size={8}>
                    <Typography.Text strong>{a.lead_name}</Typography.Text>
                    <Tag color="blue">{a.stage_label}</Tag>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {fmtDateTime(a.done_at)}
                      {a.done_by_name ? ` · ${a.done_by_name}` : ''}
                    </Typography.Text>
                  </Space>
                  <Typography.Text>{a.standard_content}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    证据：{a.evidence || '—'}
                  </Typography.Text>
                </Space>
              </Card>
            ))}
          </Space>
        ) : (
          <NextStepGuide
            compact
            title="本周无推进记录"
            description="推进记录来自商机环节里勾选「完成标准」并附证据的动作；没有记录通常意味着本周没有推进环节。"
            actions={
              <Button size="small" href="/opportunities">
                去 2.1 商机列表推进
              </Button>
            }
          />
        )}
      </Card>

      <Card
        size="small"
        title={
          <Space>
            AI 诊断建议
            <Tag color="geekblue">{p.ai_suggestions?.length ?? 0}</Tag>
            <AiDisclaimer />
          </Space>
        }
      >
        {p.ai_suggestions?.length ? (
          <Space direction="vertical" size={10} style={{ width: '100%' }}>
            {p.ai_suggestions.map((s: AiSuggestion) => (
              <Card size="small" key={s.lead_id}>
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Space wrap size={8}>
                    <a onClick={() => navigate(`/opportunities/${s.lead_id}`)}>{s.lead_name}</a>
                    {s.stage_label && <Tag>{s.stage_label}</Tag>}
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {fmtDateTime(s.created_at)}
                    </Typography.Text>
                  </Space>
                  <AiContent content={s.content} />
                </Space>
              </Card>
            ))}
          </Space>
        ) : (
          <NextStepGuide
            compact
            title="暂无 AI 诊断建议"
            description="AI 诊断在商机详情页发起，只出草稿、人确认才生效；没有建议说明当前没有生成过。"
            actions={
              <Button size="small" href="/opportunities">
                去 2.2 商机详情发起诊断
              </Button>
            }
          />
        )}
      </Card>
    </Space>
  );

  const rhythm = (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Space wrap>
        <Typography.Text type="secondary">人员筛选</Typography.Text>
        <Segmented
          options={segmentedOptions}
          value={personFilter}
          onChange={(v) => setPersonFilter(String(v))}
        />
      </Space>

      {shownPeople.length === 0 ? (
        <NextStepGuide
          title="暂无市场人员数据"
          description="统计区间内没有可展示的市场人员。可能是当前账号的数据范围不包含团队成员，或该区间内没人有商机活动；可在「6.1 用户管理」核对人员与数据范围。"
          actions={
            <Space>
              <Button type="primary" onClick={() => navigate('/admin/users')}>
                去 6.1 用户管理
              </Button>
              <Button onClick={() => navigate('/reports/weekly')}>看 4.4 周报</Button>
            </Space>
          }
        />
      ) : (
        <Collapse
          defaultActiveKey={shownPeople.slice(0, 1).map((p) => p.user_id)}
          items={shownPeople.map((p) => ({
            key: p.user_id,
            label: (
              <Space wrap size={8}>
                <Typography.Text strong>{p.user_name}</Typography.Text>
                <Tag color="blue">在跟 {p.total_count}</Tag>
                <Tag>加权 {fenToWan(p.weighted_amount_fen)} 万</Tag>
                {(p.stuck?.length ?? 0) > 0 ? (
                  <Tag color="red">卡点 {p.stuck.length}</Tag>
                ) : (
                  <Tag color="green">无卡点</Tag>
                )}
                <Tag color="geekblue">推进 {p.advanced_standards?.length ?? 0} 条</Tag>
              </Space>
            ),
            children: personCard(p),
          }))}
        />
      )}
    </Space>
  );

  /* ---------------- 第三层：评审 ---------------- */
  const pendingColumns: ColumnsType<PendingReview> = [
    {
      title: '客户名称',
      dataIndex: 'lead_name',
      width: 190,
      render: (v: string, r) => <a onClick={() => navigate(`/opportunities/${r.lead_id}`)}>{v}</a>,
    },
    { title: '负责人', dataIndex: 'owner_name', width: 100, render: (v: string | null) => v || '-' },
    { title: '当前环节', dataIndex: 'stage_label', width: 150 },
    { title: '停留天数', dataIndex: 'stay_days', width: 100, align: 'right', render: (v: number) => `${v} 天` },
    { title: '待评审原因', dataIndex: 'reason', ellipsis: true },
    {
      title: '上次判定',
      width: 130,
      render: (_, r) =>
        r.last_verdict_label ? <Tag color="orange">{r.last_verdict_label}</Tag> : <Tag>未评审</Tag>,
    },
    {
      title: '操作',
      width: 110,
      fixed: 'right',
      render: (_, r) => (
        <Button
          type="primary"
          size="small"
          onClick={() => navigate(`/reviews?lead_id=${r.lead_id}`)}
        >
          去评审
        </Button>
      ),
    },
  ];

  const reviewLayer = (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message={`第三层 · 评审：共 ${pendingReviews.length} 个项目待评审`}
        description="逐个过评审卡（5 看 + 能力/态度/正常判定）。点「去评审」会直接打开该项目的评审卡。"
      />
      <Table<PendingReview>
        rowKey="lead_id"
        size="small"
        loading={isLoading}
        dataSource={pendingReviews}
        columns={pendingColumns}
        scroll={{ x: 1000 }}
        locale={{
          emptyText: (
            <NextStepGuide
              compact
              title="暂无待评审项目"
              description="评审在项目停留超基准或触发评审条件时进入待评审清单；现在没有说明没有触发项。"
              actions={
                <Button size="small" onClick={() => navigate('/reviews')}>
                  打开 2.7 项目评审
                </Button>
              }
            />
          ),
        }}
        pagination={{ pageSize: 10, showSizeChanger: false, showTotal: (t) => `共 ${t} 条` }}
      />
      <Space>
        <Button type="primary" onClick={() => navigate('/reviews')}>
          打开 2.7 项目评审
        </Button>
        <Button onClick={() => navigate('/risks')}>打开 2.4 风险与卡单中心</Button>
      </Space>
    </Space>
  );

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <style>{`.meeting-row-alert > td { background: #fff1f0 !important; }`}</style>

      {/* 判据 C2：一句话说清"这一页先干什么"；区间选择与刷新收进 actions */}
      <PageHeader
        title="4.5 周会视图"
        subtitle="按三层开会：① 团队概览看整体缺口 ② 逐人项目节奏看谁卡在哪 ③ 待评审逐个过评审卡；区间默认本周一 ~ 今天。"
        demo
        meta={[
          { label: '统计区间', value: data?.period?.label ?? '-' },
          { label: '起止', value: `${fmtDate(data?.period?.start)} ~ ${fmtDate(data?.period?.end)}` },
          { label: '待评审', value: `${pendingReviews.length} 个` },
        ]}
        actions={
          <>
            <DatePicker.RangePicker
              value={range}
              onChange={(v) => setRange(v as [Dayjs, Dayjs] | null)}
              allowClear
            />
            <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
              刷新
            </Button>
            <Button onClick={() => navigate('/reports/weekly')}>看 4.4 周报</Button>
          </>
        }
      />

      <Card size="small">
        <Footnote>
          口径：① 默认区间为本周一 ~ 今天，团队概览口径与「4.4 周报」一致（同一后端服务函数）；② 转化率差值单位为
          「百分点」（如 30% → 26% 记作 -4.0 个百分点），不写成百分比；③ 卡单 = 所处环节停留超该环节停留时长基准，
          基准未设（0）的环节不参与停留判定；④ 首次跟进按工作日计时，以指派时间为起点；⑤ 加权预测 = 环节金额 ×
          该环节成交概率，只用于预测、不代表已签约。
        </Footnote>
        <Footnote style={{ marginTop: 6 }}>
          边界：本页用于周会过程管理，AI 诊断建议与系统预警只作为讨论输入，不作为考核依据；评审结论由人填写。
        </Footnote>
      </Card>

      <Card title="第一层 · 团队概览" loading={isLoading}>
        {overview}
      </Card>

      <Card title="第二层 · 逐人项目节奏" loading={isLoading}>
        {rhythm}
      </Card>

      <Card
        title={
          <Space>
            第三层 · 评审
            <Tag color={pendingReviews.length ? 'orange' : 'default'}>{pendingReviews.length}</Tag>
          </Space>
        }
        extra={
          <Tag color={RISK_STATUS_COLOR.pending}>
            待处理预警 {team?.risk_counts?.pending ?? 0}
          </Tag>
        }
      >
        {reviewLayer}
      </Card>

      <Footnote>
        周会视图用于过程管理：数字只用于会议讨论与推进，不作为考核依据；评审卡结论由评审人填写并留痕。
      </Footnote>
    </Space>
  );
}
