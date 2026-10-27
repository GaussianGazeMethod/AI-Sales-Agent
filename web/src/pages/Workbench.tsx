import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  Button,
  Card,
  Col,
  Empty,
  List,
  Row,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  AlertOutlined,
  CheckSquareOutlined,
  ClockCircleOutlined,
  FileTextOutlined,
  FundOutlined,
  PlusOutlined,
  RobotOutlined,
  ThunderboltOutlined,
  WalletOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import { fetchWorkbench } from '@/api/dashboard';
import { doneReminder } from '@/api/admin';
import { listDrafts } from '@/api/ai';
import { fetchPaymentSummary } from '@/api/reports';
import PageHeader from '@/components/PageHeader';
import KpiCard from '@/components/KpiCard';
import GateBanner from '@/components/GateBanner';
import SimpleBar from '@/components/SimpleBar';
import { AiDisclaimer, Footnote, NextStepGuide, PanelFlag } from '@/components/Guide';
import { FollowCountdown, LeadStatusTag } from '@/components/tags';
import { useAuthStore } from '@/store/auth';
import { ROLE_LABEL } from '@/utils/constants';
import { fenToWan, fmtDateTime, fmtPercent } from '@/utils/format';
import type { AchievementMetric, Lead, Reminder, WorkbenchResp } from '@/types';

/* ══════════════════════════════════════════════════════════════════════
 * 0.2 我的工作台（设计稿 01）
 *
 * 判据对照：
 * - C1 少填：待办行直接带「下一步行动 + 直达按钮」，不用再去列表里搜；
 *           AI 草稿标题/时间直接读草稿数据，无需二次录入。
 * - C2 少判断：每个空态/缺口都写清「为什么 + 缺什么 + 怎么办」，并给直达入口。
 * - C3 口径可见：KPI hint + 截止时间 tooltip + 页脚 ⓘ，全部就地可见；
 *           **不编成功率**：AI 相关只给免责标识与"未确认不生效"，不给任何 AI 命中率。
 * - C4 赋能边界：AI 草稿区标「未确认不生效」+ 免责标识，确认权在人手里。
 * ══════════════════════════════════════════════════════════════════════ */

/** 待办分组：逾期 > 今日 > 即将超时 > 待确认 > 其它（设计稿排序口径） */
type TodoBucket = 'overdue' | 'today' | 'due' | 'pending' | 'other';

interface TodoRow {
  key: string;
  /** 提醒 id：用于「完成」按钮（保留原工作台的直接完成能力） */
  reminderId?: string;
  leadId: string | null;
  leadName: string;
  /** 下一步行动：必须是**具体动作短语**，不要只写"跟进" */
  action: string;
  typeLabel: string;
  deadlineText: string;
  deadlineLevel: 'danger' | 'warning' | 'muted';
  /** 截止时间口径说明（tooltip，判据 C3） */
  deadlineHint: string;
  bucket: TodoBucket;
  /** 后端原始顺序：同组内保持稳定（前端稳定排序） */
  order: number;
  opLabel: string;
  to: string;
}

const ACTION_BY_TYPE: Record<string, string> = {
  first_follow_due: '首次电话触达',
  first_follow_overdue: '首次电话触达',
  next_follow_due: '按期回访客户',
  recycle_due: '恢复跟进（避免回收）',
  recycled: '重新领取并跟进',
  pending_confirm: '确认 AI 更新草稿',
};

const BUCKET_RANK: Record<TodoBucket, number> = {
  overdue: 0,
  today: 1,
  due: 2,
  pending: 3,
  other: 4,
};

const LEVEL_COLOR: Record<TodoRow['deadlineLevel'], string> = {
  danger: '#F04438',
  warning: '#F79009',
  muted: '#667085',
};

