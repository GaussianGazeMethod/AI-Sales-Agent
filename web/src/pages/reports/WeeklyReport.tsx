import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  DatePicker,
  Descriptions,
  Input,
  Modal,
  Row,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  AlertOutlined,
  CheckCircleOutlined,
  EditOutlined,
  FileTextOutlined,
  ReloadOutlined,
  RiseOutlined,
  SaveOutlined,
  SendOutlined,
  ThunderboltOutlined,
  WalletOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import dayjs, { type Dayjs } from 'dayjs';
import {
  fetchCurrentWeekly,
  fetchTeamWeekly,
  collectWeekly,
  generateWeekly,
  generateWeeklyBody,
  submitWeekly,
  updateWeekly,
} from '@/api/reports';
import AiContent from '@/components/AiContent';
import PageHeader from '@/components/PageHeader';
import KpiCard from '@/components/KpiCard';
import { AiDisclaimer, Footnote, NextStepGuide } from '@/components/Guide';
import { useAuthStore } from '@/store/auth';
import {
  GENERATED_BY_COLOR,
  GENERATED_BY_LABEL,
  fenToWan,
  fmtDate,
  fmtDateTime,
  fmtPercent,
  formatYuan,
} from '@/utils/format';
import type { AdvancedStandard, ConversionPair, FunnelItem, StuckItem, WeeklyReport } from '@/types';

/**
 * `2026-W40` → 该 ISO 周的周一（与后端 date.fromisocalendar 口径一致）。
 * 不引 dayjs 插件，手算即可。
 */
function periodToMonday(period: string): Dayjs | null {
  const m = /^(\d{4})-W(\d{2})$/.exec(period);
  if (!m) return null;
  const year = Number(m[1]);
  const week = Number(m[2]);
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const jan4Day = jan4.getUTCDay() || 7;
  const week1Monday = new Date(jan4);
  week1Monday.setUTCDate(jan4.getUTCDate() - jan4Day + 1);
  const monday = new Date(week1Monday);
  monday.setUTCDate(week1Monday.getUTCDate() + (week - 1) * 7);
  return dayjs(monday);
}

/** 小数差 → 「N 个百分点」（判据 C3：转化率差值单位一律用百分点，不写 %） */
function pointDiff(v?: number | null, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '-';
  return `${v >= 0 ? '+' : ''}${(v * 100).toFixed(digits)} 个百分点`;
}

