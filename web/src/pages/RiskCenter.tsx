import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BORDER, BRAND, DANGER } from '@/theme';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Form,
  Input,
  Modal,
  Progress,
  Row,
  Segmented,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  AlertOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  FieldTimeOutlined,
  ReloadOutlined,
  RiseOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import dayjs, { type Dayjs } from 'dayjs';
import { fetchRiskCenter, fetchStaffOptions } from '@/api/dashboard';
import { fetchConversion, handleRisk, listRisks, scanRisks } from '@/api/risks';
import { useDicts } from '@/hooks/useDicts';
import { useAuthStore } from '@/store/auth';
import KpiCard from '@/components/KpiCard';
import PageHeader from '@/components/PageHeader';
import { Footnote, NextStepGuide, NotForAssessment, PanelFlag } from '@/components/Guide';
import {
  ALERT_KIND_COLOR,
  RISK_STATUS_COLOR,
  fenToWan,
  fmtDate,
  fmtDateTime,
  fmtPercent,
  toLabelMap,
} from '@/utils/format';
import type { ConversionPair, ConversionPerson, Lead, RiskAlert } from '@/types';

const STUCK_PAGE_SIZE = 10;
const ALERT_PAGE_SIZE = 10;

/** 状态胶囊（对齐设计稿 09 的筛选胶囊行） */
const STATUS_SEGMENTS = [
  { value: 'pending', label: '待处理' },
  { value: 'processing', label: '处理中' },
  { value: 'resolved', label: '已解除' },
  { value: 'all', label: '全部' },
];

/** 预警状态下拉（与后端 RiskStatus 一致） */
const STATUS_OPTIONS = [
  { value: 'pending', label: '待处理' },
  { value: 'processing', label: '处理中' },
  { value: 'resolved', label: '已解除' },
];

/** 已有旧提醒可查询历史，新R5统一按项目下一节点判断。 */
const RHYTHM_KIND_OPTIONS = [
  { value: 'business_risk', label: '业务风险（含R5）' },
];
const RHYTHM_EXPLAIN = [{
  kind: 'business_risk', title: 'R5 项目节奏不清', role: '统一口径',
  rule: '下一关键节点或预计时间缺失，或节点到期需要确认完成情况及新安排。允许暂定时间及区间；签约后停止销售R5。',
  how: '通过项目跟进或审查确认下一节点及预计时间，统一进入项目档案；不强制填写预计签约日期。',
}];

/** 超期口径：待处理 / 处理中且创建超过 7 天未闭环 */
const OVERDUE_DAYS = 7;

/**
 * 系统预警与业务风险分域展示；旧节奏类型仅保留历史查询。
 *
 * * **系统预警**：系统按规则自动扫描生成（卡单 / 转化率 / 总时长 + 节奏不清），
 *   人只能"处理"，不能凭空造；口径固定、可复算。
 * * **业务风险**：`kind = business_risk`，由专员 / AI 人工登记的 R1–R5（资金 / 信任 / 决策链 / 需求 / 节奏），
 *   带具体 `risk_type`，其中R5统一核查节点及预计时间。
 *
 * 两者混在一张表里时，管理者最容易误判"这是系统判的还是人填的"，所以这里既给筛选也给列上标签。
 */
const BUSINESS_RISK_KIND = 'business_risk';

/** 商机侧系统预警，含可追溯的历史节奏类型。 */
const SALES_SYSTEM_KINDS = ['stuck', 'total_duration', 'rhythm_unclear', 'rhythm_window', 'conversion'];

/** 运营侧（闭环三）系统预警：本页也会出现，单列一类，避免混进商机侧口径 */
const DELIVERY_SYSTEM_KINDS = [
  'delivery_delay',
  'long_no_contact',
  'acceptance_due',
  'complaint_open',
  'issue_overdue',
  'promise_unmet',
];

type AlertCategory = 'business' | 'system_sales' | 'system_delivery';

function categoryOf(kind: string | undefined | null): AlertCategory {
  if ((kind ?? '') === BUSINESS_RISK_KIND) return 'business';
  if (SALES_SYSTEM_KINDS.includes(kind ?? '')) return 'system_sales';
  if (DELIVERY_SYSTEM_KINDS.includes(kind ?? '')) return 'system_delivery';
  // 未知种类按系统预警兜底（不让新种类悄悄消失）
  return 'system_sales';
}

const CATEGORY_LABEL: Record<AlertCategory, string> = {
  business: '业务风险（R1–R5）',
  system_sales: '系统预警（商机侧）',
  system_delivery: '系统预警（运营侧）',
};

const CATEGORY_COLOR: Record<AlertCategory, string> = {
  business: 'volcano',
  system_sales: 'blue',
  system_delivery: 'purple',
};

/**
 * 2.4 风险与卡单中心 —— 对齐设计稿 09 的「左表 + 右详情」骨架：
 * 顶部 4 个 KPI（待处理 / 已超期 / 已闭环 / 转化率预警）· 筛选胶囊 · 左表（客户名 / 种类 / 触发规则 / 说明 /
 * 责任人 / 时间 + 处理按钮）· 右详情（口径 + 两类节奏预警的区别 + 处理留痕）。
 *
 * 判据：C2（处理必须填说明，置灰给原因）、C3（每类预警的触发规则与超期口径就地可见）、C4（预警不作为考核依据）。
 */
