import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Alert,
  Button,
  Card,
  Input,
  InputNumber,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  AlertOutlined,
  CalendarOutlined,
  ClockCircleOutlined,
  ExclamationCircleFilled,
  FilterOutlined,
  FundOutlined,
  ProjectOutlined,
  ReloadOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { fetchStaffOptions } from '@/api/dashboard';
import { listOpportunities } from '@/api/opportunities';
import { LeadStatusTag } from '@/components/tags';
import PageHeader from '@/components/PageHeader';
import { Footnote, NextStepGuide, PanelFlag } from '@/components/Guide';
import { BRAND, SUCCESS, WARNING } from '@/theme';
import { fenToWan, fmtDate, fmtDateTime, fmtPercent, formatYuan, yuanToFen } from '@/utils/format';
import type { Lead } from '@/types';

const PAGE_SIZE = 10;

/**
 * 2.1 商机列表（对齐设计稿 01/02 的骨架）。
 *
 * 骨架：标题与筛选**同一行** + 一行大数字 KPI + 表内行内证据/链接（点得到详情页对应区块）。
 *
 *  筛选口径（诚实底线，判据 C3）：
 * 后端 `GET /opportunities` 只支持 `keyword` + 分页（见 backend/app/api/opportunities.py），
 * 默认排序为「当前环节进入时间升序」= 停留最久优先。
 * 因此**关键词是服务端全量筛选**，而「环节 / 停留天数 / 金额区间 / 风险标记 / 负责人 / 排序」
 * 都是**对当前页数据的前端筛选与排序**——界面上必须写清"仅对当前页"，不能伪装成全量口径。
 */

/** 闭环二 7 环节（与后端 app/models/enums.py 的 Stage 枚举一致；后端无环节字典下发，这里内置一份） */
const STAGE_OPTIONS: { value: string; label: string }[] = [
  { value: 's1_valid_opportunity', label: '一 有效商机' },
  { value: 's2_customer_reached', label: '二 客户触达' },
  { value: 's3_proposal_quote', label: '三 方案报价' },
  { value: 's4_budget_confirmed', label: '四 预算确认' },
  { value: 's5_project_reviewed', label: '五 项目过会' },
  { value: 's6_bidding', label: '六 招标采购' },
  { value: 's7_contract_signed', label: '七 合同签约' },
];

/** 停留天数筛选（spec 2.1「停留天数」）。「已超期」= 停留 > 该环节基准天数 */
const STAY_OPTIONS = [
  { value: 'all', label: '停留不限' },
  { value: 'overdue', label: '已超期（停留 > 基准）' },
  { value: 'in_base', label: '基准内（未超期）' },
  { value: 'gte7', label: '≥ 7 天' },
  { value: 'gte14', label: '≥ 14 天' },
  { value: 'gte30', label: '≥ 30 天' },
];

/** 金额区间快捷档（单位：万元）——与金额下拉二选一，选完自动填进上下限输入框 */
const AMOUNT_PRESETS = [
  { value: 'all', label: '金额不限' },
  { value: '0-30', label: '30 万以下' },
  { value: '30-100', label: '30–100 万' },
  { value: '100-300', label: '100–300 万' },
  { value: '300-', label: '300 万以上' },
  { value: 'none', label: '未填金额' },
];

type StayKey = 'all' | 'overdue' | 'in_base' | 'gte7' | 'gte14' | 'gte30';
type RiskKey = 'all' | 'risk' | 'none';
type SortKey =
  | 'default'
  | 'overdue_first'
  | 'stay_desc'
  | 'amount_desc'
  | 'weighted_desc'
  | 'follow_recent';

const SORT_OPTIONS = [
  { value: 'default', label: '默认：停留最久优先（后端）' },
  { value: 'overdue_first', label: '超期优先' },
  { value: 'stay_desc', label: '停留天数 ↓' },
  { value: 'amount_desc', label: '当前金额 ↓' },
  { value: 'weighted_desc', label: '加权预测 ↓' },
  { value: 'follow_recent', label: '最近跟进 ↓' },
];

