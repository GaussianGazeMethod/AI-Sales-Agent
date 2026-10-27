import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Input,
  Radio,
  Row,
  Select,
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
  AuditOutlined,
  CheckCircleOutlined,
  FormOutlined,
  InfoCircleOutlined,
  PaperClipOutlined,
  ReloadOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import dayjs, { type Dayjs } from 'dayjs';
import { fetchStaffOptions } from '@/api/dashboard';
import { getLeadDetail } from '@/api/leads';
import { createReview, fetchPendingReviews, listReviews } from '@/api/reports';
import { useDicts } from '@/hooks/useDicts';
import KpiCard from '@/components/KpiCard';
import PageHeader from '@/components/PageHeader';
import { Footnote, NextStepGuide, NotForAssessment, PanelFlag } from '@/components/Guide';
import { VERDICT_COLOR, REVIEW_RESULT_COLOR, fmtDate, fmtDateTime } from '@/utils/format';
import type {
  PendingReview,
  ProjectReview,
  ReviewCreateBody,
  ReviewItem,
  ReviewItemDef,
  ReviewResult,
  ReviewVerdict,
} from '@/types';

const PAGE_SIZE = 10;
/** 结论最少字数：太短的结论无法复盘，也说不清辅导动作 */
const CONCLUSION_MIN = 10;
const CONCLUSION_MAX = 500;

/** 5 看单项草稿：结论 + 证据/说明（证据来自商机附件或跟记录，按名称登记） */
type DraftItems = Record<string, { result?: ReviewResult; note: string }>;

/** 软底色 Tag 的取色：不足/有风险=橙、到位/通过=绿、待核实=橙、不通过=红（与设计稿 06 一致） */
function softTagStyle(colorKey: string | undefined): React.CSSProperties {
  const map: Record<string, { bg: string; fg: string; bd: string }> = {
    green: { bg: 'var(--success-soft)', fg: 'var(--success-strong)', bd: 'var(--success-line)' },
    orange: { bg: 'var(--warning-fill)', fg: 'var(--warning-strong)', bd: 'var(--warning-line)' },
    red: { bg: 'var(--danger-soft)', fg: 'var(--danger-strong)', bd: 'var(--danger-line)' },
  };
  const c = map[colorKey ?? ''] ?? { bg: 'var(--fill-soft)', fg: 'var(--text-sub)', bd: 'var(--fill-line)' };
  return {
    background: c.bg,
    color: c.fg,
    border: `1px solid ${c.bd}`,
    borderRadius: 6,
    margin: 0,
    fontWeight: 500,
  };
}

/**
 * 2.7 项目评审（评审卡）—— 对齐设计稿 06 右栏「项目评审」面板：
 * 面板标题 + 演示数据 · 客户名（左蓝竖条）·「五看评估」表（评估维度 / 当前判断软底色 Tag / 证据  蓝色链接）
 * ·「人工决策（必填）」蓝色信息条 · 执行判定三选一 · 评审结论与辅导动作（0/500）· 下次评审 + 保存评审。
 *
 * 判据：C2（按钮置灰必给原因）、C3（口径写在面板与页脚）、C4（AI 不作为考核依据，管理者判定）。
 * 仅 admin / market_manager 可进（路由层拦截），专员直接访问走 403。
 */