/** 日期 → `2026-W40`（ISO 周，周一为一周起点） */
function mondayToPeriod(d: Dayjs): string {
  const target = new Date(Date.UTC(d.year(), d.month(), d.date()));
  const dayNum = target.getUTCDay() || 7;
  target.setUTCDate(target.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(target.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil(((target.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${target.getUTCFullYear()}-W${String(weekNo).padStart(2, '0')}`;
}

/**
 * 4.4 周报
 * 自动汇总（新增有效商机 / 各环节分布 / 卡单 / 转化率 vs 基准 / 加权预测 / 签约回款）
 * + AI 正文（可编辑）+ 问题 + 下周计划 → 提交。
 *
 * 判据：C2（空态 / 报错都说清"为什么 + 怎么办"）、C3（口径写在 KpiCard hint 与页脚 Footnote）、
 * C4（AI 正文标「不作为考核依据」，人工改过不被覆盖）。
 */
export default function WeeklyReportPage() {
  const qc = useQueryClient();
  const role = useAuthStore((s) => s.user?.role);
  const isManager = role === 'admin' || role === 'market_manager';

  const [period, setPeriod] = useState<string | undefined>(undefined);
  /** 周期输入框草稿：只有回车 / 失焦 / 点「查询」才提交，避免每敲一个字符打一次接口 */
  const [periodInput, setPeriodInput] = useState('');
  const [editing, setEditing] = useState(false);
  const [bodyDraft, setBodyDraft] = useState('');
  const [problems, setProblems] = useState('');
  const [nextPlan, setNextPlan] = useState('');
  const [viewing, setViewing] = useState<WeeklyReport | null>(null);
  /** 是否允许 AI 覆盖人工写过的正文（默认否） */
  const [forceRewrite, setForceRewrite] = useState(false);

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['weekly-current', period ?? 'current'],
    queryFn: () => fetchCurrentWeekly(period),
  });

  const item = data?.item ?? null;
  const effectivePeriod = period ?? data?.period;

  // 首次拿到数据后把 period 固定下来（便于生成 / 团队汇总使用同一周期）
  useEffect(() => {
    if (!period && data?.period) setPeriod(data.period);
  }, [period, data?.period]);

  // 服务端周期回填输入框
  useEffect(() => {
    if (data?.period) setPeriodInput(data.period);
  }, [data?.period]);

  /** 提交周期输入（仅接受 2026-W40 格式，否则提示并回滚） */
  const commitPeriod = () => {
    const v = periodInput.trim();
    if (!v) return;
    if (!/^\d{4}-W\d{2}$/.test(v)) {
      message.warning('周期格式应为 2026-W40');
      setPeriodInput(data?.period ?? period ?? '');
      return;
    }
    if (v !== period) setPeriod(v);
  };

  // 报告变化时同步编辑草稿
  useEffect(() => {
    setBodyDraft(item?.body ?? '');
    setProblems(item?.problems ?? '');
    setNextPlan(item?.next_plan ?? '');
    setEditing(false);
  }, [item?.id, item?.updated_at, item?.body, item?.problems, item?.next_plan]);

  /** 只汇总指标：宿主能力，不依赖模型 */
  const doCollect = useMutation({
    mutationFn: () => collectWeekly({ period: effectivePeriod }),
    onSuccess: () => {
      message.success('已按最新数据汇总指标');
      qc.invalidateQueries({ queryKey: ['weekly-current'] });
      qc.invalidateQueries({ queryKey: ['weekly-team'] });
    },
  });

  /** 只生成 AI 正文：模型能力，AI 不可用会报错（指标不受影响） */
  const doGenerateBody = useMutation({
    mutationFn: (reportId: string) => generateWeeklyBody(reportId, { force: forceRewrite }),
    onSuccess: () => {
      message.success('正文已由 Agently 生成');
      qc.invalidateQueries({ queryKey: ['weekly-current'] });
    },
    onError: (err) => {
      const e = err as { response?: { data?: { detail?: string } } };
      Modal.warning({
        title: '正文生成失败',
        content: e?.response?.data?.detail ?? '生成失败，请稍后重试（指标不受影响）',
        okText: '知道了',
      });
    },
  });

  const doGenerate = useMutation({
    mutationFn: () => generateWeekly({ period: effectivePeriod, use_ai: true }),
    onSuccess: (r) => {
      if (r.ai_body_error) {
        // 指标已汇总，只是正文没生成出来 —— 明说原因，不假装成功
        Modal.warning({
          title: '指标已汇总，但正文未生成',
          content: `${r.ai_body_error}\n\n可点「AI 生成正文」重试，或直接在下方手动撰写。`,
          okText: '知道了',
        });
      } else {
        message.success(
          r.item.generated_by === 'ai' ? '周报已生成（Agently 正文）' : '周报已汇总',
        );
      }
      qc.invalidateQueries({ queryKey: ['weekly-current'] });
      qc.invalidateQueries({ queryKey: ['weekly-team'] });
    },
    onError: (err) => {
      const e = err as { response?: { data?: { detail?: string } } };
      Modal.warning({
        title: '无法生成周报',
        content: e?.response?.data?.detail ?? '生成失败，请稍后重试',
        okText: '知道了',
      });
    },
  });

  const doSave = useMutation({
    mutationFn: () =>
      updateWeekly(item!.id, {
        body: bodyDraft,
        problems,
        next_plan: nextPlan,
      }),
    onSuccess: () => {
      message.success('周报已保存（正文标注为人工编辑）');
      setEditing(false);
      qc.invalidateQueries({ queryKey: ['weekly-current'] });
      qc.invalidateQueries({ queryKey: ['weekly-team'] });
    },
  });

  const doSubmit = useMutation({
    mutationFn: () => submitWeekly(item!.id),
    onSuccess: () => {
      message.success('周报已提交');
      qc.invalidateQueries({ queryKey: ['weekly-current'] });
      qc.invalidateQueries({ queryKey: ['weekly-team'] });
    },
  });

  const readOnly = !!item?.is_submitted;
  const metrics = item?.metrics;
  const stageDistribution: FunnelItem[] = metrics?.stage_distribution ?? [];
  const conversion: ConversionPair[] = metrics?.conversion ?? [];
  const advanced: AdvancedStandard[] = metrics?.advanced_standards ?? [];

  const periodPickerValue = useMemo(
    () => (effectivePeriod ? periodToMonday(effectivePeriod) : null),
    [effectivePeriod],
  );

  const conversionColumns: ColumnsType<ConversionPair> = [    {
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
      title: '实际转化率',
      dataIndex: 'rate',
      width: 120,
      align: 'right',
      render: (v: number, r) => (
        <Typography.Text type={r.alert ? 'danger' : undefined}>{fmtPercent(v)}</Typography.Text>
      ),
    },
    {
      title: '基准',
      dataIndex: 'baseline',
      width: 100,
      align: 'right',
      render: (v: number) => fmtPercent(v),
    },
    {
      title: '差值（百分点）',
      key: 'gap',
      width: 140,
      align: 'right',
      render: (_, r) => (
        <Tooltip title="转化率差值一律用「百分点」表示，例如 30% → 26% 记作 -4.0 个百分点">
          <Typography.Text type={r.gap < 0 ? 'danger' : 'success'}>{pointDiff(r.gap)}</Typography.Text>
        </Tooltip>
      ),
    },
    {
      title: '状态',
      width: 110,
      render: (_, r) => (r.alert ? <Tag color="red">低于预警线</Tag> : <Tag color="green">正常</Tag>),
    },
  ];

  const metricsBlock = (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {/* 判据 C3：指标由宿主计算，每个数字都能就地看到口径 */}
      <Row gutter={[12, 12]}>
        <Col xs={12} md={8} lg={4}>
          <KpiCard
            label="新增有效商机"
            value={metrics?.new_valid_count ?? 0}
            suffix="个"
            icon={<RiseOutlined />}
            tone="green"
            rateText={`新增线索 ${metrics?.new_lead_count ?? 0} 条`}
            hint="口径：本周内状态变为「有效商机」的线索数；新增线索 = 本周创建的全部线索（含后续被判无效的）。指标由宿主按数据库直接汇总，不经过模型。"
          />
        </Col>
        <Col xs={12} md={8} lg={4}>
          <KpiCard
            label="在途金额"
            value={fenToWan(metrics?.total_amount_fen)}
            suffix="万"
            icon={<FileTextOutlined />}
            tone="brand"
            rateText={`在途 ${metrics?.in_progress_count ?? 0} 个`}
            hint="口径：当前处于 7 环节内的商机金额之和（不乘成交概率）；在途个数 = 这些商机的条数。"
          />
        </Col>
        <Col xs={12} md={8} lg={4}>
          <KpiCard
            label="加权预测"
            value={fenToWan(metrics?.weighted_amount_fen)}
            suffix="万"
            icon={<RiseOutlined />}
            tone="purple"
            hint="口径：加权金额 = 环节金额 × 该环节成交概率，逐环节求和；只用于预测，不代表已签约。"
          />
        </Col>
        <Col xs={12} md={8} lg={4}>
          <KpiCard
            label="卡单"
            value={metrics?.stuck_count ?? 0}
            suffix="个"
            icon={<AlertOutlined />}
            tone={(metrics?.stuck_count ?? 0) > 0 ? 'red' : 'gray'}
            danger={(metrics?.stuck_count ?? 0) > 0}
            hint="口径：所处环节停留天数超过该环节停留时长基准的商机数；基准未设（0）的环节不参与判定，基准在「6.5 环节标准卡配置」维护。"
          />
        </Col>
        <Col xs={12} md={8} lg={4}>
          <KpiCard
            label="本周签约"
            value={fenToWan(metrics?.signed_amount_fen)}
            suffix="万"
            icon={<CheckCircleOutlined />}
            tone="green"
            hint="口径：本周内状态变为「已签约」的线索合同金额之和（按签约时间归属本周）。"
          />
        </Col>
        <Col xs={12} md={8} lg={4}>
          <KpiCard
            label="本周回款"
            value={fenToWan(metrics?.paid_amount_fen)}
            suffix="万"
            icon={<WalletOutlined />}
            tone="amber"
            hint="口径：本周登记的回款记录金额之和；回款记录表没有状态字段，全部记录都视为已确认回款。"
          />
        </Col>
      </Row>

      <Row gutter={12}>
        <Col xs={24} lg={12}>
          <Card size="small" title="各环节商机分布">
            <Table<FunnelItem>
              rowKey="stage"
              size="small"
              dataSource={stageDistribution}
              pagination={false}
              locale={{
                emptyText: (
                  <NextStepGuide
                    compact
                    title="暂无在途商机"
                    description="本周没有处于 7 环节内的商机；先在「2.1 商机列表」推进线索到环节一。"
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
              dataSource={conversion}
              columns={conversionColumns}
              pagination={false}
              rowClassName={(r) => (r.alert ? 'weekly-row-alert' : '')}
              locale={{
                emptyText: (
                  <NextStepGuide
                    compact
                    title="暂无转化率数据"
                    description="转化率需要环节之间有推进记录才会产生；推进商机后本表自动出现。"
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

      <Card
        size="small"
        title={
          <Space>
            本周推进的完成标准（含证据）
            <Tag color="geekblue">{advanced.length}</Tag>
          </Space>
        }
      >
        <Table<AdvancedStandard>
          rowKey={(r) => `${r.lead_id}-${r.done_at ?? ''}-${r.standard_content}`}
          size="small"
          dataSource={advanced}
          pagination={{ pageSize: 8, showSizeChanger: false }}
          locale={{
            emptyText: (
              <NextStepGuide
                compact
                title="本周暂无推进记录"
                description="推进记录来自商机环节里勾选「完成标准」并附证据的动作；没有记录通常意味着本周没有推进环节。"
                actions={
                  <Button size="small" href="/opportunities">
                    去 2.1 商机列表推进
                  </Button>
                }
              />
            ),
          }}
          columns={[
            { title: '客户', dataIndex: 'lead_name', width: 170 },
            { title: '环节', dataIndex: 'stage_label', width: 140 },
            { title: '完成标准', dataIndex: 'standard_content', ellipsis: true },
            {
              title: '证据',
              dataIndex: 'evidence',
              ellipsis: true,
              render: (v: string | null) => v || '—',
            },
            { title: '完成人', dataIndex: 'done_by_name', width: 90, render: (v: string | null) => v || '-' },
            {
              title: '完成时间',
              dataIndex: 'done_at',
              width: 140,
              render: (v: string | null) => fmtDateTime(v),
            },
          ]}
        />
      </Card>

      <Card size="small" title={<Space>卡单商机<Tag color="red">{metrics?.stuck_items?.length ?? 0}</Tag></Space>}>
        <Table<StuckItem>
          rowKey="lead_id"
          size="small"
          dataSource={metrics?.stuck_items ?? []}
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
              render: (v: string, r) => <a href={`/opportunities/${r.lead_id}`}>{v}</a>,
            },
            { title: '环节', dataIndex: 'stage_label', width: 160 },
            {
              title: '停留 / 基准',
              width: 140,
              render: (_, r) => (
                <Typography.Text type={r.stay_days > r.base_days ? 'danger' : undefined}>
                  {r.stay_days} / {r.base_days} 天
                </Typography.Text>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  );

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <style>{`.weekly-row-alert > td { background: #fff1f0 !important; }`}</style>

      {/* 判据 C2：一句话说清"这一页先干什么"；周期选择与生成动作收进 actions */}
      <PageHeader
        title="4.4 周报"
        subtitle="先「汇总数据」把本周指标算出来，再按需让 AI 写正文（可编辑），补上本周问题与下周计划后提交。"
        demo
        meta={[
          { label: '周期', value: data?.period_label ?? effectivePeriod ?? '未确定（格式 2026-W40）' },
          { label: '区间', value: `${fmtDate(item?.period_start)} ~ ${fmtDate(item?.period_end)}` },
          { label: '状态', value: item ? (item.is_submitted ? '已提交' : '草稿') : '尚未生成' },
        ]}
        actions={
          <>
            <DatePicker
              picker="week"
              value={periodPickerValue}
              onChange={(v) => v && setPeriod(mondayToPeriod(v))}
              allowClear={false}
            />
            <Input
              style={{ width: 140 }}
              value={periodInput}
              placeholder="2026-W40"
              onChange={(e) => setPeriodInput(e.target.value)}
              onPressEnter={commitPeriod}
              onBlur={commitPeriod}
              suffix={
                <a onClick={commitPeriod} style={{ fontSize: 12 }}>
                  查询
                </a>
              }
            />
            <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
              刷新
            </Button>
            <Tooltip
              title={readOnly ? '周报已提交，不能再重新汇总' : '只按数据库现有数据重算指标，不调用模型'}
            >
              <Button
                icon={<ReloadOutlined />}
                loading={doCollect.isPending}
                disabled={readOnly}
                onClick={() => doCollect.mutate()}
              >
                汇总数据
              </Button>
            </Tooltip>
            <Tooltip
              title={
                readOnly ? '周报已提交，不能再生成' : '先汇总指标，再尽力生成 AI 正文（约 10–30 秒）'
              }
            >
              <Button
                type="primary"
                icon={<ThunderboltOutlined />}
                loading={doGenerate.isPending}
                disabled={readOnly}
                onClick={() => doGenerate.mutate()}
              >
                {item ? '重新汇总 / 生成' : '生成本周周报'}
              </Button>
            </Tooltip>
            {readOnly && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                已提交 → 汇总 / 生成按钮已禁用
              </Typography.Text>
            )}
          </>
        }
      />

      <Alert
        type="info"
        showIcon
        message="汇总指标由宿主计算，不依赖模型；AI 正文由模型生成，可编辑，人工改过不再覆盖"
        description="「汇总数据」只算指标（不依赖模型，随时可用）；「AI 生成正文」调模型写正文（约 10–30 秒）。两者独立：模型不可用时指标照常汇总，正文会明确报错而不会编造。"
      />

      <Card
        loading={isLoading}
        title={
          <Space wrap>
            <span>我的周报</span>
            {item && (
              <Tag color={GENERATED_BY_COLOR[item.generated_by] ?? 'default'}>
                {GENERATED_BY_LABEL[item.generated_by] ?? item.generated_by}
              </Tag>
            )}
            {item?.generated_by === 'pending' && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                指标已汇总、正文未生成 —— 点「AI 生成正文」或直接「编辑正文」自己写
              </Typography.Text>
            )}
            {item?.is_submitted && <Tag color="green">已提交</Tag>}
            {item?.model_used && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                模型：{item.model_used}
              </Typography.Text>
            )}
            {item?.generated_by === 'ai' && <AiDisclaimer text="AI 生成的正文仅供参考，不作为考核依据" />}
          </Space>
        }
        extra={
          item ? (
            <Space>
              {!readOnly && (
                <>
                  <Tooltip title="调用模型生成正文；模型不可用会明确报错，不影响已汇总的指标">
                    <Button
                      icon={<ThunderboltOutlined />}
                      loading={doGenerateBody.isPending}
                      onClick={() => doGenerateBody.mutate(item.id)}
                    >
                      AI 生成正文
                    </Button>
                  </Tooltip>
                  {item.generated_by === 'manual' && (
                    <Tooltip title="人工写过的正文默认不覆盖；打开后重新生成会替换你写的内容">
                      <Checkbox
                        checked={forceRewrite}
                        onChange={(e) => setForceRewrite(e.target.checked)}
                      >
                        允许覆盖人工正文
                      </Checkbox>
                    </Tooltip>
                  )}
                  <Button
                    icon={<EditOutlined />}
                    onClick={() => setEditing((v) => !v)}
                  >
                    {editing ? '预览正文' : '编辑正文'}
                  </Button>
                  <Button
                    icon={<SaveOutlined />}
                    loading={doSave.isPending}
                    onClick={() => doSave.mutate()}
                  >
                    保存
                  </Button>
                  <Button
                    type="primary"
                    icon={<SendOutlined />}
                    loading={doSubmit.isPending}
                    onClick={() =>
                      Modal.confirm({
                        title: '提交本周周报？',
                        content: '提交后不能再编辑（撤回功能在 P2 提供）。',
                        okText: '确认提交',
                        cancelText: '取消',
                        onOk: () => doSubmit.mutate(),
                      })
                    }
                  >
                    提交
                  </Button>
                </>
              )}
              {readOnly && item.submitted_at && (
                <Typography.Text type="secondary">
                  提交时间：{fmtDateTime(item.submitted_at)}
                </Typography.Text>
              )}
            </Space>
          ) : null
        }
      >
        {!item && !isLoading ? (
          <NextStepGuide
            title={`${data?.period_label ?? effectivePeriod ?? '本周'}还没有周报`}
            description="周报由两步组成：①「汇总数据」由宿主按数据库算出本周指标（不调用模型，随时可用）；②「AI 生成正文」由模型写正文，你可以在正文框里直接改，改过之后不会被再次覆盖。"
            actions={
              <Space>
                <Button
                  type="primary"
                  icon={<ThunderboltOutlined />}
                  loading={doGenerate.isPending}
                  onClick={() => doGenerate.mutate()}
                >
                  生成本周周报
                </Button>
                <Button
                  icon={<ReloadOutlined />}
                  loading={doCollect.isPending}
                  onClick={() => doCollect.mutate()}
                >
                  只汇总数据（不调模型）
                </Button>
              </Space>
            }
          />
        ) : (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions size="small" column={{ xs: 1, md: 3 }} bordered>
              <Descriptions.Item label="周期">{item?.period_label ?? '-'}</Descriptions.Item>
              <Descriptions.Item label="区间">
                {fmtDate(item?.period_start)} ~ {fmtDate(item?.period_end)}
              </Descriptions.Item>
              <Descriptions.Item label="撰写人">{item?.user_name ?? '-'}</Descriptions.Item>
              <Descriptions.Item label="最近更新">{fmtDateTime(item?.updated_at)}</Descriptions.Item>
              <Descriptions.Item label="正文来源">
                {GENERATED_BY_LABEL[item?.generated_by ?? ''] ?? item?.generated_by ?? '-'}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                {item?.is_submitted ? '已提交' : '草稿'}
              </Descriptions.Item>
            </Descriptions>

            <Row gutter={12}>
              <Col xs={24} lg={14}>
                <Card
                  size="small"
                  title="周报正文"
                  extra={
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {editing ? '直接编辑会标注为「人工编辑」' : '只读预览'}
                    </Typography.Text>
                  }
                >
                  {editing && !readOnly ? (
                    <Input.TextArea
                      rows={16}
                      value={bodyDraft}
                      onChange={(e) => setBodyDraft(e.target.value)}
                      placeholder="本周经营小结…"
                    />
                  ) : bodyDraft || item?.body ? (
                    <AiContent content={bodyDraft || item?.body} />
                  ) : (
                    <NextStepGuide
                      compact
                      title="指标已汇总，正文尚未生成"
                      description="正文可以两条路：点「AI 生成正文」让模型写（约 10–30 秒），或点「编辑正文」自己写。人工写过的正文默认不会被 AI 覆盖。"
                      actions={
                        <Space>
                          <Button
                            size="small"
                            type="primary"
                            loading={doGenerateBody.isPending}
                            onClick={() => item && doGenerateBody.mutate(item.id)}
                          >
                            AI 生成正文
                          </Button>
                          <Button size="small" onClick={() => setEditing(true)}>
                            自己写
                          </Button>
                        </Space>
                      }
                    />
                  )}
                </Card>
              </Col>
              <Col xs={24} lg={10}>
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  <Card size="small" title="本周问题">
                    <Input.TextArea
                      rows={5}
                      disabled={readOnly}
                      value={problems}
                      onChange={(e) => setProblems(e.target.value)}
                      placeholder="如：客户决策链推进慢、预算未确认"
                    />
                  </Card>
                  <Card size="small" title="下周计划">
                    <Input.TextArea
                      rows={5}
                      disabled={readOnly}
                      value={nextPlan}
                      onChange={(e) => setNextPlan(e.target.value)}
                      placeholder="如：下周争取见到校长，推动预算确认"
                    />
                  </Card>
                </Space>
              </Col>
            </Row>
          </Space>
        )}
      </Card>

      {item && <Card title="本期指标明细">{metricsBlock}</Card>}

      {isManager && (
        <TeamWeeklyCard period={effectivePeriod} onView={setViewing} />
      )}

      {/* 判据 C3 + C4：口径与边界写在页脚 */}
      <Card size="small">
        <Space direction="vertical" size={6} style={{ width: '100%' }}>
          <Footnote>
            口径：① 汇总指标由宿主计算，不依赖模型（模型不可用时指标照常汇总）；② AI 正文由模型生成，可编辑，
            人工改过不再覆盖（需要覆盖必须显式勾选「允许覆盖人工正文」）；③ 「待生成正文」（generated_by=pending）
            表示指标已汇总、正文未生成；④ 转化率差值单位为「百分点」（如 30% → 26% 记作 -4.0 个百分点），
            不写成百分比；⑤ 卡单 = 所处环节停留超该环节停留时长基准，基准未设（0）的环节不参与判定；
            ⑥ 加权预测 = 环节金额 × 该环节成交概率，只用于预测、不代表已签约。
          </Footnote>
          <Footnote>
            边界：AI 生成的正文只是草稿，你可以直接改写或忽略；周报内容用于过程管理与复盘，AI 结论不作为考核依据。
            提交后不能再编辑（撤回功能在 P2 提供）。
          </Footnote>
        </Space>
      </Card>

      <Modal
        open={!!viewing}
        width={860}
        footer={null}
        title={`团队周报 · ${viewing?.user_name ?? ''} · ${viewing?.period_label ?? ''}`}
        onCancel={() => setViewing(null)}
      >
        {viewing && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Space wrap>
              <Tag color={GENERATED_BY_COLOR[viewing.generated_by] ?? 'default'}>
                {GENERATED_BY_LABEL[viewing.generated_by] ?? viewing.generated_by}
              </Tag>
              {viewing.is_submitted ? (
                <Tag color="green">已提交 · {fmtDateTime(viewing.submitted_at)}</Tag>
              ) : (
                <Tag color="orange">未提交</Tag>
              )}
              <Typography.Text type="secondary">
                在途 {fenToWan(viewing.metrics?.total_amount_fen)} 万 · 加权{' '}
                {fenToWan(viewing.metrics?.weighted_amount_fen)} 万 · 回款{' '}
                {formatYuan(viewing.metrics?.paid_amount_fen)} 元
              </Typography.Text>
            </Space>
            <Card size="small" title="周报正文" extra={<AiDisclaimer />}>
              <AiContent content={viewing.body} />
            </Card>
            <Row gutter={12}>
              <Col span={12}>
                <Card size="small" title="本周问题">
                  <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>
                    {viewing.problems || '—'}
                  </Typography.Paragraph>
                </Card>
              </Col>
              <Col span={12}>
                <Card size="small" title="下周计划">
                  <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>
                    {viewing.next_plan || '—'}
                  </Typography.Paragraph>
                </Card>
              </Col>
            </Row>
          </Space>
        )}
      </Modal>
    </Space>
  );
}

/** 团队周报汇总（管理人员）：谁交了 / 谁没交 + 关键指标 + 可代生成 */
function TeamWeeklyCard({
  period,
  onView,
}: {
  period?: string;
  onView: (r: WeeklyReport) => void;
}) {
  const qc = useQueryClient();

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['weekly-team', period],
    queryFn: () => fetchTeamWeekly(period),
  });

  const doGenerateFor = useMutation({
    mutationFn: (userId: string) => generateWeekly({ period, user_id: userId, use_ai: true }),
    onSuccess: (r) => {
      if (r.ai_body_error) {
        Modal.warning({
          title: '指标已汇总，但正文未生成',
          content: `${r.ai_body_error}\n\n指标不受影响，可稍后重试或让本人手动撰写。`,
          okText: '知道了',
        });
      } else {
        message.success('已生成该成员周报');
      }
      qc.invalidateQueries({ queryKey: ['weekly-team'] });
    },
    onError: (err) => {
      const e = err as { response?: { data?: { detail?: string } } };
      Modal.warning({
        title: '无法生成',
        content: e?.response?.data?.detail ?? '生成失败',
        okText: '知道了',
      });
    },
  });

  return (
    <Card
      title={
        <Space>
          团队周报汇总
          <Tag color="blue">
            {data?.submitted ?? 0} / {data?.total ?? 0} 已提交
          </Tag>
        </Space>
      }
      extra={
        <Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {data?.period_label ?? period ?? '-'}
          </Typography.Text>
          <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
            刷新
          </Button>
        </Space>
      }
    >
      <Table<WeeklyReport>
        rowKey="id"
        size="small"
        loading={isLoading}
        dataSource={data?.items ?? []}
        pagination={false}
        locale={{
          emptyText: (
            <NextStepGuide
              compact
              title="该周期还没有任何周报"
              description="团队成员尚未生成该周期周报。可以让本人到本页「生成本周周报」，或在这里直接为该成员「生成/刷新」。"
            />
          ),
        }}
        columns={[
          { title: '成员', dataIndex: 'user_name', width: 110 },
          {
            title: '新增有效',
            width: 100,
            align: 'right',
            render: (_, r) => r.metrics?.new_valid_count ?? 0,
          },
          {
            title: '在途金额(万)',
            width: 130,
            align: 'right',
            render: (_, r) => fenToWan(r.metrics?.total_amount_fen),
          },
          {
            title: '加权预测(万)',
            width: 130,
            align: 'right',
            render: (_, r) => fenToWan(r.metrics?.weighted_amount_fen),
          },
          {
            title: '卡单',
            width: 80,
            align: 'right',
            render: (_, r) => r.metrics?.stuck_count ?? 0,
          },
          {
            title: '回款(元)',
            width: 130,
            align: 'right',
            render: (_, r) => formatYuan(r.metrics?.paid_amount_fen),
          },
          {
            title: '状态',
            width: 180,
            render: (_, r) =>
              r.is_submitted ? (
                <Tag color="green">已提交 · {fmtDateTime(r.submitted_at)}</Tag>
              ) : (
                <Tag color="orange">未提交</Tag>
              ),
          },
          {
            title: '操作',
            width: 170,
            render: (_, r) => (
              <Space size={4}>
                <Button type="link" size="small" onClick={() => onView(r)}>
                  查看
                </Button>
                <Button
                  type="link"
                  size="small"
                  disabled={r.is_submitted}
                  loading={doGenerateFor.isPending}
                  onClick={() => doGenerateFor.mutate(r.user_id)}
                >
                  生成/刷新
                </Button>
              </Space>
            ),
          },
        ]}
      />
    </Card>
  );
}