/** 列表页顶部的大数字指标块（标签在上、值在下，与设计稿一致） */
function BigStat({
  label,
  value,
  unit,
  tone = 'main',
  foot,
  hint,
}: {
  label: string;
  value: string | number;
  unit?: string;
  tone?: 'main' | 'brand' | 'amber' | 'green';
  foot?: React.ReactNode;
  hint: string;
}) {
  const color = {
    main: 'var(--text-main)',
    brand: BRAND,
    amber: WARNING,
    green: SUCCESS,
  }[tone];
  return (
    <Tooltip title={hint}>
      <div
        style={{
          flex: '1 1 0',
          minWidth: 180,
          background: 'var(--bg-card)',
          border: '1px solid var(--border)',
          borderRadius: 8,
          padding: '14px 18px',
          height: '100%',
        }}
      >
        <div style={{ fontSize: 13, color: 'var(--text-sub)', marginBottom: 6 }}>{label}</div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
          <span style={{ fontSize: 28, fontWeight: 600, lineHeight: 1.15, color }}>{value}</span>
          {unit && <span style={{ fontSize: 14, color: 'var(--text-sub)' }}>{unit}</span>}
        </div>
        {foot && <div style={{ marginTop: 4, fontSize: 12, color: 'var(--text-sub)' }}>{foot}</div>}
      </div>
    </Tooltip>
  );
}

/**
 * 2.1 商机列表：关键词走服务端全量筛选；环节 / 停留天数 / 金额区间 / 风险标记 / 负责人
 * 与「超期优先」排序都是**当前页**前端筛选（后端不支持这些参数，界面上已标注）。
 */