/** 提醒类型 → 操作按钮（直达，不让人自己找入口） */
function opOf(r: Reminder): { label: string; to: string } {
  if (r.type === 'pending_confirm') return { label: '查看草稿', to: '/ai/drafts' };
  if (r.lead_id) return { label: '去跟进', to: `/leads/${r.lead_id}` };
  return { label: '去待办中心', to: '/todos' };
}

/**
 * 截止时间分级（判据 C3：口径写在 tooltip 里，不虚构倒计时）
 * - 后端 Reminder 只有 remind_at（提醒生成时刻），没有截止时刻字段；
 *   因此这里只做**基于提醒生成时刻的显式推算**，并把算法写在 tooltip 上。
 * - 首次跟进按**工作日**计时，商机停留按**自然日**计时（页脚 ⓘ）。
 */
function deadlineOf(r: Reminder): Pick<TodoRow, 'deadlineText' | 'deadlineLevel' | 'bucket' | 'deadlineHint'> {
  const remindAt = r.remind_at;
  const hoursSince = Math.max(0, dayjs().diff(dayjs(remindAt), 'hour', true));
  const daysSince = Math.max(1, Math.floor(dayjs().diff(dayjs(remindAt), 'day', true)));

  if (r.type === 'first_follow_overdue' || r.type === 'recycled') {
    return {
      deadlineText: `逾期 ${daysSince} 天`,
      deadlineLevel: 'danger',
      bucket: 'overdue',
      deadlineHint: `自该提醒生成（${fmtDateTime(remindAt)}）起已 ${daysSince} 天未处理；首次跟进按工作日计时。`,
    };
  }
  if (r.type === 'recycle_due') {
    return {
      deadlineText: '即将回收',
      deadlineLevel: 'warning',
      bucket: 'due',
      deadlineHint: '已接近 7 个工作日未跟进，回收后线索回到未指派池（可在待办中心处理）。',
    };
  }
  if (r.type === 'next_follow_due') {
    const diffHours = dayjs(remindAt).diff(dayjs(), 'hour', true);
    if (diffHours <= 0) {
      return {
        deadlineText: `逾期 ${Math.max(1, Math.floor(Math.abs(diffHours) / 24))} 天`,
        deadlineLevel: 'danger',
        bucket: 'overdue',
        deadlineHint: `约定的下次跟进时间 ${fmtDateTime(remindAt)} 已过，按自然日折算逾期天数。`,
      };
    }
    if (dayjs(remindAt).isSame(dayjs(), 'day')) {
      return { deadlineText: '今日', deadlineLevel: 'danger', bucket: 'today', deadlineHint: `约定的下次跟进时间：${fmtDateTime(remindAt)}。` };
    }
    return {
      deadlineText: `剩余 ${Math.max(1, Math.round(diffHours / 24))} 天`,
      deadlineLevel: 'warning',
      bucket: 'due',
      deadlineHint: `约定的下次跟进时间：${fmtDateTime(remindAt)}。`,
    };
  }
  if (r.type === 'pending_confirm') {
    return {
      deadlineText: '待确认',
      deadlineLevel: 'warning',
      bucket: 'pending',
      deadlineHint: `AI 草稿生成于 ${fmtDateTime(remindAt)}；未确认不生效，确认权在你。`,
    };
  }
  if (r.type === 'first_follow_due') {
    // DUE_SOON_HOURS = 24：提醒在截止前 24 小时内生成 → 剩余 ≈ 24 - 已过时长
    const remain = 24 - hoursSince;
    if (remain <= 0) {
      return { deadlineText: '今日', deadlineLevel: 'danger', bucket: 'today', deadlineHint: '首次跟进 1 个工作日的时限已到临界，请今天完成首次触达。' };
    }
    return {
      deadlineText: `剩余约 ${Math.max(1, Math.round(remain))} 工作小时`,
      deadlineLevel: 'warning',
      bucket: 'due',
      deadlineHint: `提醒生成于 ${fmtDateTime(remindAt)}；首次跟进以 1 个工作日为限（按工作日计时）。`,
    };
  }
  return {
    deadlineText: fmtDateTime(remindAt),
    deadlineLevel: 'muted',
    bucket: 'other',
    deadlineHint: `提醒生成时间：${fmtDateTime(remindAt)}。`,
  };
}