export default function Reviews() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { dicts } = useDicts();
  const [searchParams, setSearchParams] = useSearchParams();

  const reviewItems: ReviewItemDef[] = dicts.review_items ?? [];
  const resultOptions = dicts.review_results ?? [];
  const verdictOptions = dicts.review_verdicts ?? [];

  /** 页签也可由 URL 指定（?tab=history），便于分享与截图核对 */
  const [tab, setTab] = useState<string>(searchParams.get('tab') === 'history' ? 'history' : 'pending');

  /* ---------------- 待评审清单 ---------------- */
  const pendingQuery = useQuery({
    queryKey: ['reviews-pending'],
    queryFn: fetchPendingReviews,
  });
  const pending = useMemo(() => pendingQuery.data?.items ?? [], [pendingQuery.data]);

  /**
   * 待评审清单的**当前清单前端筛选**（spec 2.7 的「待评审清单」要按人/环节找得到）。
   * 后端 `GET /reviews/pending` 不支持任何查询参数（见 backend/app/api/reports.py），
   * 因此这里是纯前端筛选，界面上标注"仅对当前清单"。
   */
  const [pendingOwner, setPendingOwner] = useState<string | undefined>(undefined);
  const [pendingStage, setPendingStage] = useState<string | undefined>(undefined);

  const pendingStageOptions = useMemo(() => {
    const m = new Map<string, string>();
    pending.forEach((p) => {
      if (p.stage) m.set(p.stage, p.stage_label || p.stage);
    });
    return [...m.entries()].map(([value, label]) => ({ value, label }));
  }, [pending]);

  const pendingOwnerOptions = useMemo(() => {
    const m = new Map<string, string>();
    pending.forEach((p) => {
      if (p.owner_name) m.set(p.owner_name, p.owner_name);
    });
    return [...m.keys()].map((name) => ({ value: name, label: name }));
  }, [pending]);

  const filteredPending = useMemo(
    () =>
      pending.filter((p) => {
        if (pendingOwner && p.owner_name !== pendingOwner) return false;
        if (pendingStage && p.stage !== pendingStage) return false;
        return true;
      }),
    [pending, pendingOwner, pendingStage],
  );

  const pendingFiltered = !!pendingOwner || !!pendingStage;

  /* ---------------- 评审历史 ---------------- */
  const [verdictFilter, setVerdictFilter] = useState<string | undefined>(undefined);
  const [reviewerId, setReviewerId] = useState<string | undefined>(undefined);
  const [page, setPage] = useState(1);

  const historyQuery = useQuery({
    queryKey: ['reviews', { verdict: verdictFilter, reviewer_id: reviewerId, page }],
    queryFn: () =>
      listReviews({
        verdict: verdictFilter,
        reviewer_id: reviewerId,
        page,
        page_size: PAGE_SIZE,
      }),
  });

  const { data: staff } = useQuery({
    queryKey: ['dashboard-staff-options'],
    queryFn: fetchStaffOptions,
    retry: false,
  });

  /* ---------------- 评审卡（右栏面板） ---------------- */
  const [target, setTarget] = useState<{ leadId: string; leadName: string; stageLabel: string } | null>(
    null,
  );
  const [draft, setDraft] = useState<DraftItems>({});
  const [verdict, setVerdict] = useState<ReviewVerdict | undefined>(undefined);
  const [conclusion, setConclusion] = useState('');
  const [nextReviewAt, setNextReviewAt] = useState<Dayjs>(dayjs().add(7, 'day'));
  const [formError, setFormError] = useState('');
  /** 点「证据」链接时把对应项的证据输入框滚进视野 */
  const evidenceRefs = useRef<Record<string, HTMLTextAreaElement | null>>({});

  /** 从 query 带过来的 lead_id（商机详情页「填写评审卡」入口） */
  const queryLeadId = searchParams.get('lead_id') ?? undefined;
  const { data: queryLead } = useQuery({
    queryKey: ['lead', queryLeadId],
    queryFn: () => getLeadDetail(queryLeadId as string),
    select: (data) => data.lead,
    enabled: !!queryLeadId,
  });

  const openCard = (leadId: string, leadName: string, stageLabel: string) => {
    const init: DraftItems = {};
    reviewItems.forEach((it) => {
      init[it.key] = { note: '' };
    });
    setDraft(init);
    setVerdict(undefined);
    setConclusion('');
    setNextReviewAt(dayjs().add(7, 'day'));
    setFormError('');
    setTarget({ leadId, leadName, stageLabel });
  };

  const openCardForRow = (r: PendingReview) => openCard(r.lead_id, r.lead_name, r.stage_label);
  /** 这条商机是否也在左侧待评审清单里（历史补评时用来解释"为什么左边没高亮"） */
  const inPendingList = (leadId?: string) => !!leadId && pending.some((p) => p.lead_id === leadId);

  // 商机详情页跳转 ?lead_id=xxx 时自动选中该商机的评审卡
  useEffect(() => {
    if (!queryLeadId) return;
    if (!reviewItems.length) return;
    if (target?.leadId === queryLeadId) return;
    openCard(
      queryLeadId,
      queryLead?.name ?? '商机',
      queryLead?.current_stage_label ?? queryLead?.current_stage ?? '—',
    );
    // 选中后清掉 query，避免刷新又弹一次
    const next = new URLSearchParams(searchParams);
    next.delete('lead_id');
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryLeadId, reviewItems.length, queryLead]);

  const doSubmit = useMutation({
    mutationFn: () => {
      const body: ReviewCreateBody = {
        lead_id: target?.leadId as string,
        verdict: verdict as ReviewVerdict,
        conclusion: conclusion.trim(),
        next_review_at: nextReviewAt.format('YYYY-MM-DD'),
      };
      // 5 看的 key 由后端字典下发（review_items），因此动态挂载，不写死 m1..m5
      const dynamic = body as unknown as Record<string, unknown>;
      reviewItems.forEach((it) => {
        const item = draft[it.key];
        dynamic[it.key] = {
          result: item?.result,
          note: item?.note?.trim() || undefined,
        };
      });
      return createReview(body);
    },
    onSuccess: () => {
      message.success('评审卡已提交，已记入评审历史');
      setTarget(null);
      qc.invalidateQueries({ queryKey: ['reviews-pending'] });
      qc.invalidateQueries({ queryKey: ['reviews'] });
      qc.invalidateQueries({ queryKey: ['opportunity-detail'] });
      qc.invalidateQueries({ queryKey: ['dashboard-risk-center'] });
    },
    onError: (err) => {
      // 后端 400/403 的 detail 已由拦截器提示，这里保留面板让用户修改
      const e = err as { response?: { data?: { detail?: string } } };
      setFormError(e?.response?.data?.detail ?? '提交失败，请检查后重试');
    },
  });

  /* ---------------- 保存前置校验（判据 C2：置灰必须说清"差什么、去哪补"） ---------------- */
  const missingItems = reviewItems.filter((it) => !draft[it.key]?.result);
  const trimmedConclusion = conclusion.trim();
  const conclusionTooShort = trimmedConclusion.length > 0 && trimmedConclusion.length < CONCLUSION_MIN;

  const blockReasons = useMemo(() => {
    const list: string[] = [];
    if (missingItems.length) {
      list.push(`还有 ${missingItems.length} 项「五看评估」未判定：${missingItems.map((m) => m.code).join('、')}`);
    }
    if (!verdict) list.push('「执行判定」未选（正常 / 能力问题 / 态度问题 三选一）');
    if (!trimmedConclusion) list.push('「评审结论与辅导动作」为空');
    else if (trimmedConclusion.length < CONCLUSION_MIN) {
      list.push(`「评审结论与辅导动作」至少 ${CONCLUSION_MIN} 字（当前 ${trimmedConclusion.length} 字）`);
    }
    if (!nextReviewAt) list.push('「下次评审」日期未填');
    return list;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [missingItems.length, verdict, trimmedConclusion, nextReviewAt]);

  const canSave = blockReasons.length === 0;

  const submit = () => {
    if (!canSave) {
      setFormError(`暂时不能保存，还差：${blockReasons.join('；')}`);
      return;
    }
    setFormError('');
    doSubmit.mutate();
  };

  const focusEvidence = (key: string) => {
    const el = evidenceRefs.current[key];
    el?.focus();
    el?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  };

  /* ---------------- 表格列 ---------------- */
  const pendingColumns: ColumnsType<PendingReview> = [
    {
      title: '客户名称',
      dataIndex: 'lead_name',
      width: 200,
      render: (v: string, r) => (
        <a
          onClick={() => openCardForRow(r)}
          style={{
            borderLeft: '3px solid var(--brand)',
            paddingLeft: 8,
            fontWeight: 600,
            lineHeight: '18px',
            display: 'inline-block',
          }}
        >
          {v}
        </a>
      ),
    },
    { title: '负责人', dataIndex: 'owner_name', width: 90, render: (v: string | null) => v || '-' },
    { title: '当前环节', dataIndex: 'stage_label', width: 130 },
    {
      title: '停留天数',
      dataIndex: 'stay_days',
      width: 100,
      align: 'right',
      render: (v: number) => (
        <Typography.Text type={v > 14 ? 'danger' : undefined}>{v} 天</Typography.Text>
      ),
    },
    { title: '待评审原因', dataIndex: 'reason', ellipsis: true },
    {
      title: '上次评审',
      width: 190,
      render: (_, r) =>
        r.last_review_at ? (
          <Space size={6} wrap>
            <span>{fmtDate(r.last_review_at)}</span>
            {r.last_verdict && (
              <Tag color={VERDICT_COLOR[r.last_verdict] ?? 'default'}>
                {r.last_verdict_label ?? r.last_verdict}
              </Tag>
            )}
          </Space>
        ) : (
          <Tag>未评审</Tag>
        ),
    },
    {
      title: '操作',
      width: 96,
      fixed: 'right',
      render: (_, r) => {
        const active = target?.leadId === r.lead_id;
        return (
          <Button
            type={active ? 'default' : 'primary'}
            size="small"
            icon={<FormOutlined />}
            onClick={() => openCardForRow(r)}
          >
            {active ? '评审中' : '评审'}
          </Button>
        );
      },
    },
  ];

  const historyColumns: ColumnsType<ProjectReview> = [
    {
      title: '评审时间',
      dataIndex: 'review_at',
      width: 132,
      render: (v: string) => fmtDateTime(v),
    },
    {
      title: '客户',
      dataIndex: 'lead_name',
      width: 156,
      render: (v: string, r) => <a onClick={() => navigate(`/opportunities/${r.lead_id}`)}>{v}</a>,
    },
    { title: '环节', dataIndex: 'stage_label', width: 88, render: (v: string) => v || '-' },
    { title: '评审人', dataIndex: 'reviewer_name', width: 70 },
    {
      title: '执行判定',
      dataIndex: 'verdict',
      width: 92,
      render: (v: string, r) => (
        <Tag color={VERDICT_COLOR[v] ?? 'default'}>{r.verdict_label || v}</Tag>
      ),
    },
    {
      title: '评审结论',
      dataIndex: 'conclusion',
      width: 150,
      ellipsis: true,
      render: (v: string | null) => (
        <Typography.Text ellipsis style={{ fontSize: 13 }} title={v || ''}>
          {v || '-'}
        </Typography.Text>
      ),
    },
    {
      title: '下次评审',
      dataIndex: 'next_review_at',
      width: 100,
      render: (v: string | null) => fmtDate(v),
    },
    {
      title: '操作',
      width: 104,
      render: (_, r) => (
        <Tooltip title="按这条记录的项目再开一张新评审卡（用于补充评审，不改动历史记录）">
          <Button size="small" icon={<FormOutlined />} onClick={() => openCard(r.lead_id, r.lead_name, r.stage_label)}>
            再评一次
          </Button>
        </Tooltip>
      ),
    },
  ];

  const expandedRow = (r: ProjectReview) => (
    <Table<ReviewItem>
      rowKey="key"
      size="small"
      pagination={false}
      dataSource={r.items}
      columns={[
        {
          title: '五看评估',
          width: 240,
          render: (_, it) => (
            <Space direction="vertical" size={0}>
              <Typography.Text strong>
                {it.code} {it.label}
              </Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {it.hint}
              </Typography.Text>
            </Space>
          ),
        },
        {
          title: '当前判断',
          dataIndex: 'result',
          width: 110,
          render: (v: string, it) => (
            <Tag style={softTagStyle(REVIEW_RESULT_COLOR[v])}>{it.result_label || v || '待核实'}</Tag>
          ),
        },
        {
          title: '证据',
          dataIndex: 'note',
          render: (v: string) =>
            v ? (
              <Space size={6}>
                <PaperClipOutlined style={{ color: 'var(--brand)' }} />
                <Typography.Text style={{ color: 'var(--brand)' }}>{v}</Typography.Text>
              </Space>
            ) : (
              <Typography.Text type="secondary">（未登记证据）</Typography.Text>
            ),
        },
      ]}
    />
  );

  const pendingCard = (
    <Card
      size="small"
      styles={{ body: { padding: 0 } }}
      title={
        <Space size={8}>
          <span>待评审项目</span>
          <Tag color="orange">{pendingQuery.data?.total ?? 0}</Tag>
        </Space>
      }
      extra={
        <Space size={8} wrap>
          <Tooltip title="负责人：只筛当前清单里的项目（后端 /reviews/pending 不支持查询参数，这里是清单前端筛选）">
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              size="small"
              style={{ width: 120 }}
              placeholder="全部负责人"
              value={pendingOwner}
              onChange={(v) => setPendingOwner(v ?? undefined)}
              options={pendingOwnerOptions}
            />
          </Tooltip>
          <Tooltip title="环节：只筛当前清单里处于某个环节的项目">
            <Select
              allowClear
              size="small"
              style={{ width: 130 }}
              placeholder="全部环节"
              value={pendingStage}
              onChange={(v) => setPendingStage(v ?? undefined)}
              options={pendingStageOptions}
            />
          </Tooltip>
          {pendingFiltered && (
            <Button
              size="small"
              onClick={() => {
                setPendingOwner(undefined);
                setPendingStage(undefined);
              }}
            >
              清掉清单筛选
            </Button>
          )}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            点客户名打开评审卡
          </Typography.Text>
        </Space>
      }
    >
      {pendingFiltered && (
        <div style={{ padding: '8px 12px 0', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <PanelFlag>仅对当前清单</PanelFlag>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            清单共 {pending.length} 条 → 筛后 {filteredPending.length} 条（前端筛选，非全量查询）
          </Typography.Text>
        </div>
      )}
      <Table<PendingReview>
        rowKey="lead_id"
        size="small"
        loading={pendingQuery.isLoading}
        dataSource={filteredPending}
        columns={pendingColumns}
        scroll={{ x: 1100 }}
        rowClassName={(r) => (target?.leadId === r.lead_id ? 'review-row-active' : '')}
        locale={{
          emptyText: pendingQuery.isError ? (
            <NextStepGuide
              title="待评审清单没读出来（接口 /reviews/pending 返回失败）"
              description="为什么：请求没成功，可能是登录过期（本页仅 market_manager / admin 可看）、网络中断或后端暂时不可用。怎么办：点下面「重试」；仍失败请重新登录，并把这条提示发给系统管理员。"
              actions={
                <Space wrap>
                  <Button type="primary" onClick={() => pendingQuery.refetch()}>
                    重试
                  </Button>
                  <Button onClick={() => navigate('/opportunities')}>去 2.1 商机列表</Button>
                </Space>
              }
            />
          ) : pendingFiltered ? (
            <NextStepGuide
              title="当前清单里没有符合筛选的项目"
              description="负责人 / 环节只作用于当前待评审清单，符合条件的项目可能不在清单里（清单只收「进入闭环二后超期未评审」或「已到下次评审时间」的项目）。可以清掉清单筛选，或去商机列表直接开评审卡。"
              actions={
                <Space wrap>
                  <Button
                    onClick={() => {
                      setPendingOwner(undefined);
                      setPendingStage(undefined);
                    }}
                  >
                    清掉清单筛选
                  </Button>
                  <Button onClick={() => navigate('/opportunities')}>去 2.1 商机列表</Button>
                </Space>
              }
            />
          ) : (
            <NextStepGuide
              title="当前没有待评审项目"
              description="评审卡只能针对已进入闭环二的商机开：请从「2.1 商机列表」进入项目详情，点顶部「填写评审卡」；或等系统按「进入闭环二后超过阈值天数未评审 / 已到下次评审时间」自动把项目排进这里。"
              actions={
                <Space wrap>
                  <Button type="primary" onClick={() => navigate('/opportunities')}>
                    去 2.1 商机列表
                  </Button>
                  <Button onClick={() => navigate('/reports/weekly-meeting')}>去周会视图</Button>
                </Space>
              }
            />
          ),
        }}
        pagination={{ pageSize: PAGE_SIZE, showSizeChanger: false, showTotal: (t) => `共 ${t} 条` }}
        onRow={() => ({ style: { cursor: 'pointer' } })}
      />
      <style>{`.review-row-active > td { background: var(--brand-soft) !important; }`}</style>
    </Card>
  );

  const historyCard = (
    <Card size="small" styles={{ body: { padding: 0 } }}>
      <Table<ProjectReview>
        rowKey="id"
        size="small"
        loading={historyQuery.isLoading}
        dataSource={historyQuery.data?.items ?? []}
        columns={historyColumns}
        expandable={{ expandedRowRender: expandedRow }}
        locale={{
          emptyText: historyQuery.isError ? (
            <NextStepGuide
              compact
              title="评审历史没读出来（接口 /reviews 返回失败）"
              description="为什么：请求没成功，可能是登录过期、网络中断或后端暂时不可用。怎么办：点右上「刷新」重试；仍失败请重新登录，并把这条提示发给系统管理员。"
              actions={
                <Button size="small" type="primary" onClick={() => historyQuery.refetch()}>
                  重试
                </Button>
              }
            />
          ) : (
            <NextStepGuide
              compact
              title="暂无评审记录"
              description="填过的评审卡会留在历史里，可按「执行判定」与「评审人」筛选回看；点行首箭头可展开看五项判定与证据。"
            />
          ),
        }}
        title={() => (
          <Space wrap size={8}>
            <Typography.Text type="secondary">执行判定</Typography.Text>
            <Select
              allowClear
              placeholder="全部"
              style={{ width: 140 }}
              value={verdictFilter}
              onChange={(v) => {
                setPage(1);
                setVerdictFilter(v ?? undefined);
              }}
              options={verdictOptions.map((o) => ({ value: o.value, label: o.label }))}
            />
            <Typography.Text type="secondary">评审人</Typography.Text>
            <Select
              allowClear
              placeholder="全部"
              style={{ width: 160 }}
              value={reviewerId}
              onChange={(v) => {
                setPage(1);
                setReviewerId(v ?? undefined);
              }}
              options={(staff?.items ?? []).map((s) => ({ value: s.user_id, label: s.name }))}
            />
            <Button
              icon={<ReloadOutlined />}
              loading={historyQuery.isFetching}
              onClick={() => historyQuery.refetch()}
            >
              刷新
            </Button>
          </Space>
        )}
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total: historyQuery.data?.total ?? 0,
          showSizeChanger: false,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p) => setPage(p),
        }}
      />
    </Card>
  );

  /* ---------------- 评审卡面板（对齐设计稿 06 右栏） ---------------- */
  const cardBody = !target ? (
    <NextStepGuide
      title="左侧选一个项目，这里出评审卡"
      description="评审卡由管理者填写：先逐项做「五看评估」（每项必须给结论），再由人工做「执行判定」，最后写下评审结论与下次评审时间。评审卡也可以从「2.1 商机列表 → 项目详情 → 填写评审卡」进入。"
      icon={<AuditOutlined style={{ fontSize: 40, color: 'var(--text-muted)' }} />}
      actions={
        <Space wrap>
          <Button type="primary" onClick={() => navigate('/opportunities')}>
            去 2.1 商机列表
          </Button>
          <Button onClick={() => navigate('/reports/weekly-meeting')}>去周会视图</Button>
        </Space>
      }
    />
  ) : (
    <Space direction="vertical" size={14} style={{ width: '100%' }}>
      {/* 客户名：左蓝竖条强调 */}
      <div
        style={{
          borderLeft: '3px solid var(--brand)',
          paddingLeft: 10,
          fontSize: 18,
          fontWeight: 600,
          color: 'var(--text-main)',
          lineHeight: '24px',
        }}
      >
        {target.leadName}
      </div>
      <Typography.Text type="secondary" style={{ fontSize: 12, marginTop: -8 }}>
        当前环节：{target.stageLabel || '—'} · 点客户名可进{' '}
        <a onClick={() => navigate(`/opportunities/${target.leadId}`)}>商机详情</a> 核对证据原件
        {inPendingList(target.leadId) ? '' : '（该项目当前不在左侧「待评审清单」里——你是在「评审历史」里补开一张新卡）'}
      </Typography.Text>

      {/* ── 五看评估 ── */}
      <div>
        <Typography.Text strong style={{ fontSize: 14 }}>
          五看评估
        </Typography.Text>
        <Table<ReviewItemDef>
          rowKey="key"
          size="small"
          pagination={false}
          style={{ marginTop: 8 }}
          dataSource={reviewItems}
          locale={{
            emptyText: (
              <NextStepGuide
                compact
                title="五看维度字典未下发"
                description="五看维度由后端字典 review_items 下发；字典为空时无法评审。请刷新页面，或联系管理员检查字典配置。"
              />
            ),
          }}
          columns={[
            {
              title: '评估维度',
              width: 112,
              render: (_, it) => (
                <Tooltip title={it.hint}>
                  <span style={{ fontSize: 13 }}>
                    {it.code} {it.label}
                  </span>
                </Tooltip>
              ),
            },
            {
              title: '当前判断',
              width: 88,
              render: (_, it) => {
                const cur = draft[it.key]?.result;
                if (!cur) {
                  return (
                    <Tag style={softTagStyle('orange')} onClick={() => focusEvidence(it.key)}>
                      待核实
                    </Tag>
                  );
                }
                const label = resultOptions.find((o) => o.value === cur)?.label ?? cur;
                return (
                  <Tag style={softTagStyle(REVIEW_RESULT_COLOR[cur])} onClick={() => focusEvidence(it.key)}>
                    {label}
                  </Tag>
                );
              },
            },
            {
              title: '证据',
              render: (_, it) => {
                const note = draft[it.key]?.note?.trim();
                return (
                  <Tooltip title={note || '点这里到下面登记证据（文件名 / 来源）'}>
                    <a onClick={() => focusEvidence(it.key)} style={{ fontSize: 13 }}>
                      <PaperClipOutlined style={{ marginRight: 6 }} />
                      {note || '登记证据'}
                    </a>
                  </Tooltip>
                );
              },
            },
          ]}
        />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          五看不预选：未判定的项显示「待核实」，必须在下面逐项给结论；证据写清文件名或来源，便于复核时点开原件。
        </Typography.Text>
      </div>

      {/* ── 逐项判定 + 证据（必填项） ── */}
      <div>
        <Typography.Text strong style={{ fontSize: 14 }}>
          逐项判定与证据 <Typography.Text type="danger">*</Typography.Text>
        </Typography.Text>
        <Spin spinning={reviewItems.length === 0} style={{ width: '100%' }}>
          <Space direction="vertical" size={10} style={{ width: '100%', marginTop: 8 }}>
            {reviewItems.map((it) => {
              const cur = draft[it.key];
              return (
                <Card size="small" key={it.key} styles={{ body: { padding: 10 } }}>
                  <Space direction="vertical" size={6} style={{ width: '100%' }}>
                    <Space size={8} wrap>
                      <Tag color="blue" style={{ margin: 0 }}>
                        {it.code}
                      </Tag>
                      <Typography.Text strong>{it.label}</Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {it.hint}
                      </Typography.Text>
                    </Space>
                    <Radio.Group
                      value={cur?.result}
                      onChange={(e) =>
                        setDraft((prev) => ({
                          ...prev,
                          [it.key]: { note: prev[it.key]?.note ?? '', result: e.target.value },
                        }))
                      }
                    >
                      <Space wrap>
                        {resultOptions.map((opt) => {
                          const selected = cur?.result === opt.value;
                          return (
                            <Radio.Button
                              key={opt.value}
                              value={opt.value}
                              style={
                                selected
                                  ? {
                                      ...softTagStyle(REVIEW_RESULT_COLOR[opt.value]),
                                      borderRadius: 6,
                                    }
                                  : { borderRadius: 6 }
                              }
                            >
                              {opt.label}
                            </Radio.Button>
                          );
                        })}
                      </Space>
                    </Radio.Group>
                    <Input.TextArea
                      ref={(el) => {
                        evidenceRefs.current[it.key] = el?.resizableTextArea?.textArea ?? null;
                      }}
                      rows={1}
                      maxLength={200}
                      placeholder={`证据 / 说明（如：近三年预算情况.pdf、拜访纪要已上传）— 对应「${it.hint}」`}
                      value={cur?.note ?? ''}
                      onChange={(e) =>
                        setDraft((prev) => ({
                          ...prev,
                          [it.key]: { result: prev[it.key]?.result, note: e.target.value },
                        }))
                      }
                    />
                  </Space>
                </Card>
              );
            })}
          </Space>
        </Spin>
      </div>

      {/* ── 人工决策（必填） ── */}
      <div>
        <Space size={8} wrap style={{ marginBottom: 8 }}>
          <Typography.Text strong style={{ fontSize: 14 }}>
            人工决策
          </Typography.Text>
          <Typography.Text type="danger" style={{ fontSize: 13 }}>
            （必填）
          </Typography.Text>
        </Space>

        {/* 蓝色信息条：AI 不作为考核依据（判据 C4） */}
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 8,
            background: 'var(--brand-soft)',
            border: '1px solid var(--brand-line)',
            borderRadius: 8,
            padding: '8px 12px',
            color: 'var(--brand-strong)',
            fontSize: 13,
            lineHeight: 1.6,
            marginBottom: 10,
          }}
        >
          <InfoCircleOutlined style={{ marginTop: 3 }} />
          <span>由管理员结合证据最终判定，AI 不作为考核依据。</span>
        </div>

        <Space size={8} align="center" wrap>
          <Typography.Text style={{ fontSize: 13 }}>执行判定</Typography.Text>
          <Radio.Group value={verdict} onChange={(e) => setVerdict(e.target.value)}>
            <Space size={16} wrap>
              {verdictOptions.map((opt) => (
                <Radio key={opt.value} value={opt.value}>
                  {opt.label}
                </Radio>
              ))}
            </Space>
          </Radio.Group>
        </Space>
        <div style={{ marginTop: 6 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            判定为「能力问题 / 态度问题」时，系统会自动在预警中心登记一条业务风险，便于统一跟踪；判定结果用于辅导与过程管理。
          </Typography.Text>
        </div>
      </div>

      {/* ── 评审结论与辅导动作 ── */}
      <div>
        <Space size={6} style={{ marginBottom: 6 }}>
          <Typography.Text strong style={{ fontSize: 14 }}>
            评审结论与辅导动作
          </Typography.Text>
          <Typography.Text type="danger">*</Typography.Text>
        </Space>
        <div style={{ position: 'relative' }}>
          <Input.TextArea
            rows={5}
            maxLength={CONCLUSION_MAX}
            placeholder="请结合项目进展、证据与团队情况，填写评审结论及后续辅导动作（≥10 字，如：重点辅导决策链打法，下周陪访一次并回填证据）"
            value={conclusion}
            onChange={(e) => setConclusion(e.target.value)}
            style={{ paddingBottom: 22 }}
          />
          <span
            style={{
              position: 'absolute',
              right: 12,
              bottom: 6,
              fontSize: 12,
              color: conclusion.length > CONCLUSION_MAX ? 'var(--danger)' : 'var(--text-muted)',
            }}
          >
            {conclusion.length}/{CONCLUSION_MAX}
          </span>
        </div>
        <Typography.Text type={conclusionTooShort ? 'danger' : 'secondary'} style={{ fontSize: 12 }}>
          结论要能复盘：至少 {CONCLUSION_MIN} 字（当前 {trimmedConclusion.length} 字）。写清"下一步辅导动作 + 谁在什么时候做"。
        </Typography.Text>
      </div>

      {formError && <Alert type="error" showIcon message={formError} />}

      {/* ── 底部一行：下次评审 + 保存评审 ── */}
      <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12 }}>
        <Space size={8} align="center" wrap>
          <Typography.Text strong style={{ fontSize: 13 }}>下次评审</Typography.Text>
          <DatePicker
            value={nextReviewAt}
            onChange={(v) => setNextReviewAt(v ?? dayjs().add(7, 'day'))}
            allowClear={false}
            placeholder="选择下次评审日期"
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            默认一周后（周会节奏），到期未评审会重新进入「待评审项目」
          </Typography.Text>
        </Space>
        <div style={{ marginTop: 10, display: 'flex', justifyContent: 'flex-end' }}>
          <Tooltip title={canSave ? '' : `还差：${blockReasons.join('；')}`}>
            <Button
              type="primary"
              size="large"
              loading={doSubmit.isPending}
              disabled={!canSave}
              onClick={submit}
            >
              保存评审
            </Button>
          </Tooltip>
        </div>
        {!canSave && (
          <div style={{ marginTop: 8, textAlign: 'right' }}>
            <Typography.Text type="danger" style={{ fontSize: 12 }}>
              「保存评审」暂不可用，还差：
              {blockReasons.map((b, i) => (
                <span key={b}>
                  {i > 0 ? '；' : ''}
                  {b}
                </span>
              ))}
            </Typography.Text>
          </div>
        )}
      </div>
    </Space>
  );

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <PageHeader
        title="项目评审"
        subtitle="管理者过项目：先看左侧待评审清单，再在右侧评审卡里逐项做五看评估与人工决策。结论用于辅导与过程管理，不作为考核依据。"
        demo
        actions={
          <Space wrap>
            <Button onClick={() => navigate('/reports/weekly-meeting')}>去周会视图</Button>
            <Button
              icon={<ReloadOutlined />}
              loading={pendingQuery.isFetching}
              onClick={() => pendingQuery.refetch()}
            >
              刷新
            </Button>
          </Space>
        }
      />

      <Row gutter={[12, 12]}>
        <Col xs={12} md={8}>
          <KpiCard
            label="待评审项目"
            value={pendingQuery.data?.total ?? 0}
            suffix="个"
            icon={<WarningOutlined />}
            tone={(pendingQuery.data?.total ?? 0) > 0 ? 'amber' : 'gray'}
            hint="待评审口径：进入闭环二后超过阈值天数（默认 14 天，规则 stuck_missing_review_days 可配）未评审，或已到上次填的「下次评审时间」的商机数"
          />
        </Col>
        <Col xs={12} md={8}>
          <KpiCard
            label="历史评审记录"
            value={historyQuery.data?.total ?? 0}
            suffix="张"
            icon={<AuditOutlined />}
            tone="brand"
            hint="按当前筛选（执行判定 / 评审人）统计的评审卡张数；一次评审 = 一张卡，可在下方「评审历史」展开回看五项判定与证据"
          />
        </Col>
        <Col xs={12} md={8}>
          <KpiCard
            label="必判维度"
            value={reviewItems.length + 1}
            suffix="项"
            icon={<CheckCircleOutlined />}
            tone="purple"
            hint="评审卡 = 五看逐项判定 + 1 个「执行判定」，共这一数量个必判维度；五看不预选，必须逐项给结论"
          />
        </Col>
      </Row>

      <Row gutter={[12, 12]} align="top">
        <Col xs={24} xxl={14}>
          <Tabs
            activeKey={tab}
            onChange={setTab}
            items={[
              {
                key: 'pending',
                label: pendingFiltered
                  ? `待评审清单（${filteredPending.length}/${pendingQuery.data?.total ?? 0}）`
                  : `待评审清单（${pendingQuery.data?.total ?? 0}）`,
                children: pendingCard,
              },
              {
                key: 'history',
                label: `评审历史（${historyQuery.data?.total ?? 0}）`,
                children: historyCard,
              },
            ]}
          />
        </Col>
        <Col xs={24} xxl={10}>
          <Card
            size="small"
            title={
              <Space size={8}>
                <span style={{ fontSize: 16, fontWeight: 600 }}>项目评审</span>
              </Space>
            }
            extra={<span className="panel-flag">演示数据</span>}
          >
            {cardBody}
          </Card>
        </Col>
      </Row>

      <Footnote>
        评审卡口径：五看（关键决策者 / 意向与资金 / 需求与确认人 / 干系人识别 / 未知竞品）逐项判定（通过 / 有风险 /
        不通过，不预选）+ 人工「执行判定」（正常 / 能力问题 / 态度问题）+ 评审结论与辅导动作（≥{CONCLUSION_MIN} 字）+
        下次评审时间，四项齐了「保存评审」才可用。证据以文件名 / 来源登记，可回商机详情核对原件；
        判定为能力 / 态度问题会自动在 2.4 风险与卡单中心登记一条业务风险。
        待评审清单的「负责人 / 环节」筛选<b>只作用于当前清单</b>（后端 /reviews/pending 未支持查询参数），
        评审历史的「执行判定 / 评审人」筛选走后端查询。
        评审结论用于辅导与过程管理，AI 结论仅作辅助、不作为考核依据；比较两期转化率差异请用百分点（如「较上期 +3.2 个百分点」）。
      </Footnote>
      <NotForAssessment />
    </Space>
  );
}