export default function Opportunities() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  /**
   * 支持从别处带 `?keyword=` 进来（客户详情的降级提示、商机详情「同客户其他项目」读失败时的兜底入口）。
   * 判据 C2：别处点过来的「去商机列表搜这个客户」必须真的带上搜索词，否则用户还要自己再敲一遍。
   */
  const [keyword, setKeyword] = useState(() => params.get('keyword') ?? '');

  /** 关键词落地到 URL：刷新 / 复制链接后筛选不丢（也便于分享"某个客户的商机"） */
  const applyKeyword = (v: string) => {
    setKeyword(v);
    setPage(1);
    setParams(v ? { keyword: v } : {});
  };
  const [page, setPage] = useState(1);

  /* ---------------- 当前页前端筛选（后端 /opportunities 只支持 keyword + 分页） ---------------- */
  const [stage, setStage] = useState<string | undefined>(undefined);
  const [stay, setStay] = useState<StayKey>('all');
  const [amountPreset, setAmountPreset] = useState<string>('all');
  /** 金额上下限（单位：万元，来自输入框）；提交给筛选时统一换算成整数分比较 */
  const [amountMinWan, setAmountMinWan] = useState<number | null>(null);
  const [amountMaxWan, setAmountMaxWan] = useState<number | null>(null);
  const [risk, setRisk] = useState<RiskKey>('all');
  const [ownerId, setOwnerId] = useState<string | undefined>(undefined);
  const [sortBy, setSortBy] = useState<SortKey>('default');

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['opportunities', { keyword, page, page_size: PAGE_SIZE }],
    queryFn: () => listOpportunities({ keyword: keyword || undefined, page, page_size: PAGE_SIZE }),
  });

  /** 负责人下拉：管理员 / 市场部管理可筛选全团队；专员后端已把数据限制为本人，筛选意义不大但无害 */
  const { data: staff } = useQuery({
    queryKey: ['dashboard-staff-options'],
    queryFn: fetchStaffOptions,
    retry: false,
  });

  const rows = useMemo(() => {
    const minFen = amountMinWan === null ? null : (yuanToFen(amountMinWan * 10000) ?? null);
    const maxFen = amountMaxWan === null ? null : (yuanToFen(amountMaxWan * 10000) ?? null);

    const list = (data?.items ?? []).filter((r) => {
      const stayDays = r.stage_stay_days;
      const amount = r.budget_amount_fen;

      if (stage && (r.current_stage ?? '') !== stage) return false;

      if (stay === 'overdue' && !r.stage_overdue) return false;
      if (stay === 'in_base' && r.stage_overdue) return false;
      if (stay === 'gte7' && (stayDays == null || stayDays < 7)) return false;
      if (stay === 'gte14' && (stayDays == null || stayDays < 14)) return false;
      if (stay === 'gte30' && (stayDays == null || stayDays < 30)) return false;

      if (risk === 'risk' && !(r.risk_types ?? []).length) return false;
      if (risk === 'none' && (r.risk_types ?? []).length) return false;

      if (ownerId && r.owner_id !== ownerId) return false;

      // 金额区间：未填金额的记录只在「金额不限」时保留，避免被当成 0 万混进区间结果
      if (amountPreset === 'none') {
        if (amount !== null && amount !== undefined) return false;
      } else if (minFen !== null || maxFen !== null) {
        if (amount === null || amount === undefined) return false;
        if (minFen !== null && amount < minFen) return false;
        if (maxFen !== null && amount > maxFen) return false;
      }
      return true;
    });

    if (sortBy === 'default') return list;

    const sorted = [...list];
    if (sortBy === 'overdue_first') {
      // 超期优先：超期在前；同组按停留天数降序（口径写在下拉 Title 与页脚）
      sorted.sort((a, b) => {
        const oa = a.stage_overdue ? 1 : 0;
        const ob = b.stage_overdue ? 1 : 0;
        if (oa !== ob) return ob - oa;
        return (b.stage_stay_days ?? 0) - (a.stage_stay_days ?? 0);
      });
    } else if (sortBy === 'stay_desc') {
      sorted.sort((a, b) => (b.stage_stay_days ?? 0) - (a.stage_stay_days ?? 0));
    } else if (sortBy === 'amount_desc') {
      sorted.sort((a, b) => (b.budget_amount_fen ?? -1) - (a.budget_amount_fen ?? -1));
    } else if (sortBy === 'weighted_desc') {
      sorted.sort((a, b) => (b.weighted_amount_fen ?? -1) - (a.weighted_amount_fen ?? -1));
    } else if (sortBy === 'follow_recent') {
      sorted.sort(
        (a, b) => new Date(b.last_follow_at ?? 0).getTime() - new Date(a.last_follow_at ?? 0).getTime(),
      );
    }
    return sorted;
  }, [data?.items, stage, stay, risk, ownerId, amountPreset, amountMinWan, amountMaxWan, sortBy]);

  const columns: ColumnsType<Lead> = [
    {
      title: '客户 / 项目',
      dataIndex: 'name',
      width: 200,
      ellipsis: true,
      render: (v: string, r) => (
        <Space direction="vertical" size={0} style={{ maxWidth: '100%' }}>
          <Space size={6} wrap>
            <a onClick={() => navigate(`/opportunities/${r.id}`)}>{v}</a>
            <LeadStatusTag status={r.status} />
          </Space>
          {/* 项目名（客户-项目两层模型）：一个客户名下多个项目靠它区分 */}
          {r.project_name?.trim() ? (
            <Tooltip title="项目名：同一客户名下的多个项目，各自走一遍 7 环节、各有金额；点名字进详情可切换同客户其他项目">
              <Typography.Text type="secondary" style={{ fontSize: 12 }} ellipsis>
                <ProjectOutlined /> {r.project_name}
              </Typography.Text>
            </Tooltip>
          ) : (
            <Tooltip title="未填项目名：只显示客户名。同一客户有多个项目时建议点「编辑商机」补上项目名，便于区分">
              <Typography.Text type="secondary" style={{ fontSize: 12, cursor: 'help' }}>
                未填项目名
              </Typography.Text>
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: '类型 / 区域',
      key: 'org',
      width: 110,
      render: (_, r) => (
        <Space direction="vertical" size={0}>
          <Typography.Text style={{ fontSize: 13 }}>{r.org_type_label || r.org_type || '未填'}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {[r.city, r.district].filter(Boolean).join(' ') || r.province || '未填'}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '当前环节 / 停留',
      dataIndex: 'current_stage_label',
      width: 178,
      render: (v: string, r) => (
        <Space direction="vertical" size={2}>
          <a onClick={() => navigate(`/opportunities/${r.id}`)}>{v || r.current_stage || '未进入闭环二'}</a>
          {r.stage_overdue ? (
            // 超期直接给「去处理」入口：点进详情就能看到门槛与证据缺口
            <Tooltip title="停留已超过该环节基准天数 → 会被判卡单；点这里进详情补齐证据或回退环节">
              <Tag
                color="red"
                style={{ cursor: 'pointer' }}
                onClick={(e) => {
                  e.stopPropagation();
                  navigate(`/opportunities/${r.id}`);
                }}
              >
                <ExclamationCircleFilled /> 停留 {r.stage_stay_days ?? '待确认'} / 基准 {r.stage_base_days ?? 0} 天 · 去处理
              </Tag>
            </Tooltip>
          ) : (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              停留 {r.stage_stay_days ?? '待确认'} / 基准 {r.stage_base_days ?? 0} 天
            </Typography.Text>
          )}
        </Space>
      ),
    },
    {
      title: '下一节点 / 时间', key: 'rhythm', width: 160,
      render: (_, r) => r.status === 'signed' ? <Tag>销售阶段已结束</Tag> : (
        <Space direction="vertical" size={0}>
          <Typography.Text>{r.next_node || '下一节点待确认'}</Typography.Text>
          <Typography.Text type="secondary">{r.next_node_time || '预计时间待确认'}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '风险标记',
      key: 'risks',
      width: 96,
      render: (_, r) =>
        (r.risk_labels ?? []).length ? (
          <Tooltip title={`已登记：${(r.risk_labels ?? []).join('、')}（点这里进详情看风险明细）`}>
            <Tag
              color="volcano"
              style={{ cursor: 'pointer' }}
              onClick={(e) => {
                e.stopPropagation();
                navigate(`/opportunities/${r.id}`);
              }}
            >
              {(r.risk_labels ?? []).length} 条风险
            </Tag>
          </Tooltip>
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            无
          </Typography.Text>
        ),
    },
    {
      title: '当前金额',
      dataIndex: 'budget_amount_fen',
      width: 116,
      align: 'right',
      render: (v: number | null, r) =>
        v === null || v === undefined ? (
          <Tooltip title="还没填预算金额 → 点这里进详情「金额与变更」修正（必须填变更依据）">
            <a onClick={() => navigate(`/opportunities/${r.id}`)}>未填</a>
          </Tooltip>
        ) : (
          <Tooltip title={`${fenToWan(v)} 万元（= 精确到元的 ¥${formatYuan(v)}）`}>
            <span style={{ fontWeight: 600 }}>¥{fenToWan(v)} 万</span>
          </Tooltip>
        ),
    },
    {
      title: '成交概率',
      dataIndex: 'win_probability',
      width: 92,
      align: 'right',
      render: (v: number | null) => (
        <Tooltip title="成交概率由「当前环节」决定（环节越靠后越高），不是人工估的">
          <span>{fmtPercent(v)}</span>
        </Tooltip>
      ),
    },
    {
      title: '加权预测',
      dataIndex: 'weighted_amount_fen',
      width: 118,
      align: 'right',
      render: (v: number | null) => (
        <Tooltip title="加权预测 = 当前金额 × 该环节成交概率">
          <span style={{ fontWeight: 600, color: BRAND }}>¥{fenToWan(v)} 万</span>
        </Tooltip>
      ),
    },
    {
      title: '预计签约',
      dataIndex: 'expected_sign_date',
      width: 116,
      render: (v: string | null) =>
        v ? (
          <Space size={4}>
            <CalendarOutlined style={{ color: 'var(--text-sub)' }} />
            <span>{fmtDate(v)}</span>
          </Space>
        ) : (
          <Typography.Text type="secondary">未预计</Typography.Text>
        ),
    },
    {
      title: '负责人',
      dataIndex: 'owner_name',
      width: 88,
      render: (v: string | null) => v || '未指派',
    },
    {
      title: '最近跟进',
      dataIndex: 'last_follow_at',
      width: 134,
      render: (v: string | null) =>
        v ? (
          fmtDateTime(v)
        ) : (
          <Tooltip title="还没有跟进记录 → 跟进记录可作为完成标准的证据，点客户名进详情「跟进记录」查看">
            <Typography.Text type="secondary">暂无</Typography.Text>
          </Tooltip>
        ),
    },
  ];

  const overdueOnPage = rows.filter((r) => r.stage_overdue).length;
  const riskOnPage = rows.filter((r) => (r.risk_types ?? []).length).length;
  const weightedOnPage = rows.reduce((sum, r) => sum + (r.weighted_amount_fen ?? 0), 0);
  const rhythmClearOnPage = rows.filter((r) => r.rhythm_clear).length;

  /* 只有「关键词」是服务端全量筛选；其余都是当前页前端筛选，activeClientFilters 用于提示口径 */
  const activeClientFilters = [
    stage ? `环节=${STAGE_OPTIONS.find((s) => s.value === stage)?.label ?? stage}` : '',
    stay !== 'all' ? `停留=${STAY_OPTIONS.find((s) => s.value === stay)?.label ?? stay}` : '',
    amountPreset !== 'all' && amountPreset !== 'none' ? `金额档=${amountPreset} 万` : '',
    amountMinWan !== null ? `金额≥${amountMinWan} 万` : '',
    amountMaxWan !== null ? `金额≤${amountMaxWan} 万` : '',
    risk !== 'all' ? `风险=${risk === 'risk' ? '仅带风险' : '仅无风险'}` : '',
    ownerId ? `负责人=${(staff?.items ?? []).find((s) => s.user_id === ownerId)?.name ?? '已选'}` : '',
    sortBy !== 'default' ? `排序=${SORT_OPTIONS.find((s) => s.value === sortBy)?.label ?? sortBy}` : '',
  ].filter(Boolean);

  const filtered = activeClientFilters.length > 0 || !!keyword;

  const resetFilters = () => {
    setStage(undefined);
    setStay('all');
    setAmountPreset('all');
    setAmountMinWan(null);
    setAmountMaxWan(null);
    setRisk('all');
    setOwnerId(undefined);
    setSortBy('default');
    setKeyword('');
    setPage(1);
    setParams({});
  };

  /** 金额快捷档 → 自动填上下限（选「金额不限」则清空） */
  const applyAmountPreset = (v: string) => {
    setAmountPreset(v);
    if (v === 'all' || v === 'none') {
      setAmountMinWan(null);
      setAmountMaxWan(null);
      return;
    }
    const [min, max] = v.split('-');
    setAmountMinWan(min ? Number(min) : null);
    setAmountMaxWan(max ? Number(max) : null);
  };

  if (isError) {
    return (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <PageHeader
          title="商机列表"
          demo
          subtitle="先处理超期与卡单——默认按「当前环节停留最久优先」，点一行进详情补齐门槛与证据。"
        />
        <Card>
          <Alert
            type="error"
            showIcon
            message="商机列表没读出来（接口 /opportunities 返回失败）"
            description={
              <Space direction="vertical" size={4}>
                <span>
                  为什么：请求没成功，可能是登录已过期、网络中断或后端服务暂时不可用。
                  {error instanceof Error ? `（${error.message}）` : ''}
                </span>
                <span>怎么办：先点「重试」；仍失败请刷新页面重新登录；持续失败请把这条提示发给系统管理员。</span>
              </Space>
            }
            action={
              <Space>
                <Button size="small" type="primary" loading={isFetching} onClick={() => refetch()}>
                  重试
                </Button>
                <Button size="small" onClick={() => navigate('/workbench')}>
                  回工作台
                </Button>
              </Space>
            }
          />
        </Card>
      </Space>
    );
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {/*  标题与筛选同一行（设计稿 02 的骨架）：服务端关键词放进 PageHeader 的 actions，天然同排 */}
      <PageHeader
        title="商机列表"
        demo
        subtitle="先处理超期与卡单——默认按「当前环节停留最久优先」，点一行进详情补齐门槛与证据。"
        actions={
          <Space wrap>
            <Tooltip title="关键词是服务端全量筛选（后端 /opportunities 支持 keyword），会在所有页里查">
              <Input.Search
                allowClear
                placeholder="搜索客户名称（全量）"
                style={{ width: 220 }}
                onSearch={(v) => applyKeyword(v.trim())}
              />
            </Tooltip>
            <Button
              icon={<ReloadOutlined />}
              loading={isFetching}
              onClick={() => refetch()}
            >
              刷新
            </Button>
          </Space>
        }
      />

      {/* ── 当前页筛选条：spec 2.1 要求「环节 / 停留天数 / 金额区间 / 风险标记 / 负责人」+「超期优先」 ── */}
      <Card size="small" styles={{ body: { paddingTop: 12, paddingBottom: 12 } }}>
        <Space wrap size={10} align="center">
          <Space size={6}>
            <FilterOutlined style={{ color: BRAND }} />
            <Typography.Text strong style={{ fontSize: 13 }}>
              当前页筛选
            </Typography.Text>
          </Space>

          <Tooltip title="环节：只看某一环节的商机（仅对当前页筛选）">
            <Select
              allowClear
              style={{ width: 150 }}
              placeholder="全部环节"
              value={stage}
              onChange={(v) => setStage(v ?? undefined)}
              options={STAGE_OPTIONS}
            />
          </Tooltip>

          <Tooltip title="停留天数：按当前环节的停留自然日筛选；「已超期」= 停留 > 该环节基准（会被判卡单）">
            <Select
              style={{ width: 176 }}
              value={stay}
              onChange={(v) => setStay(v as StayKey)}
              options={STAY_OPTIONS}
            />
          </Tooltip>

          <Tooltip title="金额区间：按当前金额（预算金额）筛选；未填金额的记录只在选「未填金额」时出现">
            <Select
              style={{ width: 140 }}
              value={amountPreset}
              onChange={(v) => applyAmountPreset(v)}
              options={AMOUNT_PRESETS}
            />
          </Tooltip>
          <Space size={4} align="center">
            <InputNumber
              style={{ width: 100 }}
              min={0}
              placeholder="最低"
              addonAfter="万"
              value={amountMinWan}
              onChange={(v) => {
                setAmountMinWan(v);
                setAmountPreset('all');
              }}
            />
            <Typography.Text type="secondary">~</Typography.Text>
            <InputNumber
              style={{ width: 100 }}
              min={0}
              placeholder="最高"
              addonAfter="万"
              value={amountMaxWan}
              onChange={(v) => {
                setAmountMaxWan(v);
                setAmountPreset('all');
              }}
            />
          </Space>

          <Tooltip title="风险标记：已登记 R1–R5 风险的商机；明细在详情页「风险」Tab">
            <Segmented
              value={risk}
              options={[
                { value: 'all', label: '风险不限' },
                { value: 'risk', label: `仅带风险${riskOnPage ? ` ${riskOnPage}` : ''}` },
                { value: 'none', label: '仅无风险' },
              ]}
              onChange={(v) => setRisk(v as RiskKey)}
            />
          </Tooltip>

          <Tooltip title="负责人：专员只能看到本人名下商机（后端已限制数据范围）；管理人员可在这里筛团队里某个人">
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              style={{ width: 140 }}
              placeholder="全部负责人"
              value={ownerId}
              onChange={(v) => setOwnerId(v ?? undefined)}
              options={(staff?.items ?? []).map((s) => ({ value: s.user_id, label: s.name }))}
            />
          </Tooltip>

          <Tooltip title="排序只作用于当前页：后端默认「停留最久优先」；「超期优先」= 超期在前、同组按停留天数降序">
            <Select
              style={{ width: 210 }}
              value={sortBy}
              onChange={(v) => setSortBy(v as SortKey)}
              options={SORT_OPTIONS}
            />
          </Tooltip>

          <Button size="small" onClick={resetFilters} disabled={!filtered}>
            清掉筛选
          </Button>
        </Space>

        {/* 口径必须写清：关键词是全量，其它只作用于当前页 */}
        <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text-sub)', lineHeight: 1.7 }}>
          <PanelFlag>仅对当前页</PanelFlag>{' '}
          环节 / 停留天数 / 金额区间 / 风险标记 / 负责人 / 排序 都是<b>当前页 {data?.items?.length ?? 0} 条</b>的前端筛选与排序
          （后端 <code>GET /opportunities</code> 目前只支持关键词与分页，未支持这些参数）。要全量筛选请先用「搜索客户名称（全量）」缩小范围，或翻页后继续筛。
          {activeClientFilters.length > 0 && (
            <>
              <br />
              当前生效：{activeClientFilters.join(' ｜ ')}；筛后剩 <b>{rows.length}</b> 条。
            </>
          )}
        </div>
      </Card>

      {/* 一行大数字 KPI（标签在上、值在下）*/}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'stretch' }}>
        <BigStat
          label="商机总数"
          value={data?.total ?? 0}
          unit="条"
          tone="brand"
          hint="进入闭环二（已判定为有效商机）的商机总数；来自后端分页 total，含全部环节，不受当前页筛选影响"
          foot={
            <span>
              <ProjectOutlined /> 后端分页 total
            </span>
          }
        />
        <BigStat
          label="本页超期"
          value={overdueOnPage}
          unit={`/ ${rows.length} 条`}
          tone={overdueOnPage > 0 ? 'amber' : 'main'}
          hint="仅统计当前页中「环节停留超过基准天数」的商机；全量要看请用上方「已超期（停留 > 基准）」逐页筛"
          foot={
            <span>
              <AlertOutlined /> 停留 &gt; 该环节基准天数
            </span>
          }
        />
        <BigStat
          label="本页带风险"
          value={riskOnPage}
          unit={`/ ${rows.length} 条`}
          tone={riskOnPage > 0 ? 'amber' : 'main'}
          hint="仅统计当前页中已登记 R1–R5 风险的商机数；风险明细在详情页「风险」Tab"
          foot={
            <span>
              <WarningOutlined /> 已登记 R1–R5 风险
            </span>
          }
        />
        <BigStat
          label="本页加权预测"
          value={`¥ ${Number(fenToWan(weightedOnPage))}`}
          unit="万"
          hint="仅统计当前页：加权预测 = 当前金额 × 该环节成交概率，逐条相加"
          foot={
            <span>
              <FundOutlined /> 金额 × 环节成交概率
            </span>
          }
        />
        <BigStat
          label="本页节奏清晰"
          value={rhythmClearOnPage}
          unit={`/ ${rows.length} 条`}
          tone={rhythmClearOnPage < rows.length ? 'amber' : 'green'}
          hint="本页已明确下一关键节点及预计时间且未到期待确认的商机数；已签约不再判断销售节奏"
          foot={
            <span>
              <ClockCircleOutlined /> 两项时间有其一即算清晰
            </span>
          }
        />
      </div>

      <Card>
        {filtered && rows.length !== (data?.items?.length ?? 0) && (
          <div style={{ marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <PanelFlag>筛选中</PanelFlag>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              本页原 {data?.items?.length ?? 0} 条 → 筛后 {rows.length} 条（前端筛选，只作用于当前页）
            </Typography.Text>
            <Button size="small" onClick={resetFilters}>
              清掉筛选
            </Button>
          </div>
        )}
        <Table<Lead>
          rowKey="id"
          size="small"
          loading={isLoading}
          dataSource={rows}
          columns={columns}
          scroll={{ x: 1250 }}
          locale={{
            emptyText: (
              <NextStepGuide
                title={activeClientFilters.length ? '当前页没有符合筛选条件的商机' : '暂无商机'}
                description={
                  activeClientFilters.length
                    ? `筛选项只作用于当前页的 ${data?.items?.length ?? 0} 条，可能符合条件的商机在别的页。可以：① 清掉筛选；② 翻到下一页继续筛；③ 用右上角「搜索客户名称（全量）」按客户名做服务端全量查询。`
                    : '只有进入闭环二（判定为有效商机）的客户会出现在这里。先去线索池把线索验证成有效商机。'
                }
                actions={
                  <Space wrap>
                    <Button onClick={resetFilters} disabled={!filtered}>
                      清掉筛选
                    </Button>
                    <Button type="primary" onClick={() => navigate('/leads?view=all')}>
                      去线索池
                    </Button>
                  </Space>
                }
              />
            ),
          }}
          onRow={(r) => ({
            onClick: () => navigate(`/opportunities/${r.id}`),
            style: { cursor: 'pointer' },
          })}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total: data?.total ?? 0,
            onChange: setPage,
            showTotal: (t) => `共 ${t} 条`,
          }}
        />
      </Card>

      <Footnote>
        筛选口径（判据 C3）：右上角「搜索客户名称（全量）」走后端 <code>keyword</code>，是<b>全量</b>查询；
        环节 / 停留天数 / 金额区间 / 风险标记 / 负责人 / 排序都是<b>仅对当前页</b>的前端筛选与排序
        （后端 <code>GET /opportunities</code> 只支持 keyword 与分页）。
        默认按「当前环节停留最久优先」（= 后端按进入环节时间升序）；「超期优先」= 超期在前、同组按停留天数降序。
        环节停留按自然日计时，基准天数来自该环节的标准卡配置；加权预测 = 当前金额 × 该环节成交概率。
        「节奏」列统一查看下一关键节点和预计时间；缺项或节点到期待确认时提醒R5，
        通过项目跟进确认下一节点及预计时间，更新档案后复核风险。
      </Footnote>
    </Space>
  );
}