export default function RiskCenter() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const role = useAuthStore((s) => s.user?.role);
  const isManager = role === 'admin' || role === 'market_manager';
  const { dicts } = useDicts();
  const kindMap = toLabelMap(dicts.alert_kinds);

  /** 预警种类筛选：以后端字典为准，若字典未下发节奏两类则就地补上（R5 主口径必须可筛） */
  const kindOptions = useMemo(() => {
    const base = (dicts.alert_kinds ?? []).map((d) => ({ value: d.value, label: d.label }));
    const exist = new Set(base.map((o) => o.value));
    return [...base, ...RHYTHM_KIND_OPTIONS.filter((o) => !exist.has(o.value))];
  }, [dicts.alert_kinds]);

  /* ---------------- 顶部总览（含卡单 / 长周期 / 转化率概览） ---------------- */
  const { data, isLoading } = useQuery({
    queryKey: ['dashboard-risk-center'],
    queryFn: fetchRiskCenter,
  });

  /* ---------------- ① 预警清单 ---------------- */
  /** 服务端筛选（后端 /risks 支持）：状态 / 种类 / 只看我的 */
  const [alertStatus, setAlertStatus] = useState<string>('pending');
  const [alertKind, setAlertKind] = useState<string | undefined>(undefined);
  const [alertMine, setAlertMine] = useState(false);
  const [alertPage, setAlertPage] = useState(1);
  /** 当前页前端筛选：预警类别（系统预警 vs R1–R5 业务风险）、负责人 */
  const [alertCategory, setAlertCategory] = useState<'all' | AlertCategory>('all');
  const [alertOwner, setAlertOwner] = useState<string | undefined>(undefined);
  /** 右侧详情选中的预警（对齐设计稿 09 左表 + 右详情） */
  const [selectedId, setSelectedId] = useState<string | null>(null);
  /** 批量处理：勾选的预警 id */
  const [batchIds, setBatchIds] = useState<string[]>([]);
  const [batchOpen, setBatchOpen] = useState(false);

  const alertsQuery = useQuery({
    queryKey: ['risks', { status: alertStatus, kind: alertKind, mine: alertMine, page: alertPage }],
    queryFn: () =>
      listRisks({
        status: alertStatus === 'all' ? undefined : alertStatus,
        kind: alertKind,
        mine: alertMine || undefined,
        page: alertPage,
        page_size: ALERT_PAGE_SIZE,
      }),
  });

  /** 服务端返回的当前页原始数据 */
  const pageAlerts = alertsQuery.data?.items ?? [];

  /** 负责人下拉（管理人员才有意义；专员后端强制只看自己） */
  const { data: riskStaff } = useQuery({
    queryKey: ['dashboard-staff-options'],
    queryFn: fetchStaffOptions,
    enabled: isManager,
    retry: false,
  });

  /** 当前页前端筛选：类别 + 负责人（口径已写在筛选条与页脚） */
  const alertRows = useMemo(
    () =>
      pageAlerts.filter((a) => {
        if (alertCategory !== 'all' && categoryOf(a.kind) !== alertCategory) return false;
        if (alertOwner && a.owner_id !== alertOwner) return false;
        return true;
      }),
    [pageAlerts, alertCategory, alertOwner],
  );

  const pageCategoryCount = useMemo(() => {
    const acc = { business: 0, system_sales: 0, system_delivery: 0 };
    pageAlerts.forEach((a) => {
      acc[categoryOf(a.kind)] += 1;
    });
    return acc;
  }, [pageAlerts]);

  const clientFiltered = alertCategory !== 'all' || !!alertOwner;

  /**
   * 翻页 / 换筛选后清空批量勾选：否则「已选 N 条」里会混进当前看不到的行，
   * 用户无法核对到底改了哪几条（批量操作必须可见可核对）。
   */
  useEffect(() => {
    setBatchIds([]);
  }, [alertStatus, alertKind, alertMine, alertOwner, alertCategory, alertPage]);

  const selected = alertRows.find((a) => a.id === selectedId) ?? null;

  /** 已解除 / 处理中的全量计数（顶部 KPI 用；不受左表状态筛选影响） */
  const resolvedQuery = useQuery({
    queryKey: ['risks', { status: 'resolved', page: 1, size: 1 }],
    queryFn: () => listRisks({ status: 'resolved', page: 1, page_size: 1 }),
  });
  const processingQuery = useQuery({
    queryKey: ['risks', { status: 'processing', page: 1, size: 1 }],
    queryFn: () => listRisks({ status: 'processing', page: 1, page_size: 1 }),
  });

  /** 已超期 = 未解除且创建超过 7 天（口径写在 KPI hint 上，只统计当前页前端筛选后的行） */
  const overdueCount = useMemo(() => {
    const now = dayjs();
    return alertRows.filter(
      (a) => a.status !== 'resolved' && now.diff(dayjs(a.created_at), 'day') > OVERDUE_DAYS,
    ).length;
  }, [alertRows]);

  const [handling, setHandling] = useState<RiskAlert | null>(null);
  const [handleForm] = Form.useForm<{ status: string; handle_note: string }>();
  /** 处理说明是否已填（未填 → 提交置灰并说明原因，对应"处理时填处理说明"的硬要求） */
  const [noteFilled, setNoteFilled] = useState(false);

  const invalidateAll = () => {
    qc.invalidateQueries({ queryKey: ['dashboard-risk-center'] });
    qc.invalidateQueries({ queryKey: ['risks'] });
    qc.invalidateQueries({ queryKey: ['opportunity-detail'] });
    qc.invalidateQueries({ queryKey: ['workbench'] });
  };

  const doScan = useMutation({
    mutationFn: scanRisks,
    onSuccess: (r) => {
      message.success(
        `扫描完成：共扫描 ${r.scanned} 个在途商机，新增预警 ${r.total_created} 条` +
          (r.breached?.length ? `；转化率预警环节 ${r.breached.length} 个` : ''),
      );
      invalidateAll();
    },
  });

  const doHandle = useMutation({
    mutationFn: (v: { riskId: string; status: string; note: string }) =>
      handleRisk(v.riskId, { status: v.status, handle_note: v.note.trim() }),
    onSuccess: () => {
      message.success('预警处理状态与处理说明已留存');
      setHandling(null);
      handleForm.resetFields();
      setNoteFilled(false);
      invalidateAll();
    },
  });

  const openHandle = (r: RiskAlert) => {
    setHandling(r);
    setNoteFilled(!!(r.handle_note ?? '').trim());
    handleForm.setFieldsValue({
      status: r.status === 'pending' ? 'processing' : r.status,
      handle_note: r.handle_note ?? '',
    });
  };

  /* ---------------- 批量处理（管理人员视角：一次处理多条同类预警） ---------------- */

  const [batchForm] = Form.useForm<{ status: string; handle_note: string }>();
  const [batchNoteFilled, setBatchNoteFilled] = useState(false);

  const openBatch = () => {
    batchForm.setFieldsValue({ status: 'processing', handle_note: '' });
    setBatchNoteFilled(false);
    setBatchOpen(true);
  };

  /**
   * 批量处理：逐条调用 `PATCH /risks/{id}`，**共用同一段处理说明**。
   * 后端没有批量接口，因此这里显式串行/并行提交并回报成功条数——
   * 不做"看起来成功其实只改了第一条"的假批量。
   */
  const doBatch = useMutation({
    mutationFn: async (v: { ids: string[]; status: string; note: string }) => {
      const results = await Promise.allSettled(
        v.ids.map((id) => handleRisk(id, { status: v.status, handle_note: v.note.trim() })),
      );
      return {
        ok: results.filter((r) => r.status === 'fulfilled').length,
        fail: results.filter((r) => r.status === 'rejected').length,
      };
    },
    onSuccess: (r) => {
      if (r.fail === 0) {
        message.success(`已批量处理 ${r.ok} 条预警，处理说明已逐条留痕`);
      } else if (r.ok === 0) {
        message.error(`批量处理失败（${r.fail} 条都没成功），请检查登录状态后重试`);
      } else {
        message.warning(`部分成功：${r.ok} 条已处理，${r.fail} 条失败（网络或权限问题），可重试失败项`);
      }
      setBatchOpen(false);
      setBatchIds([]);
      batchForm.resetFields();
      setBatchNoteFilled(false);
      invalidateAll();
    },
  });

  const alertColumns: ColumnsType<RiskAlert> = [
    {
      title: '客户名',
      dataIndex: 'lead_name',
      width: 140,
      render: (v: string | null | undefined, r) => (
        <a
          onClick={(e) => {
            e.stopPropagation();
            navigate(`/opportunities/${r.lead_id}`);
          }}
          style={{ fontWeight: 600 }}
        >
          {v || '（客户名缺失，点开商机核对）'}
        </a>
      ),
    },
    {
      title: '类别',
      key: 'category',
      width: 138,
      render: (_, r) => {
        const cat = categoryOf(r.kind);
        return (
          <Tooltip
            title={
              cat === 'business'
                ? '业务风险（R1–R5）：由专员 / AI 人工登记，带具体风险类型（资金 / 信任 / 决策链 / 需求 / 节奏），其中R5统一核查节点及预计时间'
                : cat === 'system_sales'
                  ? '系统预警（商机侧·闭环二）：由系统按规则自动扫描生成（卡单 / 转化率 / 总时长），口径固定、可复算，人只能处理不能凭空造'
                  : '系统预警（运营侧·闭环三）：交付 / 验收 / 投诉 / 产品问题类，由运营侧规则生成，与商机侧口径分开看'
            }
          >
            <Tag color={CATEGORY_COLOR[cat]}>{CATEGORY_LABEL[cat]}</Tag>
          </Tooltip>
        );
      },
    },
    {
      title: '种类',
      dataIndex: 'kind_label',
      width: 130,
      render: (v: string, r) => (
        <Tag color={ALERT_KIND_COLOR[r.kind] ?? 'default'}>{v || kindMap[r.kind] || r.kind}</Tag>
      ),
    },
    {
      title: '触发规则',
      dataIndex: 'trigger_rule',
      width: 180,
      ellipsis: true,
      render: (v: string) =>
        v ? (
          <Typography.Text ellipsis style={{ fontSize: 13 }} title={v}>
            {v}
          </Typography.Text>
        ) : (
          <Typography.Text type="secondary">-</Typography.Text>
        ),
    },
    {
      title: '说明',
      dataIndex: 'description',
      ellipsis: true,
      render: (v: string) => (
        <Typography.Text ellipsis style={{ fontSize: 13 }} title={v || ''}>
          {v || '-'}
        </Typography.Text>
      ),
    },
    {
      title: '责任人',
      dataIndex: 'owner_name',
      width: 80,
      render: (v: string | null) => v || '-',
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 140,
      render: (v: string) => fmtDateTime(v),
    },
    {
      title: '处理',
      width: 72,
      render: (_, r) => (
        <Button
          type="link"
          size="small"
          onClick={(e) => {
            e.stopPropagation();
            openHandle(r);
          }}
        >
          {r.status === 'resolved' ? '查看' : '处理'}
        </Button>
      ),
    },
  ];

  const rhythmPanel = (
    <div style={{ display: 'grid', gap: 8 }}>
      {RHYTHM_EXPLAIN.map((r) => (
        <div
          key={r.kind}
          style={{
            border: '1px solid var(--border)',
            borderRadius: 8,
            padding: '8px 10px',
            background: 'var(--bg-layout)',
          }}
        >
          <Space size={6} wrap>
            <Tag color={ALERT_KIND_COLOR[r.kind] ?? 'default'}>{r.title}</Tag>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {r.role}
            </Typography.Text>
          </Space>
          <div style={{ fontSize: 12, color: 'var(--text-sub)', marginTop: 4, lineHeight: 1.7 }}>
            <div>
              <b>触发规则：</b>
              {r.rule}
            </div>
            <div>
              <b>怎么办：</b>
              {r.how}
            </div>
          </div>
        </div>
      ))}
    </div>
  );

  const detailPanel = !selected ? (
    <NextStepGuide
      compact
      title="左侧选一条预警，这里看规则与处理留痕"
      description="每条预警都能看到「触发规则 + 说明 + 责任人 + 时间」，点「处理」时要写清处理说明（必填），留痕后可在商机详情回看。"
    />
  ) : (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <Space size={8} wrap>
        <Typography.Text strong style={{ fontSize: 15 }}>
          {selected.lead_name || '（客户名缺失）'}
        </Typography.Text>
        <Tag color={ALERT_KIND_COLOR[selected.kind] ?? 'default'}>
          {selected.kind_label || selected.kind}
        </Tag>
        <Tag color={RISK_STATUS_COLOR[selected.status] ?? 'default'}>
          {selected.status_label || selected.status}
        </Tag>
      </Space>
      <a onClick={() => navigate(`/opportunities/${selected.lead_id}`)}>查看商机详情（就地补证据 / 补时间）</a>
      <div style={{ fontSize: 13, color: 'var(--text-sub)', lineHeight: 1.8 }}>
        <div>
          <b>触发规则：</b>
          {selected.trigger_rule || '—'}
        </div>
        <div>
          <b>说明：</b>
          {selected.description || '—'}
        </div>
        <div>
          <b>责任人：</b>
          {selected.owner_name || '—'}　<b>时间：</b>
          {fmtDateTime(selected.created_at)}
        </div>
        <div>
          <b>处理人 / 说明：</b>
          {selected.handler_name
            ? `${selected.handler_name} · ${selected.handle_note || '（未写说明）'} · ${fmtDateTime(selected.handled_at)}`
            : '尚未处理'}
        </div>
      </div>
      {selected.kind === 'rhythm_unclear' || selected.kind === 'rhythm_window' ? (
        <Alert type="info" showIcon message="这条属于节奏类预警" description={rhythmPanel} />
      ) : null}
      <Space wrap>
        <Button type="primary" onClick={() => openHandle(selected)}>
          {selected.status === 'resolved' ? '补充处理说明' : '处理这条预警'}
        </Button>
        <Button onClick={() => setSelectedId(null)}>收起</Button>
      </Space>
    </Space>
  );

  /**
   * 系统预警 vs 业务风险：一张面板说清"谁生成的、怎么算的、怎么办"。
   * spec 2.4 明确要求两者**分开看**，否则管理者会把"系统判的"误当成"人填的"。
   */
  const categoryPanel = (
    <div
      style={{
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: '10px 12px',
        background: 'var(--bg-layout)',
        height: '100%',
      }}
    >
      <Space size={8} wrap style={{ marginBottom: 6 }}>
        <AlertOutlined style={{ color: 'var(--brand)' }} />
        <Typography.Text strong>系统预警 与 R1–R5 业务风险的区别</Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          （本页当前显示 {pageAlerts.length} 条）
        </Typography.Text>
      </Space>
      <div style={{ display: 'grid', gap: 8 }}>
        <div style={{ fontSize: 12, color: 'var(--text-sub)', lineHeight: 1.7 }}>
          <Space size={6} wrap>
            <Tag color={CATEGORY_COLOR.system_sales}>系统预警（商机侧）</Tag>
            <b>{pageCategoryCount.system_sales}</b> 条
          </Space>
          <div>
            <b>三类：</b>卡单（停留超基准且标准未完成）/ 转化率（低于基准 × 预警比例）/
            总时长（接近配置期限）
          </div>
          <div>
            <b>怎么来的：</b>系统按 6.7 预警规则自动扫描生成，口径固定可复算——<b>不是人填的，也不能凭空造</b>。
          </div>
          <div>
            <b>怎么办：</b>点「处理」写下处理说明（必填）即可留痕；卡单与节奏类通常要回商机详情补证据 / 补时间承诺。
          </div>
        </div>
        <div style={{ fontSize: 12, color: 'var(--text-sub)', lineHeight: 1.7 }}>
          <Space size={6} wrap>
            <Tag color={CATEGORY_COLOR.business}>业务风险（R1–R5）</Tag>
            <b>{pageCategoryCount.business}</b> 条
          </Space>
          <div>
            <b>五类：</b>R1 资金预算不清 / R2 信任不到 / R3 决策链不清 / R4 客户需求不清 / R5 项目节奏不清
          </div>
          <div>
            <b>怎么来的：</b>专员在商机详情「风险」Tab 登记（或确认 AI 草稿后落库），其中R5按档案中的下一节点及预计时间统一核查。
          </div>
          <div>
            <b>怎么办：</b>处理时写清"做了什么、什么时候再跟进"；解除后仍可在商机详情回看留痕。
          </div>
        </div>
        {pageCategoryCount.system_delivery > 0 && (
          <div style={{ fontSize: 12, color: 'var(--text-sub)', lineHeight: 1.7 }}>
            <Space size={6} wrap>
              <Tag color={CATEGORY_COLOR.system_delivery}>系统预警（运营侧·闭环三）</Tag>
              <b>{pageCategoryCount.system_delivery}</b> 条
            </Space>
            <div>
              <b>口径：</b>交付延期 / 长期未联系 / 验收临近 / 投诉未闭环 / 产品问题久拖 / 承诺未兑现，
              由运营侧规则生成，与商机侧口径分开看；处理在 3.x 运营页面。
            </div>
          </div>
        )}
      </div>
    </div>
  );

  const alertTab = (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {/* 两类预警必须分开看：左边系统预警 vs R1–R5 业务风险；右边两类节奏预警的区别 */}
      <Row gutter={[12, 12]}>
        <Col xs={24} xl={12}>
          {categoryPanel}
        </Col>
        <Col xs={24} xl={12}>
          <div
            style={{
              border: '1px solid var(--border)',
              borderRadius: 8,
              padding: '10px 12px',
              background: 'var(--warning-soft)',
              height: '100%',
            }}
          >
            <Space size={8} wrap style={{ marginBottom: 6 }}>
              <FieldTimeOutlined style={{ color: 'var(--warning)' }} />
              <Typography.Text strong>「项目节奏不清」与「年度节奏窗口参照」的区别</Typography.Text>
            </Space>
            {rhythmPanel}
          </div>
        </Col>
      </Row>

      {/* 商机侧快捷筛选：后端 /risks 的 kind 参数是单值，所以给一键胶囊（服务端全量，不是仅当前页） */}
      <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', background: 'var(--bg-card)' }}>
        <Space wrap size={8} align="center">
          <Typography.Text strong style={{ fontSize: 13 }}>
            商机侧快捷筛选：
          </Typography.Text>
          {[
            { k: 'stuck', l: '卡单预警' },
            { k: 'total_duration', l: '总时长预警' },
            { k: 'rhythm_unclear', l: '项目节奏不清' },
            { k: 'rhythm_window', l: '年度节奏窗口' },
            { k: 'business_risk', l: '业务风险 R1–R5' },
          ].map((o) => (
            <Button
              key={o.k}
              size="small"
              type={alertKind === o.k ? 'primary' : 'default'}
              onClick={() => {
                setAlertPage(1);
                setSelectedId(null);
                setAlertKind(alertKind === o.k ? undefined : o.k);
              }}
            >
              {o.l}
            </Button>
          ))}
          {alertKind && (
            <Button
              size="small"
              onClick={() => {
                setAlertKind(undefined);
                setAlertPage(1);
              }}
            >
              清除种类筛选
            </Button>
          )}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            这是<b>服务端全量</b>筛选；闭环三（运营侧）的预警也会出现在同一张表里，
            所以默认「待处理」列表可能先被运营侧预警占满，用这里的胶囊按种类隔离即可。
          </Typography.Text>
        </Space>
      </div>

      <Row gutter={[12, 12]}>
        <Col xs={24} xl={16}>
          <Card size="small" styles={{ body: { paddingTop: 8 } }}>
            <Space wrap size={8} style={{ marginBottom: 10, width: '100%', justifyContent: 'space-between' }}>
              <Space wrap size={8}>
                <Segmented
                  value={alertStatus}
                  onChange={(v) => {
                    setAlertPage(1);
                    setSelectedId(null);
                    setAlertStatus(String(v));
                  }}
                  options={STATUS_SEGMENTS}
                />
                <Tooltip title="预警类别：系统按规则自动生成的（卡单 / 转化率 / 总时长）与专员人工登记的 R1–R5 业务风险分开看。类别筛选只作用于当前页。">
                  <Segmented
                    value={alertCategory}
                    onChange={(v) => {
                      setSelectedId(null);
                      setAlertCategory(v as 'all' | AlertCategory);
                    }}
                    options={[
                      { value: 'all', label: '不限类别' },
                      { value: 'system_sales', label: `系统预警 ${pageCategoryCount.system_sales}` },
                      { value: 'business', label: `业务风险 R1–R5 ${pageCategoryCount.business}` },
                    ]}
                  />
                </Tooltip>
                <Select
                  allowClear
                  style={{ width: 200 }}
                  placeholder="全部种类"
                  value={alertKind}
                  onChange={(v) => {
                    setAlertPage(1);
                    setAlertKind(v ?? undefined);
                  }}
                  options={kindOptions}
                />
                {isManager && (
                  <>
                    <Tooltip title="负责人：只筛当前页里属于某个责任人的预警；要看全团队某人的全部预警请让他按状态逐页查看（后端 /risks 未支持按 owner 过滤）">
                      <Select
                        allowClear
                        showSearch
                        optionFilterProp="label"
                        style={{ width: 140 }}
                        placeholder="全部负责人"
                        value={alertOwner}
                        onChange={(v) => {
                          setSelectedId(null);
                          setAlertOwner(v ?? undefined);
                        }}
                        options={(riskStaff?.items ?? []).map((s) => ({
                          value: s.user_id,
                          label: s.name,
                        }))}
                      />
                    </Tooltip>
                    <Tooltip title="只看我的：走后端 mine 参数（服务端全量筛选，不是仅当前页）">
                      <Segmented
                        value={alertMine ? 'mine' : 'all'}
                        onChange={(v) => {
                          setAlertPage(1);
                          setSelectedId(null);
                          setAlertMine(v === 'mine');
                        }}
                        options={[
                          { value: 'all', label: '全团队' },
                          { value: 'mine', label: '只看我的' },
                        ]}
                      />
                    </Tooltip>
                  </>
                )}
                <Button
                  icon={<ReloadOutlined />}
                  onClick={() => alertsQuery.refetch()}
                  loading={alertsQuery.isFetching}
                >
                  刷新
                </Button>
              </Space>
              <Space>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  共 {alertsQuery.data?.total ?? 0} 条
                  {clientFiltered ? `（本页筛后 ${alertRows.length}）` : ''}
                </Typography.Text>
                <Button
                  type="primary"
                  icon={<ThunderboltOutlined />}
                  loading={doScan.isPending}
                  onClick={() => doScan.mutate()}
                >
                  手工触发扫描
                </Button>
              </Space>
            </Space>

            {clientFiltered && (
              <Space size={8} style={{ marginBottom: 8 }} wrap>
                <PanelFlag>仅对当前页</PanelFlag>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  类别 / 负责人是本页前端筛选（后端 /risks 支持状态、种类、只看我的）；本页{' '}
                  {pageAlerts.length} 条 → 筛后 {alertRows.length} 条。
                </Typography.Text>
                <Button
                  size="small"
                  onClick={() => {
                    setAlertCategory('all');
                    setAlertOwner(undefined);
                  }}
                >
                  清掉本页筛选
                </Button>
              </Space>
            )}

            {/* 批量处理条：选中多行后可一次性处理（逐条 PATCH，共用同一段处理说明） */}
            {batchIds.length > 0 && (
              <div
                style={{
                  marginBottom: 8,
                  padding: '6px 10px',
                  background: 'var(--brand-soft)',
                  borderRadius: 8,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  flexWrap: 'wrap',
                }}
              >
                <Typography.Text style={{ fontSize: 13 }}>
                  已选 <b>{batchIds.length}</b> 条预警
                </Typography.Text>
                <Button type="primary" size="small" onClick={openBatch}>
                  批量处理
                </Button>
                <Button size="small" onClick={() => setBatchIds([])}>
                  取消选择
                </Button>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  批量处理会逐条调用处理接口并共用同一段处理说明（每条都会留痕）；后端没有批量接口，成功/失败条数会如实回报。
                </Typography.Text>
              </div>
            )}

            <Table<RiskAlert>
              rowKey="id"
              size="small"
              loading={alertsQuery.isLoading}
              dataSource={alertRows}
              columns={alertColumns}
              rowSelection={{
                selectedRowKeys: batchIds,
                onChange: (keys) => setBatchIds(keys as string[]),
                getCheckboxProps: (r) => ({ disabled: r.status === 'resolved' }),
              }}
              rowClassName={(r) => (r.id === selectedId ? 'ant-table-row-selected' : '')}
              onRow={(r) => ({ onClick: () => setSelectedId(r.id), style: { cursor: 'pointer' } })}
              locale={{
                emptyText: alertsQuery.isError ? (
                  <NextStepGuide
                    compact
                    title="预警清单没读出来（接口 /risks 返回失败）"
                    description="为什么：请求没成功，可能是登录过期、网络中断或后端暂时不可用。怎么办：点「刷新」重试；仍失败请重新登录，并把这条提示发给系统管理员。"
                    actions={
                      <Button size="small" type="primary" onClick={() => alertsQuery.refetch()}>
                        重试
                      </Button>
                    }
                  />
                ) : (
                  <NextStepGuide
                    compact
                    title="暂无符合条件的预警"
                    description={
                      clientFiltered
                        ? '类别 / 负责人是本页前端筛选，符合条件的预警可能在别的页。可以清掉本页筛选，或翻页后继续筛。'
                        : alertStatus === 'pending'
                          ? '当前没有待处理预警。系统会在卡单、总时长、缺时间承诺、转化率低于预警线时自动生成；也可以点「手工触发扫描」立刻扫一遍在途商机。'
                          : '换个状态 / 种类筛选看看；或者点「手工触发扫描」立刻扫一遍在途商机。'
                    }
                    actions={
                      <Space wrap>
                        <Button size="small" onClick={() => setAlertStatus('pending')}>
                          看待处理
                        </Button>
                        {clientFiltered && (
                          <Button
                            size="small"
                            onClick={() => {
                              setAlertCategory('all');
                              setAlertOwner(undefined);
                            }}
                          >
                            清掉本页筛选
                          </Button>
                        )}
                        <Button
                          size="small"
                          type="primary"
                          loading={doScan.isPending}
                          onClick={() => doScan.mutate()}
                        >
                          手工触发扫描
                        </Button>
                      </Space>
                    }
                  />
                ),
              }}
              pagination={{
                current: alertPage,
                pageSize: ALERT_PAGE_SIZE,
                total: alertsQuery.data?.total ?? 0,
                showSizeChanger: false,
                showTotal: (t) => `共 ${t} 条`,
                onChange: (p) => setAlertPage(p),
              }}
            />
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card size="small" title="预警详情与处理">
            {detailPanel}
          </Card>
        </Col>
      </Row>
    </Space>
  );

  /* ---------------- ② 卡单与长周期 ---------------- */
  const stuckColumns: ColumnsType<Lead> = [
    {
      title: '客户名称',
      dataIndex: 'name',
      width: 200,
      render: (v: string, r) => <a onClick={() => navigate(`/opportunities/${r.id}`)}>{v}</a>,
    },
    {
      title: '当前环节',
      dataIndex: 'current_stage_label',
      width: 200,
      render: (v: string, r) => (
        <Space size={6}>
          <span>{v || r.current_stage || '-'}</span>
          {r.stage_overdue && <Tag color="red">超期</Tag>}
        </Space>
      ),
    },
    {
      title: '停留 / 基准',
      width: 130,
      render: (_, r) => (
        <Typography.Text type={r.stage_overdue ? 'danger' : undefined}>
          {r.stage_stay_days ?? 0} / {r.stage_base_days ?? 0} 天
        </Typography.Text>
      ),
    },
    {
      title: '风险标记',
      dataIndex: 'risk_labels',
      render: (v: string[]) => (v?.length ? v.join('、') : '-'),
    },
    { title: '负责人', dataIndex: 'owner_name', width: 100, render: (v: string | null) => v || '-' },
    {
      title: '最近跟进',
      dataIndex: 'last_follow_at',
      width: 150,
      render: (v: string | null) => fmtDateTime(v),
    },
  ];

  const longRunningColumns: ColumnsType<Lead> = [
    {
      title: '客户名称',
      dataIndex: 'name',
      width: 200,
      render: (v: string, r) => <a onClick={() => navigate(`/opportunities/${r.id}`)}>{v}</a>,
    },
    {
      title: '有效商机时间',
      dataIndex: 'valid_at',
      width: 140,
      render: (v: string | null) => fmtDate(v),
    },
    {
      title: '已推进天数',
      width: 120,
      render: (_, r) => (r.valid_at ? `${dayjs().diff(dayjs(r.valid_at), 'day')} 天` : '-'),
    },
    {
      title: '当前环节',
      dataIndex: 'current_stage_label',
      width: 160,
      render: (v: string, r) => v || r.current_stage || '-',
    },
    {
      title: '预算金额(万)',
      dataIndex: 'budget_amount_fen',
      width: 130,
      align: 'right',
      render: (v: number | null) => fenToWan(v),
    },
    { title: '负责人', dataIndex: 'owner_name', width: 100, render: (v: string | null) => v || '-' },
  ];

  const stuckTab = (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Card
        title={
          <Space>
            卡单清单
            <Tag color="red">{data?.stuck?.length ?? 0}</Tag>
          </Space>
        }
        extra={
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            当前环节停留超过基准即视为卡单（按自然日计时，基准来自环节标准卡配置）
          </Typography.Text>
        }
      >
        <Table<Lead>
          rowKey="id"
          size="small"
          loading={isLoading}
          dataSource={data?.stuck ?? []}
          columns={stuckColumns}
          locale={{
            emptyText: (
              <NextStepGuide
                compact
                title="暂无卡单"
                description="卡单口径 = 当前环节停留超过该环节基准天数（按自然日）。现在所有在途商机都在基准内。"
              />
            ),
          }}
          pagination={{ pageSize: STUCK_PAGE_SIZE, showTotal: (t) => `共 ${t} 条` }}
          onRow={(r) => ({
            onClick: () => navigate(`/opportunities/${r.id}`),
            style: { cursor: 'pointer' },
          })}
        />
      </Card>

      <Card
        title={
          <Space>
            接近 {data?.total_duration_warning_days ?? 90} 天的长周期项目
            <Tag color="orange">{data?.long_running?.length ?? 0}</Tag>
          </Space>
        }
        extra={
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            单项目接近总时长预警线（{data?.total_duration_warning_days ?? 90} 天）
          </Typography.Text>
        }
      >
        <Table<Lead>
          rowKey="id"
          size="small"
          loading={isLoading}
          dataSource={data?.long_running ?? []}
          columns={longRunningColumns}
          locale={{
            emptyText: (
              <NextStepGuide
                compact
                title="暂无长周期项目"
                description={`接近总时长预警线（${data?.total_duration_warning_days ?? 90} 天）的在途项目会出现在这里，用于提前安排节奏。`}
              />
            ),
          }}
          pagination={{ pageSize: STUCK_PAGE_SIZE, showTotal: (t) => `共 ${t} 条` }}
          onRow={(r) => ({
            onClick: () => navigate(`/opportunities/${r.id}`),
            style: { cursor: 'pointer' },
          })}
        />
      </Card>
    </Space>
  );

  /* ---------------- ③ 转化率 vs 基准 ---------------- */
  const [convRange, setConvRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [convOwner, setConvOwner] = useState<string | undefined>(undefined);

  const convParams = {
    start: convRange?.[0]?.format('YYYY-MM-DD'),
    end: convRange?.[1]?.format('YYYY-MM-DD'),
    owner_id: isManager ? convOwner : undefined,
  };

  const convQuery = useQuery({
    queryKey: ['dashboard-conversion', convParams],
    queryFn: () => fetchConversion(convParams),
  });

  const { data: staff } = useQuery({
    queryKey: ['dashboard-staff-options'],
    queryFn: fetchStaffOptions,
    enabled: isManager,
    retry: false,
  });

  const conv = convQuery.data;

  /** 实际 vs 基准的双条形对比（不引图表库） */
  const compareCell = (rate: number, baseline: number, alert: boolean) => (
    <Space direction="vertical" size={0} style={{ width: '100%', minWidth: 190 }}>
      <Progress
        percent={Math.min(Math.round(rate * 100), 100)}
        size="small"
        showInfo={false}
        strokeColor={alert ? DANGER : BRAND}
      />
      <Space size={6} style={{ fontSize: 12 }}>
        <Typography.Text type={alert ? 'danger' : undefined} style={{ fontSize: 12 }}>
          实际 {fmtPercent(rate)}
        </Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          基准 {fmtPercent(baseline)}
        </Typography.Text>
      </Space>
      <Progress
        percent={Math.min(Math.round(baseline * 100), 100)}
        size="small"
        showInfo={false}
        strokeColor={BORDER}
      />
    </Space>
  );

  const pairColumns: ColumnsType<ConversionPair> = [
    {
      title: '阶段转化',
      width: 220,
      render: (_, r) => (
        <Space size={4}>
          <span>{r.from_stage_label}</span>
          <Typography.Text type="secondary">→</Typography.Text>
          <Typography.Text strong>{r.to_stage_label}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '样本（分母 / 分子）',
      width: 150,
      align: 'right',
      render: (_, r) => `${r.from_count} / ${r.to_count}`,
    },
    {
      title: '实际 vs 基准',
      width: 260,
      render: (_, r) => compareCell(r.rate, r.baseline, r.alert),
    },
    {
      title: '预警线',
      dataIndex: 'warn_threshold',
      width: 110,
      align: 'right',
      render: (v: number) => fmtPercent(v),
    },
    {
      title: (
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          差距（百分点）
        </Typography.Text>
      ),
      dataIndex: 'gap',
      width: 150,
      align: 'right',
      render: (v: number) => (
        <Typography.Text type={v < 0 ? 'danger' : 'success'}>
          {v >= 0 ? '+' : ''}
          {(v * 100).toFixed(1)} 个百分点
        </Typography.Text>
      ),
    },
    {
      title: '状态',
      width: 110,
      render: (_, r) => (r.alert ? <Tag color="red">低于预警线</Tag> : <Tag color="green">正常</Tag>),
    },
  ];

  const personColumns: ColumnsType<ConversionPerson> = [
    { title: '负责人', dataIndex: 'user_name', width: 120 },
    { title: '有效商机数', dataIndex: 'valid_count', width: 110, align: 'right' },
    { title: '已推进数', dataIndex: 'advanced_count', width: 100, align: 'right' },
    {
      title: '推进率',
      dataIndex: 'rate',
      width: 220,
      render: (v: number, r) => compareCell(v, r.baseline, r.alert),
    },
    {
      title: '状态',
      width: 110,
      render: (_, r) => (r.alert ? <Tag color="red">低于基准</Tag> : <Tag color="green">正常</Tag>),
    },
  ];

  const conversionTab = (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message={`预警口径：实际转化率 < 基准 × ${conv?.warn_ratio ?? 0.7}（低于基准 ${fmtPercent(
          1 - (conv?.warn_ratio ?? 0.7),
          0,
        )} 以上）且分母 ≥ ${conv?.min_sample ?? 5} 条时触发转化率预警`}
        description="分母小于最小样本量时不报警，避免小样本误报；阈值可在 6.7 预警规则配置中调整。"
      />

      <Card
        size="small"
        title="筛选"
        extra={
          <Space wrap>
            <DatePicker.RangePicker
              value={convRange}
              onChange={(v) => setConvRange(v as [Dayjs, Dayjs] | null)}
              allowClear
            />
            {isManager && (
              <Select
                allowClear
                style={{ width: 180 }}
                placeholder="团队全部"
                value={convOwner}
                onChange={(v) => setConvOwner(v ?? undefined)}
                options={(staff?.items ?? []).map((s) => ({
                  value: s.user_id,
                  label: s.name,
                }))}
              />
            )}
            <Button
              icon={<ReloadOutlined />}
              loading={convQuery.isFetching}
              onClick={() => convQuery.refetch()}
            >
              刷新
            </Button>
          </Space>
        }
      >
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          统计口径：{conv?.owner_scope === 'team' ? '团队（全部市场人员）' : '本人'}
          {conv?.start || conv?.end ? `；区间 ${conv?.start ?? '不限'} ~ ${conv?.end ?? '不限'}` : '；不限时间'}
          {conv?.breached?.length ? `；已触发预警：${conv.breached.join('、')}` : '；当前无触发预警的阶段'}
        </Typography.Text>
      </Card>

      <Card size="small" title="阶段转化率 vs 基准">
        <Table<ConversionPair>
          rowKey="path"
          size="small"
          loading={convQuery.isLoading}
          dataSource={conv?.pairs ?? []}
          columns={pairColumns}
          pagination={false}
          rowClassName={(r) => (r.alert ? 'risk-row-alert' : '')}
          locale={{
            emptyText: (
              <NextStepGuide
                compact
                title="暂无转化率数据"
                description={`分母（起始环节商机数）小于最小样本量 ${conv?.min_sample ?? 5} 条时不计算，避免小样本误报；可放宽时间区间后再看。`}
              />
            ),
          }}
        />
      </Card>

      <Card size="small" title="按人推进率">
        <Table<ConversionPerson>
          rowKey="user_id"
          size="small"
          loading={convQuery.isLoading}
          dataSource={conv?.per_person ?? []}
          columns={personColumns}
          pagination={false}
          rowClassName={(r) => (r.alert ? 'risk-row-alert' : '')}
          locale={{
            emptyText: (
              <NextStepGuide compact title="暂无人员数据" description="所选区间内没有可统计的在途商机。" />
            ),
          }}
        />
      </Card>
    </Space>
  );

  const breached = data?.conversion?.breached?.length ?? 0;

  /* ---------------- 渲染 ---------------- */
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <style>{`.risk-row-alert > td { background: var(--danger-soft) !important; }`}</style>

      <PageHeader
        title="风险与卡单中心"
        subtitle="按严重度排序处理——先看超期与卡单，再看节奏不清；每条预警都能点进商机详情就地补证据、补时间。"
        demo
        tags={<Tag color="orange">待处理 {data?.counts?.pending ?? 0}</Tag>}
        actions={
          <Space wrap>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => {
                invalidateAll();
              }}
            >
              刷新
            </Button>
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              loading={doScan.isPending}
              onClick={() => doScan.mutate()}
            >
              手工触发扫描
            </Button>
          </Space>
        }
      />

      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}>
          <KpiCard
            label="待处理"
            value={data?.counts?.pending ?? 0}
            suffix="条"
            icon={<AlertOutlined />}
            tone={(data?.counts?.pending ?? 0) > 0 ? 'red' : 'green'}
            danger={(data?.counts?.pending ?? 0) > 0}
            hint="待处理 = 状态为「待处理」的预警条数，含商机侧（闭环二：卡单 / 总时长 / 节奏 / 转化率）与运营侧（闭环三：交付 / 验收 / 投诉 / 产品问题 / 承诺）两类；后端 /risks 单值 kind 参数无法一次排除某一侧，用「全部种类」下拉或「商机侧快捷筛选」按种类隔离。预警不作为考核依据"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="已超期"
            value={overdueCount}
            suffix="条"
            icon={<ClockCircleOutlined />}
            tone={overdueCount > 0 ? 'amber' : 'gray'}
            danger={overdueCount > 0}
            hint={`口径：当前列表内状态为「待处理 / 处理中」且创建超过 ${OVERDUE_DAYS} 天仍未解除的预警条数（与左表筛选联动，换筛选即换口径）`}
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="已闭环"
            value={resolvedQuery.data?.total ?? data?.counts?.resolved ?? 0}
            suffix="条"
            icon={<CheckCircleOutlined />}
            tone="green"
            hint="已闭环 = 状态为「已解除」的预警条数（处理后必须写处理说明，说明会留痕并可在商机详情回看）"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="转化率预警"
            value={breached}
            suffix="个环节"
            icon={<RiseOutlined />}
            tone={breached > 0 ? 'red' : 'green'}
            danger={breached > 0}
            hint="转化率预警 = 实际转化率低于「基准 × 预警比例」且分母不小于最小样本量的环节数（见「转化率」页签）"
          />
        </Col>
      </Row>

      <Card size="small">
        <Tabs
          items={[
            {
              key: 'alerts',
              label: `预警清单${data?.counts ? `（待处理 ${data.counts.pending} / 处理中 ${processingQuery.data?.total ?? data.counts.processing}）` : ''}`,
              children: alertTab,
            },
            { key: 'stuck', label: '卡单与长周期', children: stuckTab },
            { key: 'conversion', label: '转化率', children: conversionTab },
          ]}
        />
      </Card>

      {/* 处理预警弹窗：处理说明必填（未填时提交置灰并说明原因） */}
      <Modal
        open={!!handling}
        title="处理预警"
        okText="提交"
        cancelText="取消"
        confirmLoading={doHandle.isPending}
        okButtonProps={{ disabled: !noteFilled }}
        onCancel={() => {
          setHandling(null);
          setNoteFilled(false);
          handleForm.resetFields();
        }}
        onOk={async () => {
          const v = await handleForm.validateFields();
          if (!handling) return;
          if (!(v.handle_note ?? '').trim()) {
            message.error('必须填写处理说明，才能提交');
            return;
          }
          doHandle.mutate({ riskId: handling.id, status: v.status, note: v.handle_note ?? '' });
        }}
      >
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Space wrap>
            <Tag color={ALERT_KIND_COLOR[handling?.kind ?? ''] ?? 'default'}>
              {handling?.kind_label}
            </Tag>
            <Typography.Text strong>{handling?.trigger_rule}</Typography.Text>
          </Space>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            {handling?.description}
          </Typography.Paragraph>
          <a onClick={() => handling && navigate(`/opportunities/${handling.lead_id}`)}>
            查看商机详情{handling?.lead_name ? `（${handling.lead_name}）` : ''}
          </a>
          <Form form={handleForm} layout="vertical">
            <Form.Item
              label="处理状态"
              name="status"
              rules={[{ required: true, message: '请选择处理状态' }]}
            >
              <Select options={STATUS_OPTIONS} />
            </Form.Item>
            <Form.Item
              label="处理说明（必填）"
              name="handle_note"
              rules={[{ required: true, message: '必须填写处理说明' }]}
              extra="写清“这次做了什么、什么时候跟进”；说明会留痕并在商机详情可回看。"
            >
              <Input.TextArea
                rows={3}
                maxLength={200}
                showCount
                placeholder="如：已电话确认预算与时间，下一环节安排下周过会"
                onChange={(e) => setNoteFilled(!!e.target.value.trim())}
              />
            </Form.Item>
          </Form>
          {!noteFilled && (
            <Alert
              type="warning"
              showIcon
              message="「提交」暂不可用：还差处理说明"
              description="预警处理必须留下说明，否则别人无法判断这条预警是否真的处理过；填一行字即可提交。"
            />
          )}
        </Space>
      </Modal>

      {/* 批量处理弹窗：共用一段处理说明，成功/失败条数如实回报 */}
      <Modal
        open={batchOpen}
        title={`批量处理 ${batchIds.length} 条预警`}
        okText={`确认处理 ${batchIds.length} 条`}
        cancelText="取消"
        confirmLoading={doBatch.isPending}
        okButtonProps={{ disabled: !batchNoteFilled || batchIds.length === 0 }}
        onCancel={() => {
          setBatchOpen(false);
          setBatchNoteFilled(false);
          batchForm.resetFields();
        }}
        onOk={async () => {
          const v = await batchForm.validateFields();
          if (!(v.handle_note ?? '').trim()) {
            message.error('批量处理也必须填写处理说明');
            return;
          }
          if (!batchIds.length) {
            message.error('没有选中任何预警');
            return;
          }
          doBatch.mutate({ ids: batchIds, status: v.status, note: v.handle_note ?? '' });
        }}
      >
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message={`将把选中的 ${batchIds.length} 条预警统一改为同一状态，并写入同一段处理说明（逐条留痕）`}
            description={`已解除的预警不可批量处理（勾选框已禁用）；如某条预警的处理动作不同，请单独点该行的「处理」。每条预警的处理人 = 当前登录人、处理时间 = 现在，均逐条留痕。`}
          />
          <Form form={batchForm} layout="vertical">
            <Form.Item
              label="处理状态"
              name="status"
              rules={[{ required: true, message: '请选择处理状态' }]}
            >
              <Select options={STATUS_OPTIONS} />
            </Form.Item>
            <Form.Item
              label="处理说明（必填，本批共用）"
              name="handle_note"
              rules={[{ required: true, message: '必须填写处理说明' }]}
              extra="写清“这一批做了什么、下一批什么时候跟进”。说明会逐条留痕并可在商机详情回看。"
            >
              <Input.TextArea
                rows={3}
                maxLength={200}
                showCount
                placeholder="如：已电话确认预算与时间，本周统一安排过会"
                onChange={(e) => setBatchNoteFilled(!!e.target.value.trim())}
              />
            </Form.Item>
          </Form>
          {!batchNoteFilled && (
            <Alert
              type="warning"
              showIcon
              message="「确认处理」暂不可用：还差处理说明"
              description="批量处理同样必须留痕，否则别人无法判断这批预警是否真的处理过；填一行字即可提交。"
            />
          )}
        </Space>
      </Modal>

      <Footnote>
        「系统预警」与「业务风险」分开看：系统预警（卡单 / 转化率 / 总时长）由系统按 6.7 规则自动扫描生成，
        口径固定可复算；业务风险（R1–R5）由专员在商机详情「风险」Tab 登记（或确认 AI 草稿后落库），其中R5由下一节点及预计时间统一核查。
        筛选口径：状态 / 种类 / 只看我的走<b>服务端</b>（后端 /risks 支持 status、kind、mine）；类别 / 负责人是<b>仅对当前页</b>的前端筛选。
        R5统一按档案中的下一关键节点与预计时间核查，允许暂定、区间及再次确认安排；
        缺失或明确日期已过时提醒复核，不再使用旧日期字段、宽限天数或年度窗口触发。
        已签约停止销售阶段计时；交付未联系从双方交接确认后按有效运营记录计算。
        「已超期」= 未解除且预警创建超过 {OVERDUE_DAYS} 天。
        批量处理会逐条调用处理接口并共用同一段处理说明，成功/失败条数如实回报（后端无批量接口）。
        转化率「差距」一律用百分点表述（实际转化率 − 基准，相差 1% = 1 个百分点）。分母小于最小样本量不报警。
        全部预警与转化率数据用于过程管理，不作为考核依据。
      </Footnote>
      <NotForAssessment />
    </Space>
  );
}