/** 后端 Reminder 还带 type_label（前端类型未声明，页面内做只读扩展） */
type ReminderWithLabel = Reminder & { type_label?: string };

/** 待办：提醒 + 卡单（环节停滞）→ 统一排序口径的一行 */
function buildTodoRows(reminders: Reminder[], stuck: Lead[]): TodoRow[] {
  const rows: TodoRow[] = [];
  const seen = new Set<string>();

  reminders.forEach((raw, index) => {
    const r = raw as ReminderWithLabel;
    // 同一客户 + 同一提醒类型只留最早一条（SLA 每天重复生成，避免刷屏）
    const dedupKey = `${r.lead_id ?? 'none'}|${r.type}`;
    if (seen.has(dedupKey)) return;
    seen.add(dedupKey);
    const d = deadlineOf(r);
    const op = opOf(r);
    rows.push({
      key: `reminder-${r.id}`,
      reminderId: r.id,
      leadId: r.lead_id,
      leadName: r.lead_name || r.content.slice(0, 18),
      action: ACTION_BY_TYPE[r.type] ?? '处理该待办',
      typeLabel: r.type_label ?? r.type,
      order: index,
      opLabel: op.label,
      to: op.to,
      ...d,
    });
  });

  stuck.forEach((lead, index) => {
    const stay = lead.stage_stay_days ?? 0;
    const base = lead.stage_base_days ?? 0;
    const over = Math.max(0, stay - base);
    rows.push({
      key: `stuck-${lead.id}`,
      leadId: lead.id,
      leadName: lead.name,
      action: `推进当前环节（已停留 ${stay} 天）`,
      typeLabel: lead.current_stage_label || '环节卡单',
      deadlineText: base ? `基准 ${base} 自然日` : '超过基准',
      deadlineLevel: 'muted',
      deadlineHint: `本环节停留 ${stay} 天，基准 ${base} 自然日，已超 ${over} 天；商机停留按自然日计时。`,
      bucket: 'overdue',
      order: 1000 + index,
      opLabel: '查看商机',
      to: `/opportunities/${lead.id}`,
    });
  });

  // 稳定排序：逾期 > 今日 > 即将超时 > 待确认 > 其它；同组按后端原始顺序
  return rows.sort(
    (a, b) => BUCKET_RANK[a.bucket] - BUCKET_RANK[b.bucket] || a.order - b.order,
  );
}

/** 风险预警（后端 risk_items 的结构，页面内只做只读呈现） */
interface RiskItem {
  id: string;
  lead_id: string | null;
  kind_label?: string;
  risk_label?: string;
  description?: string;
  trigger_rule?: string;
  status_label?: string;
}

export default function Workbench() {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const qc = useQueryClient();
  const [tab, setTab] = useState<'all' | 'urgent' | 'pending'>('all');

  const { data: rawData, isLoading } = useQuery({ queryKey: ['workbench'], queryFn: fetchWorkbench });
  /** 后端还返回 period / rhythm_window（前端类型未声明，页面内做只读扩展） */
  const data = rawData as (WorkbenchResp & { period?: string }) | undefined;

  /** KPI「签单金额 / 回款金额」口径：2.6 回款汇总（后端已按数据权限收窄） */
  const { data: payments } = useQuery({
    queryKey: ['payments-summary', 'workbench'],
    queryFn: () => fetchPaymentSummary(),
  });

  /** 待确认 AI 草稿：直接读草稿本身（标题/生成时间/摘要都来自草稿） */
  const { data: pendingDrafts } = useQuery({
    queryKey: ['ai-drafts', { status: 'pending', page: 1, page_size: 5 }],
    queryFn: () => listDrafts({ status: 'pending', page: 1, page_size: 5 }),
  });

  const onDone = async (id: string) => {
    await doneReminder(id);
    message.success('待办已完成（仅关闭提醒）');
    void qc.invalidateQueries({ queryKey: ['workbench'] });
  };

  const role = data?.role ?? user?.role ?? '';
  const isManager = role === 'market_manager' || role === 'admin';
  const isExecutive = role === 'executive';

  const reminders = useMemo(() => data?.todo_items ?? [], [data]);
  const stuck = useMemo(() => data?.stuck_items ?? [], [data]);
  const rows = useMemo(() => buildTodoRows(reminders, stuck), [reminders, stuck]);

  const urgentRows = useMemo(
    () => rows.filter((r) => r.bucket === 'overdue' || r.bucket === 'today' || r.bucket === 'due'),
    [rows],
  );
  const pendingRows = useMemo(() => rows.filter((r) => r.bucket === 'pending'), [rows]);

  const tabRows = tab === 'urgent' ? urgentRows : tab === 'pending' ? pendingRows : rows;

  const metricOf = (m: string): AchievementMetric | undefined =>
    (data?.my_achievement ?? []).find((x) => x.metric === m);

  const validMetric = metricOf('valid_lead_count');
  const validRate = validMetric?.achievement_rate ?? 0;

  const funnel = data?.funnel ?? [];
  const team = data?.team_overview;
  const period = data?.period ?? '';
  const riskItems = (data?.risk_items ?? []) as RiskItem[];
  const draftItems = pendingDrafts?.items ?? [];
  const pendingDraftCount = data?.pending_drafts ?? draftItems.length;

  const columns: ColumnsType<TodoRow> = [
    {
      title: '学校名称',
      dataIndex: 'leadName',
      width: 200,
      ellipsis: true,
      render: (v: string, r) => (
        <Space direction="vertical" size={0}>
          <a onClick={() => navigate(r.to)}>{v}</a>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {r.typeLabel}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '下一步行动',
      dataIndex: 'action',
      ellipsis: true,
      render: (v: string) => <Typography.Text strong>{v}</Typography.Text>,
    },
    {
      title: '截止时间',
      dataIndex: 'deadlineText',
      width: 150,
      render: (v: string, r) => (
        <Tooltip title={r.deadlineHint}>
          <span style={{ color: LEVEL_COLOR[r.deadlineLevel], fontWeight: r.deadlineLevel === 'muted' ? 400 : 600 }}>
            {v}
          </span>
        </Tooltip>
      ),
    },
    {
      title: '操作',
      key: 'op',
      width: 150,
      render: (_, r) => (
        <Space size={8}>
          <Button
            size="small"
            type={r.bucket === 'overdue' || r.bucket === 'today' ? 'primary' : 'default'}
            onClick={() => navigate(r.to)}
          >
            {r.opLabel}
          </Button>
          {r.reminderId && (
            <Tooltip title="仅关闭这条提醒，不改动商机数据（记录仍可在待办中心查看）">
              <a onClick={() => onDone(r.reminderId as string)}>完成</a>
            </Tooltip>
          )}
        </Space>
      ),
    },
  ];

  const stuckColumns: ColumnsType<Lead> = [
    { title: '客户名称', dataIndex: 'name', ellipsis: true },
    { title: '归属人', dataIndex: 'owner_name', width: 90, render: (v) => v || '-' },
    { title: '状态', dataIndex: 'status', width: 100, render: (v) => <LeadStatusTag status={v} /> },
    {
      title: '环节停留',
      dataIndex: 'stage_stay_days',
      width: 110,
      render: (v: number | null, r) =>
        v === null || v === undefined ? (
          '-'
        ) : (
          <Tooltip title={`本环节基准 ${r.stage_base_days ?? '-'} 自然日`}>
            <span style={{ color: r.stage_overdue ? '#F04438' : undefined }}>
              {v} 天{r.stage_overdue ? '（超基准）' : ''}
            </span>
          </Tooltip>
        ),
    },
    {
      title: '首次跟进',
      key: 'countdown',
      width: 120,
      render: (_, r) => (
        <FollowCountdown
          assignAt={r.assign_at}
          firstFollowAt={r.first_follow_at}
          deadline={r.first_follow_deadline}
        />
      ),
    },
    {
      title: '操作',
      key: 'op',
      width: 110,
      render: (_, r) => (
        <Space size={4}>
          <a onClick={() => navigate(`/opportunities/${r.id}`)}>查看</a>
          <a onClick={() => navigate(`/leads/${r.id}`)}>去跟进</a>
        </Space>
      ),
    },
  ];

  if (isLoading) {
    return (
      <Card>
        <Spin />
      </Card>
    );
  }

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <PageHeader
        title="我的工作台"
        subtitle="优先处理即将超时的跟进与待确认草稿。"
        demo
        meta={[
          { label: '角色', value: <Tag color="blue">{ROLE_LABEL[role] ?? role}</Tag> },
          { label: '当前周期', value: period || '-' },
          { label: '待办', value: `${data?.todo_count ?? 0} 条` },
        ]}
        actions={
          <Space>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/leads/new')}>
              新增线索
            </Button>
            <Button icon={<ThunderboltOutlined />} onClick={() => navigate('/ai/chat')}>
              AI 快速录入
            </Button>
          </Space>
        }
      />

      {/* ① 4 个 KPI：口径全部写在 hint 里（判据 C3） */}
      <Row gutter={[12, 12]}>
        <Col xs={24} sm={12} xl={6}>
          <KpiCard
            label={isManager || isExecutive ? '团队有效商机' : '本月有效商机'}
            value={validMetric ? validMetric.actual_value : '—'}
            total={validMetric ? `/ ${validMetric.target_value}` : undefined}
            icon={<FileTextOutlined />}
            tone="brand"
            progress={validMetric ? Math.min(Math.round(validRate * 100), 100) : undefined}
            rateText={validMetric ? `达成 ${fmtPercent(validRate)}` : '本月未设定目标'}
            hint={
              validMetric
                ? `有效商机 = 状态为「有效商机」的线索数；目标来自 1.8 市场目标设定（周期 ${period}）。`
                : `当前周期（${period}）未设定有效商机目标 → 去「1.8 市场目标设定」设定后这里自动出现达成率。`
            }
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <KpiCard
            label={isManager || isExecutive ? '团队签单金额' : '签单金额'}
            value={`¥${fenToWan(payments?.contracted_fen)}`}
            suffix="万"
            icon={<WalletOutlined />}
            tone="green"
            rateText={payments ? `已签约 ${payments.signed_count} 个项目` : undefined}
            hint="签单金额 = 合同额合计，来自 2.6 回款汇总（口径与回款页一致；未签约不计入）。"
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <KpiCard
            label={isManager || isExecutive ? '团队回款金额' : '回款金额'}
            value={`¥${fenToWan(payments?.paid_fen)}`}
            suffix="万"
            icon={<FundOutlined />}
            tone="purple"
            rateText={payments ? `本期回款 ${fenToWan(payments.period_paid_fen)} 万` : undefined}
            hint="回款金额 = 已回款合计，来自 2.6 回款记录汇总；本期回款按回款日期落在当前周期内统计。"
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <KpiCard
            label="待办"
            value={data?.todo_count ?? 0}
            icon={<CheckSquareOutlined />}
            tone="amber"
            danger={(data?.todo_count ?? 0) > 0}
            rateText={`逾期/即将超时 ${urgentRows.length} 条`}
            hint="待办 = 指派给我的待处理提醒（SLA 扫描生成）。下方「我的待办」按逾期 → 今日 → 即将超时排序。"
          />
        </Col>
      </Row>

      {!validMetric && (
        <NextStepGuide
          compact
          title="本月尚未设定有效商机目标"
          description={`当前周期 ${period} 没有目标，达成率无法计算。目标设定在 1.8 市场目标设定，设好之后本页与 2.8 我的业绩会自动出现达成进度。`}
          actions={
            <Button type="primary" size="small" onClick={() => navigate('/targets')}>
              去设定目标
            </Button>
          }
        />
      )}

      {/* ② 我的待办（左 2/3） + 我的商机环节分布（右 1/3） */}
      <Row gutter={[12, 12]}>
        <Col xs={24} xl={16}>
          <Card
            title={
              <Space>
                <ClockCircleOutlined /> 我的待办
              </Space>
            }
            extra={
              <Space size={8}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  已按客户+类型去重
                </Typography.Text>
                <a onClick={() => navigate('/todos')}>全部待办（{data?.todo_count ?? 0}）</a>
              </Space>
            }
          >
            {!rows.length ? (
              <NextStepGuide
                title="当前没有待处理事项"
                description="首次跟进与回收提醒由系统按 SLA 扫描生成（管理员可手工触发扫描）；有新指派或 AI 草稿时这里会出现待办。"
                actions={
                  <Button size="small" onClick={() => navigate('/leads')}>
                    去线索池看看
                  </Button>
                }
              />
            ) : (
              <>
                <Tabs
                  activeKey={tab}
                  onChange={(k) => setTab(k as typeof tab)}
                  items={[
                    { key: 'all', label: `全部 ${rows.length}` },
                    { key: 'urgent', label: `即将超时 ${urgentRows.length}` },
                    { key: 'pending', label: `待确认 ${pendingRows.length}` },
                  ]}
                />
                <Table<TodoRow>
                  rowKey="key"
                  size="small"
                  columns={columns}
                  dataSource={tabRows}
                  pagination={tabRows.length > 10 ? { pageSize: 10, size: 'small' } : false}
                  locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该分组下暂无待办" /> }}
                />
              </>
            )}
          </Card>
        </Col>

        <Col xs={24} xl={8}>
          <Card
            title={
              <Space>
                <FundOutlined /> 我的商机环节分布
              </Space>
            }
            extra={<a onClick={() => navigate('/opportunities')}>全部商机</a>}
            style={{ height: '100%' }}
          >
            <SimpleBar
              items={funnel.map((f) => ({
                label: f.label || f.stage,
                value: f.count,
                extra: `预算 ${fenToWan(f.amount_fen)} 万`,
              }))}
              color="#465FFF"
              emptyText="暂无在途商机"
            />
            <Footnote style={{ marginTop: 10 }}>
              口径：各环节商机数（在途 = 状态为「有效商机 / 跟进中」，不含丢单与已签约）；金额为该环节预算金额合计。
            </Footnote>
          </Card>
        </Col>
      </Row>

      {/* ③ 卡单提示（缺什么 + 帮我分析） + 待确认 AI 草稿（未确认不生效） */}
      <Row gutter={[12, 12]}>
        <Col xs={24} xl={14}>
          <Card
            title={
              <Space>
                <AlertOutlined style={{ color: '#F79009' }} /> 卡单提示（{stuck.length}）
              </Space>
            }
            extra={<a onClick={() => navigate('/risks')}>风险与卡单中心</a>}
            style={{ height: '100%' }}
          >
            {!stuck.length ? (
              <Typography.Text type="secondary">
                当前没有环节停留超基准的商机（判定口径：环节停留天数 &gt; 该环节基准天数，已签约不算卡单）。
              </Typography.Text>
            ) : (
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                {stuck.slice(0, 3).map((lead) => {
                  const stay = lead.stage_stay_days ?? 0;
                  const base = lead.stage_base_days ?? 0;
                  const over = Math.max(1, stay - base);
                  const related = riskItems.filter((r) => r.lead_id === lead.id);
                  const items: string[] = [
                    `停留 ${stay} 天，超过「${lead.current_stage_label || '当前环节'}」基准 ${base} 自然日（超 ${over} 天）`,
                  ];
                  if (!lead.rhythm_clear) items.push('下一关键节点或预计时间尚未明确，或节点到期待确认（R5）');
                  (lead.risk_labels ?? []).forEach((label) => items.push(`存在业务风险：${label}`));
                  related.slice(0, 2).forEach((r) => {
                    items.push(`${r.kind_label ?? '预警'}：${r.description ?? r.trigger_rule ?? '-'}`);
                  });
                  return (
                    <GateBanner
                      key={lead.id}
                      title={`${lead.name} 超期 ${over} 自然日`}
                      items={items}
                      action={
                        <Space size={8} wrap>
                          <Button
                            size="small"
                            type="primary"
                            onClick={() => navigate(`/ai/chat?lead=${lead.id}`)}
                          >
                            帮我分析
                          </Button>
                          <Button size="small" onClick={() => navigate(`/opportunities/${lead.id}`)}>
                            查看商机
                          </Button>
                        </Space>
                      }
                    />
                  );
                })}
                {stuck.length > 3 && (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    仅显示前 3 条，其余 {stuck.length - 3} 条见「风险与卡单中心」。
                  </Typography.Text>
                )}
              </Space>
            )}
          </Card>
        </Col>

        <Col xs={24} xl={10}>
          <Card
            title={
              <Space>
                <RobotOutlined /> 待确认 AI 草稿（{pendingDraftCount}）
              </Space>
            }
            extra={
              <Space size={8}>
                <PanelFlag>未确认不生效</PanelFlag>
                <a onClick={() => navigate('/ai/drafts')}>全部</a>
              </Space>
            }
            style={{ height: '100%' }}
          >
            {!draftItems.length ? (
              <NextStepGuide
                title={pendingDraftCount ? '草稿列表加载中或已处理' : '暂无待确认草稿'}
                description="在 5.1 对话与录入里发一段拜访/沟通内容，AI 会生成更新草稿；草稿必须由你确认后才写入业务数据。"
                actions={
                  <Button size="small" type="primary" onClick={() => navigate('/ai/chat')}>
                    AI 快速录入
                  </Button>
                }
              />
            ) : (
              <List
                size="small"
                dataSource={draftItems}
                renderItem={(d) => (
                  <List.Item
                    actions={[
                      <Button key="view" size="small" onClick={() => navigate('/ai/drafts')}>
                        查看草稿
                      </Button>,
                    ]}
                  >
                    <List.Item.Meta
                      title={
                        <Typography.Text strong>
                          {d.lead_name || '未定位客户'} · {d.summary.slice(0, 16)}
                          {d.summary.length > 16 ? '…' : ''}
                        </Typography.Text>
                      }
                      description={
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          生成时间：{fmtDateTime(d.created_at)}
                        </Typography.Text>
                      }
                    />
                  </List.Item>
                )}
              />
            )}
            <div style={{ marginTop: 8 }}>
              <AiDisclaimer text="AI 只出草稿，确认权在你" />
            </div>
          </Card>
        </Col>
      </Row>

      {/* ④ 角色相关：团队概览 / 我的目标达成 / 未指派池 / 风险预警（保留原有能力，不降级） */}
      {(isManager || isExecutive) && team && (
        <Card size="small" title={`团队概览（${period}）`}>
          <Row gutter={[12, 12]}>
            {[
              { label: '本周新增有效', value: `${team.new_valid_count} 条` },
              { label: '累计有效商机', value: `${team.total_valid_count} 条` },
              { label: '加权预测', value: `${fenToWan(team.weighted_amount_fen)} 万` },
              { label: '签单金额', value: `${fenToWan(team.signed_amount_fen)} 万` },
              { label: '回款金额', value: `${fenToWan(team.paid_amount_fen)} 万` },
            ].map((s) => (
              <Col xs={12} md={8} xl={4} key={s.label}>
                <div className="kpi-label">{s.label}</div>
                <div className="kpi-value" style={{ fontSize: 20 }}>
                  {s.value}
                </div>
              </Col>
            ))}
          </Row>
          <Footnote style={{ marginTop: 10 }}>
            口径：加权预测 = Σ（预算金额 × 成交概率）；签单/回款来自 2.6 回款记录，团队范围按你的数据权限收窄。
          </Footnote>
        </Card>
      )}

      {!isManager && !isExecutive && (data?.my_achievement?.length ?? 0) > 0 && (
        <Card size="small" title={`我的目标达成（${period}）`}>
          <Row gutter={[12, 12]}>
            {(data?.my_achievement ?? []).map((m) => {
              const rate = Math.min(Math.round((m.achievement_rate || 0) * 100), 100);
              return (
                <Col xs={24} md={8} key={m.metric}>
                  <KpiCard
                    label={m.label ?? m.metric}
                    value={m.metric === 'valid_rate' ? fmtPercent(m.actual_value) : m.actual_value}
                    total={`/ ${m.metric === 'valid_rate' ? fmtPercent(m.target_value) : m.target_value}`}
                    progress={rate}
                    rateText={`达成 ${fmtPercent(m.achievement_rate)}`}
                    hint="目标来自 1.8 市场目标设定；达成率 = 实际 / 目标。"
                  />
                </Col>
              );
            })}
          </Row>
        </Card>
      )}

      {(isManager || isExecutive) && (
        <Card
          size="small"
          title="未指派池"
          extra={
            <Space>
              <Tag color={data?.unassigned_count ? 'orange' : 'default'}>
                待分配 {data?.unassigned_count ?? 0}
              </Tag>
              <Button size="small" type="primary" onClick={() => navigate('/assign')}>
                去分配
              </Button>
            </Space>
          }
        >
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            未指派线索不归任何人，超过 SLA 会持续堆积；分配时可在 1.2 线索池与分配看到每人负载参考。
          </Typography.Text>
        </Card>
      )}

      <Card
        size="small"
        title={
          <Space>
            <ClockCircleOutlined style={{ color: '#F79009' }} />
            卡单 / 超时清单（{stuck.length}）
          </Space>
        }
      >
        {stuck.length === 0 ? (
          <Typography.Text type="secondary">
            暂无卡单。判定口径：环节停留天数 &gt; 该环节基准天数（已签约不算卡单）。
          </Typography.Text>
        ) : (
          <Table
            rowKey="id"
            size="small"
            columns={stuckColumns}
            dataSource={stuck}
            pagination={false}
            scroll={{ x: 720 }}
          />
        )}
      </Card>

      {!!riskItems.length && (
        <Card size="small" title={`风险与预警（${riskItems.length}）`} extra={<a onClick={() => navigate('/risks')}>去风险中心</a>}>
          <List
            size="small"
            dataSource={riskItems}
            renderItem={(r) => (
              <List.Item
                actions={[
                  r.lead_id ? (
                    <a key="go" onClick={() => navigate(`/opportunities/${r.lead_id}`)}>
                      查看商机
                    </a>
                  ) : null,
                ]}
              >
                <List.Item.Meta
                  title={
                    <Space size={6} wrap>
                      <Tag color="orange">{r.kind_label ?? '预警'}</Tag>
                      {r.risk_label && <Tag color="volcano">{r.risk_label}</Tag>}
                      <Typography.Text>{r.description ?? '-'}</Typography.Text>
                    </Space>
                  }
                  description={
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      触发规则：{r.trigger_rule ?? '-'} · 状态：{r.status_label ?? '-'}
                    </Typography.Text>
                  }
                />
              </List.Item>
            )}
          />
        </Card>
      )}

      <Footnote>
        ⓘ 首次跟进按<b>工作日</b>计时；商机停留按<b>自然日</b>计时。待办「逾期 N 天」自该提醒生成时间起算，
        精确截止时刻以商机详情/待办中心为准。AI 产出仅为草稿，确认后才生效，且不作为考核依据。
      </Footnote>
    </Space>
  );
}
