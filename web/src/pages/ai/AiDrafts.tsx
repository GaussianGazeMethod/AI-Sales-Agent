import { useEffect, useMemo, useState } from 'react';
import dayjs from 'dayjs';
import type { CSSProperties, ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
  DatePicker,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  ClockCircleOutlined,
  FileTextOutlined,
  LinkOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import {
  PARSE_MODE_COLOR,
  PARSE_MODE_LABEL,
  confirmDraft,
  confirmDraftsBatch,
  ignoreDraft,
  listDrafts,
  type AiDraft,
  type AiDraftOverride,
  type AiDraftPayload,
  type AiDraftStandard,
} from '@/api/ai';
import PageHeader from '@/components/PageHeader';
import LeadSelect from '@/components/LeadSelect';
import GateBanner from '@/components/GateBanner';
import { AiDisclaimer, Footnote, NextStepGuide, PanelFlag } from '@/components/Guide';
import { getOpportunityDetail } from '@/api/opportunities';
import { listLeads } from '@/api/leads';
import { describeError } from '@/api/client';
import { useDicts } from '@/hooks/useDicts';
import {
  fenToWan,
  fenToYuanNumber,
  fmtDateTime,
  toLabelMap,
  yuanToFen,
} from '@/utils/format';

/** 下一节点及预计时间的审查摘要，不把月份或区间强转成日期。 */
function scheduleOf(d: AiDraft): string | null {
  const plan = d.payload.schedule;
  return plan ? `${plan.next_node || '节点待确认'} · ${plan.next_node_time || '时间待确认'}` : null;
}

/** 后端把 resolution / warnings 放在 payload 里（前端类型未声明，页面内做只读扩展） */
type DraftPayloadMeta = AiDraftPayload & {
  resolution?: { source?: string; reason?: string; candidate_scope?: string } | null;
  warnings?: string[];
};

const STATUS_OPTIONS = [
  { value: 'pending', label: '待确认' },
  { value: 'confirmed', label: '已确认' },
  { value: 'ignored', label: '已忽略' },
];

const STATUS_TAG: Record<string, { color: string; label: string }> = {
  pending: { color: 'processing', label: '待确认' },
  confirmed: { color: 'success', label: '已确认' },
  ignored: { color: 'default', label: '已忽略' },
  failed: { color: 'error', label: '解析失败' },
};

const CHANNEL_LABEL: Record<string, string> = {
  chat: 'App 内发送',
  im: 'IM 消息（企业微信/钉钉）',
  im_agent: 'IM 归集',
  manual: 'PC 手工录入',
};

const PAGE_SIZE = 10;

/** 修正弹窗里的标准行（可删除 / 改证据） */
interface StandardRow extends AiDraftStandard {
  keep: boolean;
}

interface EditForm {
  followup_date?: import('dayjs').Dayjs | null;
  next_node?: string;
  next_node_time?: string;
  followup_method?: string;
  followup_content?: string;
  amount_yuan?: number;
  amount_reason?: string;
  remark?: string;
}

interface DiffRow {
  key: string;
  field: string;
  before: ReactNode;
  after: ReactNode;
  /**  该字段的更新依据（AI 为什么这么改）——设计稿 05 的「更新依据」 */
  reason: string;
  /** 能否逐条打开「修正」弹窗（金额 / 跟进 / 标准 / 风险 都可改） */
  editable?: boolean;
}

function truncate(text: string, max = 48): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

/* ══════════════════════════════════════════════════════════════════════
 * 更新草稿面板（5.1 右侧面板 与 5.2 详情抽屉**共用同一套呈现**）
 *
 * 判据对照：
 * - C4 赋能边界：可核对（字段级对照 + 标准证据 + **原始消息出处**）、
 *                可修正（修正弹窗复用 payload_override）、可忽略（忽略按钮）；
 *                面板常驻「未确认不生效」与 AI 免责标识，且不给任何命中率数字。
 * - C3 口径可见：更新依据（AI 为什么这么改）就地展示，没有依据就写"AI 依据消息原文抽取"。
 * - C2 少判断：未满足项给「未满足 / 保持未完成 / 下一步」；未定位客户给候选与直达。
 * ══════════════════════════════════════════════════════════════════════ */

export interface AiDraftPanelProps {
  draft: AiDraft;
  confirming?: boolean;
  ignoring?: boolean;
  /** 确认（可带字段级修正 payload_override） */
  onConfirm?: (body?: { payload_override?: AiDraftOverride; remark?: string }) => void;
  onIgnore?: () => void;
  /** 底部补充操作（如「再录一条」） */
  footerExtra?: ReactNode;
  /**  最近一次确认后**未生效**的原因（后端 notes）——必须显眼，不能只弹个 toast */
  lastNotes?: string[];
  style?: CSSProperties;
}

export function AiDraftPanel({
  draft,
  confirming,
  ignoring,
  onConfirm,
  onIgnore,
  footerExtra,
  lastNotes,
  style,
}: AiDraftPanelProps) {
  const navigate = useNavigate();
  const { dicts } = useDicts();
  const riskMap = toLabelMap(dicts.risk_types);
  const methodMap = toLabelMap(dicts.follow_methods);
  const stageMap = toLabelMap(dicts.stages);
  const methodOptions = (dicts.follow_methods ?? []).map((m) => ({ value: m.value, label: m.label }));
  const riskOptions = (dicts.risk_types ?? []).map((m) => ({ value: m.value, label: m.label }));

  const payload = draft.payload as DraftPayloadMeta;
  const standards = payload.standards ?? [];
  const warnings = payload.warnings ?? [];
  const stageLabel = payload.lead_stage ? stageMap[payload.lead_stage] ?? payload.lead_stage : '—';

  const [leadId, setLeadId] = useState<string | undefined>(draft.lead_id ?? undefined);
  const [editOpen, setEditOpen] = useState(false);
  const [standardsEdit, setStandardsEdit] = useState<StandardRow[]>([]);
  const [risksEdit, setRisksEdit] = useState<string[]>([]);
  /** 逐条修正：正在就地改证据的标准 id */
  const [evidenceFor, setEvidenceFor] = useState<string | null>(null);
  const [evidenceDraft, setEvidenceDraft] = useState('');
  const [form] = Form.useForm<EditForm>();

  useEffect(() => {
    setLeadId(draft.lead_id ?? undefined);
    setEvidenceFor(null);
  }, [draft.id, draft.lead_id]);

  /**
   * 本环节标准完成情况（同 2.2 商机详情的权威数据，复用查询缓存）。
   *
   *  为什么面板要看它：设计稿要求「资料不足时**保持标准未完成**」「确认不绕过证据与硬门槛」——
   * 只讲"AI 命中了几条"是不够的，必须让人看到**哪些标准仍未满足、下一步补什么证据**。
   */
  const { data: oppDetail, isError: oppError } = useQuery({
    queryKey: ['opportunity-detail', leadId],
    queryFn: () => getOpportunityDetail(leadId as string),
    enabled: !!leadId,
    staleTime: 30 * 1000,
  });
  const stageProgress = oppDetail?.stage_progress ?? null;

  /**
   * 候选客户「一键定位」：后端只给候选名（不含 lead_id），所以按**名称反查**可见线索。
   * 命中唯一 → 直接填入；命中多条 / 零条 → 如实说明，让人在下拉里选（不猜）。
   */
  const locate = useMutation({
    mutationFn: (name: string) => listLeads({ keyword: name, page_size: 10 }),
    onSuccess: (res, name) => {
      const hits = res.items ?? [];
      if (hits.length === 1) {
        setLeadId(hits[0].id);
        message.success(`已定位客户：${hits[0].name}`);
      } else if (hits.length > 1) {
        message.warning(`「${name}」匹配到 ${hits.length} 条，请在上方下拉里按城市/负责人选择`);
      } else {
        message.warning(`可见范围内没有「${name}」，请在上方下拉里搜索，或先到线索池建立该客户`);
      }
    },
    onError: (e) => message.error(describeError(e).detail),
  });

  /**  变更内容对比：只呈现**真实变化**（当前值 → 拟更新 + 该字段依据） */
  const diffRows = useMemo<DiffRow[]>(() => {
    const rows: DiffRow[] = [];
    const resolveReason = (fallback: string) => payload.resolution?.reason || fallback;
    const amount = payload.amount;
    if (amount && amount.amount_fen !== null && amount.amount_fen !== undefined) {
      if (amount.before_fen !== amount.amount_fen) {
        rows.push({
          key: 'amount',
          field: '预计金额',
          before:
            amount.before_fen === null || amount.before_fen === undefined
              ? '—'
              : `${fenToWan(amount.before_fen)} 万`,
          after: `${fenToWan(amount.amount_fen)} 万`,
          reason: amount.reason || resolveReason('AI 依据消息原文抽取（未给出金额变更依据）'),
          editable: true,
        });
      }
    }
    if (payload.followup?.content) {
      const method = methodMap[payload.followup.method ?? ''] ?? payload.followup.method ?? '跟进';
      rows.push({
        key: 'followup',
        field: '跟进记录',
        before: '—',
        after: `新增：${method}｜${payload.followup.followed_at?.slice(0, 10) ?? '确认时记录'}｜${truncate(payload.followup.content)}`,
        reason: resolveReason('AI 依据消息原文抽取（消息本身就是本次跟进记录）'),
        editable: true,
      });
    }
    if (payload.standards?.length) {
      const hard = payload.standards.filter((s) => s.is_hard_gate).length;
      rows.push({
        key: 'standards',
        field: '完成标准',
        before: '—',
        after: `勾选 ${payload.standards.length} 条${hard ? `（含硬门槛 ${hard} 条）` : ''}`,
        reason: resolveReason('依据消息里的原文描述匹配本环节标准；证据逐条列在「标准动作建议」'),
        editable: true,
      });
    }
    if (payload.risks?.length) {
      rows.push({
        key: 'risks',
        field: '风险',
        before: '无',
        after: payload.risks.map((r) => riskMap[r] ?? r).join('、'),
        reason: resolveReason('AI 从消息里的不确定性表述（如"预算没定""等校长定"）识别出风险'),
        editable: true,
      });
    }
    if (scheduleOf(draft)) {
      rows.push({
        key: 'schedule',
        field: '时间承诺',
        before: '—',
        after: `${scheduleOf(draft)}（下一节点及预计时间）`,
        reason: resolveReason('AI 从消息里的时间表述抽出的客户确认时间'),
      });
    }
    if (payload.advance_stage) {
      rows.push({
        key: 'stage',
        field: '环节推进',
        before: stageLabel,
        after: '推进到下一环节',
        reason: resolveReason('AI 判断本环节标准已满足，建议推进'),
      });
    }
    return rows;
  }, [payload, draft, methodMap, riskMap, stageLabel]);

  /** 未满足项：标准已匹配但**证据为空**（后端草稿不返回未匹配标准，故只按证据推断，不编造） */
  const unmet = useMemo(() => standards.filter((s) => !String(s.evidence ?? '').trim()), [standards]);

  /** 本环节**仍未完成**的标准（来自 2.2 权威数据；缺证据的也算未完成） */
  const stageUnmet = useMemo(
    () =>
      (stageProgress?.standards ?? []).filter(
        (it) => !it.completion?.is_done || !String(it.completion?.evidence ?? '').trim(),
      ),
    [stageProgress],
  );

  const reason =
    payload.amount?.reason ||
    payload.resolution?.reason ||
    'AI 依据消息原文抽取（模型未给出额外依据）';

  const conf = draft.match_confidence ?? 0;
  const matchPill =
    conf >= 0.9 ? <Tag color="success">匹配度高</Tag> : <Tag color="orange">待确认</Tag>;

  const openEdit = () => {
    setStandardsEdit(standards.map((s) => ({ ...s, keep: true })));
    setRisksEdit(payload.risks ?? []);
    form.setFieldsValue({
      followup_date: payload.followup?.followed_at ? dayjs(payload.followup.followed_at) : null,
      followup_method: payload.followup?.method,
      followup_content: payload.followup?.content ?? '',
      amount_yuan: fenToYuanNumber(payload.amount?.amount_fen ?? null),
      amount_reason: payload.amount?.reason ?? '',
      remark: '',
      next_node: payload.schedule?.next_node ?? '',
      next_node_time: payload.schedule?.next_node_time ?? '',
    });
    setEditOpen(true);
  };

  /**  逐条修正标准证据 → 直接按修正后的值确认（不必先开整页修正弹窗） */
  const submitEvidence = (row: AiDraftStandard) => {
    const next: AiDraftStandard[] = standards.map((s) =>
      s.standard_id === row.standard_id ? { ...s, evidence: evidenceDraft } : s,
    );
    onConfirm?.({ payload_override: { standards: next } });
    setEvidenceFor(null);
    setEvidenceDraft('');
  };

  /** 逐条「只保留部分风险」→ 按修正后的风险确认 */
  const submitRiskDrop = (risk: string) => {
    const next = (payload.risks ?? []).filter((r) => r !== risk);
    onConfirm?.({ payload_override: { risks: next } });
  };

  const submitEdit = async () => {
    const v = await form.validateFields();
    const override: AiDraftOverride = {};
    const schedule = { next_node: v.next_node?.trim() || null, next_node_time: v.next_node_time?.trim() || null };
    if (schedule.next_node !== (payload.schedule?.next_node ?? null)
      || schedule.next_node_time !== (payload.schedule?.next_node_time ?? null)) {
      override.schedule = schedule;
    }

    const nextFollowup = {
      method: v.followup_method || payload.followup?.method || 'wechat',
      content: v.followup_content ?? '',
      followed_at: v.followup_date ? (v.followup_date.format('YYYY-MM-DD') === payload.followup?.followed_at?.slice(0, 10) ? payload.followup.followed_at : v.followup_date.format('YYYY-MM-DD') + 'T00:00:00') : undefined,
    };
    if (
      (payload.followup?.content ?? '') !== nextFollowup.content ||
      (payload.followup?.method ?? '') !== nextFollowup.method ||
      payload.followup?.followed_at !== nextFollowup.followed_at
    ) {
      override.followup = nextFollowup;
    }

    const kept = standardsEdit
      .filter((s) => s.keep)
      .map((s) => ({
        standard_id: s.standard_id,
        content: s.content,
        evidence: s.evidence,
        is_hard_gate: s.is_hard_gate,
      }));
    const sig = (list: { standard_id: string; evidence?: string }[]) =>
      list.map((s) => `${s.standard_id}|${s.evidence ?? ''}`).sort().join(',');
    if (sig(kept) !== sig(standards)) override.standards = kept;

    const amountFen = yuanToFen(v.amount_yuan ?? null);
    if (
      amountFen !== undefined &&
      (amountFen !== (payload.amount?.amount_fen ?? null) ||
        (v.amount_reason ?? '') !== (payload.amount?.reason ?? ''))
    ) {
      override.amount = {
        amount_fen: amountFen,
        before_fen: payload.amount?.before_fen ?? null,
        reason: v.amount_reason || '人工修正',
      };
    }

    const riskSig = (list: string[]) => [...list].sort().join(',');
    if (riskSig(risksEdit) !== riskSig(payload.risks ?? [])) override.risks = risksEdit;

    // 客户改选：草稿没定位到客户、或人工改成别的客户
    if (leadId && leadId !== draft.lead_id) override.lead_id = leadId;

    onConfirm?.({ payload_override: override, remark: v.remark });
    setEditOpen(false);
    setStandardsEdit([]);
    setRisksEdit([]);
    form.resetFields();
  };

  return (
    <Space direction="vertical" size={10} style={{ width: '100%', ...style }}>
      {/* ── 面板标题：未确认不生效 + 生成时间 ── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <Space size={8} wrap>
          <Typography.Text strong style={{ fontSize: 16 }}>
            更新草稿
          </Typography.Text>
          {draft.status === 'pending' ? (
            <PanelFlag>未确认不生效</PanelFlag>
          ) : (
            <Tag color={STATUS_TAG[draft.status]?.color ?? 'default'}>
              {STATUS_TAG[draft.status]?.label ?? draft.status}
            </Tag>
          )}
          <Tag color={PARSE_MODE_COLOR[draft.parse_mode] ?? 'default'}>
            {PARSE_MODE_LABEL[draft.parse_mode] ?? draft.parse_mode}
          </Tag>
          {draft.model_used && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {draft.model_used}
            </Typography.Text>
          )}
        </Space>
        <Typography.Text type="secondary" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
          生成时间：{fmtDateTime(draft.created_at)}
        </Typography.Text>
      </div>

      {/* ── 客户信息：客户名 + 匹配度胶囊 + 可改选客户 ── */}
      <Card size="small" title="客户信息" styles={{ body: { paddingTop: 12 } }}>
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Space size={8} wrap style={{ width: '100%' }}>
            <LeadSelect
              value={leadId}
              onChange={setLeadId}
              allowClear={false}
              placeholder="未定位到客户，请先指定商机"
              style={{ minWidth: 280 }}
            />
            <Tooltip title={`匹配度 = AI 对「这条消息属于哪个客户」的置信度：${(conf * 100).toFixed(0)}%（≥90% 视为高）。定错了可以直接改选客户。`}>
              {matchPill}
            </Tooltip>
            {draft.lead_name && draft.lead_id && (
              <a onClick={() => navigate(`/opportunities/${draft.lead_id}`)}>查看商机</a>
            )}
          </Space>
          {!leadId && (
            <Alert
              type="warning"
              showIcon
              message="未定位到客户：请先指定客户，否则无法确认"
              description={
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    点下面的候选可**按名称反查**并填入客户；也可在上方下拉里直接搜索。未指定客户的草稿，后端会拒绝确认（草稿本身不会改任何数据）。
                  </Typography.Text>
                  {draft.match_candidates.length > 0 ? (
                    <Space wrap>
                      {draft.match_candidates.slice(0, 6).map((c) => (
                        <Tooltip
                          key={c.key}
                          title="AI 给出的候选（编号只对本次调用可见）；点击将按客户名在你有权限的线索里反查，命中唯一才自动填入。"
                        >
                          <Tag
                            color={locate.isPending ? 'default' : 'blue'}
                            style={{ cursor: 'pointer' }}
                            onClick={() => locate.mutate(c.name)}
                          >
                            {c.key} · {c.name}（{c.city || '—'}·{c.owner_name ?? '无归属'}·
                            {c.status_label}）
                          </Tag>
                        </Tooltip>
                      ))}
                    </Space>
                  ) : (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      AI 没给出候选（该账号可见范围内没有相近客户）——请手动搜索，或先到线索池建立该客户。
                    </Typography.Text>
                  )}
                  {payload.resolution?.candidate_scope && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      定位范围：{payload.resolution.candidate_scope}（有界取集，不代表全库）
                    </Typography.Text>
                  )}
                </Space>
              }
            />
          )}
        </Space>
      </Card>

      {/*  变更内容对比（字段级 diff：原值 / 拟更新 / 依据） */}
      <Card
        size="small"
        title="变更内容对比"
        extra={
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            逐条可修正
          </Typography.Text>
        }
      >
        {diffRows.length ? (
          <Table<DiffRow>
            rowKey="key"
            size="small"
            bordered
            pagination={false}
            dataSource={diffRows}
            columns={[
              {
                title: '字段', dataIndex: 'field', width: 76,
                render: (v, row) => <Space direction="vertical" size={4}>
                  <span>{v}</span>
                  {row.editable && draft.status === 'pending' && (
                    <Button size="small" type="link" style={{ padding: 0 }} disabled={!onConfirm} onClick={openEdit}>
                      修正
                    </Button>
                  )}
                </Space>,
              },
              {
                title: '当前值', dataIndex: 'before', width: 80,
                render: (v) => <Typography.Text type="secondary">{v}</Typography.Text>,
              },
              {
                title: '拟更新', dataIndex: 'after',
                render: (v, row) => <Space direction="vertical" size={8}>
                  <Typography.Text strong>{v}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    依据：{row.reason}
                  </Typography.Text>
                </Space>,
              },
            ]}
          />
        ) : (
          <NextStepGuide
            compact
            title="本条消息未识别到可写入的字段变更"
            description="AI 没有从这条消息里抽到金额、跟进记录、标准、风险或时间承诺。可以「忽略」它（不产生任何副作用），或点「修正」手动补齐字段后再确认。"
          />
        )}
      </Card>

      {/*  本环节标准完成情况：确认不绕过证据与硬门槛（设计稿 05 的「未满足 / 保持未完成」） */}
      {leadId && (
        <Card
          size="small"
          title="本环节标准完成情况"
          extra={
            <a onClick={() => navigate(`/opportunities/${leadId}`)}>去商机详情补证据</a>
          }
        >
          {oppError ? (
            <Alert
              type="warning"
              showIcon
              message="看不到该商机的环节标准（不在你的数据范围，或商机已删除）"
              description="这不影响确认草稿本身：AI 草稿的「完成标准」只按证据落库，缺证据的项后端会保留未完成。要看标准清单请联系该商机负责人。"
            />
          ) : !stageProgress ? (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              正在读取本环节标准…（读取不到时以「标准动作建议」与生效结果为准）
            </Typography.Text>
          ) : (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              {!stageProgress.hard_gate_passed && (
                <GateBanner
                  title={`硬门槛未过：即使确认草稿也不会推进环节（当前 ${stageProgress.stage_label || '—'}）`}
                  items={[
                    `缺失硬门槛：${(stageProgress.missing_hard_gates ?? []).join('；') || '见下方标准清单'}`,
                    '确认草稿只写入它自己抽到的字段；环节推进需要硬门槛完成标准**带证据**勾选后才能走 2.2 的推进。',
                  ]}
                  action={
                    <Button size="small" onClick={() => navigate(`/opportunities/${leadId}`)}>
                      去补证据并推进
                    </Button>
                  }
                />
              )}
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                已完成 {stageProgress.done_count}/{stageProgress.total_count} 条 · 本环节停留{' '}
                {stageProgress.stay_days} 天（基准 {stageProgress.base_days} 天）
                {stageProgress.overdue ? ' · 已超期' : ''}
                <br />
                口径：本卡片照录商机里**已勾选的标准与证据原文**；证据是否算「真执行」由 AI 诊断与项目评审判断，
                这里不做推断。
              </Typography.Text>
              {stageUnmet.length ? (
                <Table
                  rowKey={(r) => r.standard.id}
                  size="small"
                  pagination={false}
                  dataSource={stageUnmet}
                  columns={[
                    {
                      title: '未满足标准',
                      dataIndex: ['standard', 'content'],
                      render: (v: string, r) => (
                        <Space size={4} wrap>
                          {r.standard.is_hard_gate && <Tag color="red">硬门槛</Tag>}
                          <span>{v}</span>
                        </Space>
                      ),
                    },
                    {
                      title: '当前',
                      width: 96,
                      render: () => (
                        <Space size={4}>
                          <Tag color="red">未满足</Tag>
                          <Tag>保持未完成</Tag>
                        </Space>
                      ),
                    },
                    {
                      title: '证据 / 下一步',
                      width: 220,
                      render: (_, r) => (
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {r.completion?.is_done
                            ? '已勾选但证据为空 → 下一步：补证据（勾选必附证据）'
                            : `下一步：${r.standard.evidence_hint || '补充证据后手动勾选（如拜访纪要 / 聊天截图 / 文件）'}`}
                        </Typography.Text>
                      ),
                    },
                  ]}
                />
              ) : (
                <Alert
                  type="success"
                  showIcon
                  message="本环节标准已完成，且每条都带证据"
                  description="确认草稿不会重复勾选；若 AI 又抽到同一条标准，后端会按证据去重。"
                />
              )}
            </Space>
          )}
        </Card>
      )}

      {/* 更新依据 */}
      <Card size="small" title="更新依据">
        <div style={{ background: 'var(--bg-layout)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px' }}>
          <Typography.Text style={{ fontSize: 13 }}>{reason}</Typography.Text>
        </div>
      </Card>

      {/* 标准动作建议 + 相关证据（可逐条改证据后确认） */}
      <Card size="small" title="标准动作建议">
        {standards.length ? (
          <Table<AiDraftStandard>
            rowKey="standard_id"
            size="small"
            pagination={false}
            dataSource={standards}
            columns={[
              {
                title: '命中标准',
                dataIndex: 'content',
                render: (v: string, r) => (
                  <Space size={4} wrap>
                    {r.is_hard_gate && <Tag color="red">硬门槛</Tag>}
                    <span>{v}</span>
                  </Space>
                ),
              },
              {
                title: '相关证据',
                dataIndex: 'evidence',
                width: 240,
                render: (v: string | undefined, r) =>
                  String(v ?? '').trim() ? (
                    <Space direction="vertical" size={2} style={{ width: '100%' }}>
                      <Space size={4} wrap>
                        <LinkOutlined />
                        <Typography.Text style={{ fontSize: 12 }}>{truncate(String(v), 40)}</Typography.Text>
                      </Space>
                      {draft.status === 'pending' && onConfirm && (
                        <Button
                          size="small"
                          type="link"
                          style={{ padding: 0 }}
                          onClick={() => {
                            setEvidenceFor(r.standard_id);
                            setEvidenceDraft(String(v ?? ''));
                          }}
                        >
                          改这条证据
                        </Button>
                      )}
                    </Space>
                  ) : (
                    <Tag color="red">缺证据（确认后保持未完成）</Tag>
                  ),
              },
            ]}
          />
        ) : (
          <Typography.Text type="secondary">本条草稿未匹配到当前环节的标准动作。</Typography.Text>
        )}
        {evidenceFor && (
          <Space direction="vertical" size={6} style={{ width: '100%', marginTop: 8 }}>
            <Input.TextArea
              rows={2}
              value={evidenceDraft}
              placeholder="证据要写清「时间 + 对象 + 交付物」，例：2026-10-10 上门拜访王主任，提交 AI 课堂方案 PPT"
              onChange={(e) => setEvidenceDraft(e.target.value)}
            />
            <Space>
              <Button
                type="primary"
                size="small"
                loading={confirming}
                disabled={!evidenceDraft.trim()}
                onClick={() => {
                  const row = standards.find((s) => s.standard_id === evidenceFor);
                  if (row) submitEvidence(row);
                }}
              >
                按修正后的证据确认
              </Button>
              <Button size="small" onClick={() => setEvidenceFor(null)}>
                取消
              </Button>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                勾选必附证据：证据为空时后端会保留该标准未完成。
              </Typography.Text>
            </Space>
          </Space>
        )}
      </Card>

      {/* 风险建议（可逐条摘掉后确认） */}
      {(payload.risks ?? []).length > 0 && (
        <Card size="small" title="风险建议">
          <Space direction="vertical" size={6} style={{ width: '100%' }}>
            {(payload.risks ?? []).map((r) => (
              <Space key={r} size={8} wrap>
                <Tag color="volcano">{r}</Tag>
                <Typography.Text style={{ fontSize: 13 }}>{riskMap[r] ?? r}</Typography.Text>
                {draft.status === 'pending' && onConfirm && (
                  <Popconfirm
                    title="摘掉这条风险后确认？"
                    description="该风险不会写入商机风险列表（其它字段照常落库）。"
                    onConfirm={() => submitRiskDrop(r)}
                  >
                    <Button size="small" type="link" style={{ padding: 0 }}>
                      不登记这条
                    </Button>
                  </Popconfirm>
                )}
              </Space>
            ))}
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              风险代码口径：R1 资金预算不清 / R2 信任不到 / R3 决策链不清 / R4 需求不清 / R5 项目节奏不清 / R6 产品价值无法匹配客户需求。
            </Typography.Text>
          </Space>
        </Card>
      )}

      {/*  上次确认后未生效的原因（后端 notes）：显眼、可照做 */}
      {(lastNotes ?? []).length > 0 && (
        <Alert
          type="warning"
          showIcon
          icon={<WarningOutlined />}
          message={`已确认，但有 ${lastNotes?.length} 项未能生效（未生效 ≠ 已生效）`}
          description={
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              {(lastNotes ?? []).map((n, i) => (
                <Typography.Text key={i} style={{ fontSize: 12 }}>
                  · {n}
                </Typography.Text>
              ))}
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                怎么办：按上面的原因补齐（多为缺证据 / 硬门槛未过 / 与现有数据冲突）后，到商机详情手动勾选或推进；
                草稿确认**不会绕过证据与硬门槛**。
              </Typography.Text>
            </Space>
          }
        />
      )}

      {/* 未满足项 + 解析提示（没有就不显示，不编） */}
      {(unmet.length > 0 || warnings.length > 0) && (
        <Card size="small" title="未满足项 / 解析提示">
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            {unmet.map((s) => (
              <Alert
                key={s.standard_id}
                type="warning"
                showIcon
                message={
                  <Space size={6} wrap>
                    <Typography.Text strong>{truncate(s.content, 28)}</Typography.Text>
                    <Tag color="red">缺证据</Tag>
                    <Tag>保持未完成</Tag>
                  </Space>
                }
                description="下一步：补充证据（拜访纪要 / 聊天截图 / 文件）后再手动勾选；勾选必附证据是硬性要求，确认草稿不会替它打勾。"
              />
            ))}
            {warnings.map((w, i) => (
              <Typography.Text key={`w-${i}`} type="warning" style={{ fontSize: 12 }}>
                · 解析提示：{w}
              </Typography.Text>
            ))}
          </Space>
        </Card>
      )}

      {/* 当前阶段 */}
      <Card size="small" title="当前阶段">
        <Space direction="vertical" size={4}>
          <Space size={6}>
            <Tag color="blue">{stageLabel}</Tag>
            <Tag>（保持不变）</Tag>
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            草稿确认不绕过证据与环节硬门槛：确认只写入草稿自己抽取的字段（跟进 / 标准 / 金额 / 风险 / 时间承诺），
            **不会自动推进环节**；硬门槛未带证据完成时，环节推进仍需在商机详情里手动走（并会被拦下）。
          </Typography.Text>
        </Space>
      </Card>

      {/*  原始消息出处（provenance）——可核对的关键 */}
      <Collapse
        size="small"
        items={[
          {
            key: 'source',
            label: '查看原始消息内容',
            /** 常驻渲染（折叠时只是收起）——便于静态渲染/无障碍读取，不影响用户折叠体验 */
            forceRender: true,
            children: (
              <Space direction="vertical" size={6} style={{ width: '100%' }}>
                <Typography.Paragraph style={{ margin: 0, fontSize: 13, whiteSpace: 'pre-wrap' }}>
                  {draft.source_message || '（无原文）'}
                </Typography.Paragraph>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  通道：{CHANNEL_LABEL[draft.channel] ?? draft.channel ?? '-'} · 生成时间：
                  {fmtDateTime(draft.created_at)} · 发起人：{draft.created_by_name ?? '-'}
                  {payload.resolution?.source ? ` · 客户定位方式：${payload.resolution.source}` : ''}
                </Typography.Text>
              </Space>
            ),
          },
        ]}
      />

      {/* 生效结果（已确认/已忽略的草稿回看） */}
      {draft.status !== 'pending' && (
        <Card size="small" title="生效结果">
          <Space direction="vertical" size={4} style={{ width: '100%' }}>
            <Typography.Text style={{ fontSize: 12 }}>
              处理人/时间：{draft.confirmed_by_name ? `${draft.confirmed_by_name} · ${fmtDateTime(draft.confirmed_at)}` : '-'}
            </Typography.Text>
            {draft.apply_result && (
              <Typography.Text style={{ fontSize: 12 }}>
                跟进：{draft.apply_result.applied.followup ? '已写入' : '无'}；标准{' '}
                {draft.apply_result.applied.standards.length} 条；金额：
                {draft.apply_result.applied.amount ? '已更新' : '无'}；风险{' '}
                {draft.apply_result.applied.risks.length} 条；环节：
                {draft.apply_result.applied.stage || '未推进'}
              </Typography.Text>
            )}
            {(draft.apply_result?.notes ?? []).map((n, i) => (
              <Typography.Text key={i} type="warning" style={{ fontSize: 12 }}>
                · {n}
              </Typography.Text>
            ))}
            {draft.status === 'ignored' && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                该草稿已被忽略，未修改任何业务数据。
              </Typography.Text>
            )}
          </Space>
        </Card>
      )}

      {/* 底部操作：忽略 / 修正 / 确认并写入 */}
      {draft.status === 'pending' && (
        <Space wrap>
          <Popconfirm title="忽略这条草稿？" description="忽略不会修改任何业务数据。" onConfirm={onIgnore}>
            <Button danger loading={ignoring} disabled={!onIgnore}>
              忽略
            </Button>
          </Popconfirm>
          <Button onClick={openEdit} disabled={!onConfirm}>
            修正
          </Button>
          <Tooltip title={!leadId ? '未定位到客户：请先在上方「客户信息」改选客户，再确认' : ''}>
            <Button
              type="primary"
              loading={confirming}
              disabled={!leadId || !onConfirm}
              onClick={() => onConfirm?.({ payload_override: leadId !== draft.lead_id ? { lead_id: leadId } : undefined })}
            >
              确认并写入
            </Button>
          </Tooltip>
          {footerExtra}
          <AiDisclaimer />
        </Space>
      )}

      {/* 修正弹窗：复用既有 payload_override（可改字段后确认） */}
      <Modal
        open={editOpen}
        title="修正后确认"
        width={760}
        okText="确认并写入"
        cancelText="取消"
        confirmLoading={confirming}
        onOk={submitEdit}
        onCancel={() => {
          setEditOpen(false);
          setStandardsEdit([]);
          form.resetFields();
        }}
      >
        <Form form={form} layout="vertical">
          <Form.Item label="下一关键节点" name="next_node"><Input placeholder="如：与客户确认汇报时间" /></Form.Item>
          <Form.Item label="预计时间" name="next_node_time" help="允许暂定、月份或区间；未知保持待确认，不凑具体日。"><Input placeholder="如下周一再确认、11月上旬（暂定）" /></Form.Item>
          <Form.Item label="实际跟进日期" name="followup_date" help="历史回填请核对日期；只提供日期时按当天00:00记录。">
            <DatePicker allowClear />
          </Form.Item>
          <Form.Item label="跟进方式" name="followup_method">
            <Select allowClear placeholder="选择跟进方式" options={methodOptions} />
          </Form.Item>
          <Form.Item label="跟进内容" name="followup_content">
            <Input.TextArea rows={3} placeholder="AI 生成的跟进记录，可修改" />
          </Form.Item>

          <Form.Item label="完成标准（取消勾选即删除该条；勾选必附证据）">
            <Table<StandardRow>
              rowKey="standard_id"
              size="small"
              pagination={false}
              dataSource={standardsEdit}
              locale={{ emptyText: <Empty description="无标准" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
              columns={[
                {
                  title: '保留',
                  width: 60,
                  render: (_, r) => (
                    <Checkbox
                      checked={r.keep}
                      onChange={(e) =>
                        setStandardsEdit((list) =>
                          list.map((x) => (x.standard_id === r.standard_id ? { ...x, keep: e.target.checked } : x)),
                        )
                      }
                    />
                  ),
                },
                {
                  title: '标准',
                  dataIndex: 'content',
                  render: (v: string, r) => (
                    <Space size={4} wrap>
                      {r.is_hard_gate && <Tag color="red">硬门槛</Tag>}
                      <span>{v}</span>
                    </Space>
                  ),
                },
                {
                  title: '证据（勾选必填）',
                  dataIndex: 'evidence',
                  width: 260,
                  render: (v: string | undefined, r) => (
                    <Input
                      size="small"
                      value={v}
                      placeholder="时间+对象 / 文件 / 截图"
                      onChange={(e) =>
                        setStandardsEdit((list) =>
                          list.map((x) => (x.standard_id === r.standard_id ? { ...x, evidence: e.target.value } : x)),
                        )
                      }
                    />
                  ),
                },
              ]}
            />
          </Form.Item>

          <Space style={{ display: 'flex' }} align="start">
            <Form.Item label="修正金额（元）" name="amount_yuan">
              <InputNumber min={0} precision={2} style={{ width: 180 }} placeholder="如 300000" />
            </Form.Item>
            <Form.Item label="变更依据（金额必填）" name="amount_reason" style={{ flex: 1 }}>
              <Input style={{ width: 380 }} placeholder="如：客户明确本期预算 30 万" />
            </Form.Item>
          </Space>

          <Form.Item
            label="登记哪些风险（取消勾选 = 不登记该风险）"
            help="风险代码口径：R1 资金预算不清 / R2 信任不到 / R3 决策链不清 / R4 需求不清 / R5 项目节奏不清 / R6 产品价值无法匹配客户需求。"
          >
            <Checkbox.Group
              value={risksEdit}
              onChange={(v) => setRisksEdit(v as string[])}
              options={riskOptions}
            />
          </Form.Item>

          <Form.Item label="确认备注" name="remark">
            <Input placeholder="选填，写入操作留痕" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}

/* ══════════════════════════════════════════════════════════════════════
 * 5.2 我的待确认：AI 草稿逐条确认 / 修正后确认 / 忽略 + 批量确认
 * ══════════════════════════════════════════════════════════════════════ */

export default function AiDrafts() {
  const navigate = useNavigate();
  const location = useLocation();
  const qc = useQueryClient();

  const [status, setStatus] = useState('pending');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string[]>([]);
  const [viewing, setViewing] = useState<AiDraft | null>(null);
  /** 最近一次确认后后端返回的**未生效原因**（在详情面板里显眼呈现） */
  const [lastNotes, setLastNotes] = useState<string[]>([]);
  /** 批量确认里失败的条目：逐条给出「为什么 + 怎么办」 */
  const [batchFailed, setBatchFailed] = useState<{ id: string; message: string }[]>([]);

  /** 5.1 生成草稿后跳过来的 draftId：直接打开该草稿的确认抽屉（少切换） */
  const presetDraftId = (location.state as { draftId?: string } | null)?.draftId;

  const queryKey = ['ai-drafts', { status, page, page_size: PAGE_SIZE }] as const;
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey,
    queryFn: () => listDrafts({ status, page, page_size: PAGE_SIZE }),
  });

  const items = data?.items ?? [];
  const listErr = describeError(error);

  /** 确认/忽略后刷新草稿列表、工作台、商机与线索 */
  const refreshAll = () => {
    qc.invalidateQueries({ queryKey: ['ai-drafts'] });
    qc.invalidateQueries({ queryKey: ['workbench'] });
    qc.invalidateQueries({ queryKey: ['opportunities'] });
    qc.invalidateQueries({ queryKey: ['opportunity-detail'] });
    qc.invalidateQueries({ queryKey: ['leads'] });
  };

  const doConfirm = useMutation({
    mutationFn: (v: { id: string; body?: { payload_override?: AiDraftOverride; remark?: string } }) =>
      confirmDraft(v.id, v.body),
    onSuccess: (r) => {
      const notes = r.notes ?? [];
      // ⭐ 未生效原因留痕：不再只弹 toast（会被刷掉），改为在详情面板里常驻展示
      setLastNotes(notes);
      if (notes.length) {
        message.warning(`草稿已确认，但有 ${notes.length} 项未生效（原因见右侧说明）`);
      } else {
        message.success('草稿已确认并生效');
      }
      setSelected((s) => s.filter((id) => id !== r.draft.id));
      setViewing(r.draft);
      refreshAll();
    },
    onError: (e) => {
      const { status: code, detail } = describeError(e);
      setLastNotes([code === 409 ? `被拒绝：${detail}` : detail]);
      message.error(detail);
    },
  });

  const doIgnore = useMutation({
    mutationFn: (id: string) => ignoreDraft(id),
    onSuccess: () => {
      message.success('草稿已忽略（未修改任何业务数据，可放心忽略）');
      setViewing(null);
      setLastNotes([]);
      refreshAll();
    },
    onError: (e) => message.error(describeError(e).detail),
  });

  const doBatch = useMutation({
    mutationFn: (ids: string[]) => confirmDraftsBatch(ids),
    onSuccess: (r) => {
      const failed = r.failed ?? [];
      setBatchFailed(failed);
      if (failed.length) {
        message.warning(`批量确认：成功 ${r.confirmed} 条，失败 ${failed.length} 条（原因见上方提示）`);
      } else {
        message.success(`批量确认完成：${r.confirmed} 条已生效`);
      }
      setSelected([]);
      refreshAll();
    },
    onError: (e) => message.error(describeError(e).detail),
  });

  const pendingItems = useMemo(() => items.filter((i) => i.status === 'pending'), [items]);

  /** 从 5.1 带 draftId 跳进来：定位到该草稿并打开（草稿可能不在第 1 页，故按 id 拉详情） */
  useEffect(() => {
    if (!presetDraftId) return;
    const hit = items.find((i) => i.id === presetDraftId);
    if (hit && !viewing) setViewing(hit);
  }, [presetDraftId, items, viewing]);

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <PageHeader
        title="我的待确认"
        subtitle="核对 AI 草稿的字段级变更（原值 / 拟更新 / 依据）与原始消息出处，确认后才写入业务数据。"
        demo
        tags={<AiDisclaimer />}
        meta={[
          { label: '当前筛选', value: STATUS_OPTIONS.find((s) => s.value === status)?.label ?? status },
          { label: '共', value: `${data?.total ?? 0} 条` },
          { label: '口径', value: '默认只看本人草稿；AI 只出草稿，人确认才生效' },
        ]}
        actions={
          <Space>
            <Segmented
              value={status}
              options={STATUS_OPTIONS}
              onChange={(v) => {
                setStatus(String(v));
                setPage(1);
                setSelected([]);
                setLastNotes([]);
                setBatchFailed([]);
              }}
            />
            {status === 'pending' && (
              <Popconfirm
                title={`确认选中的 ${selected.length} 条草稿？`}
                description="批量确认不修正字段，直接按 AI 抽取结果落库；缺证据的标准会保持未完成。需要改字段请逐条打开「查看草稿并确认 → 修正」。"
                disabled={!selected.length}
                onConfirm={() => doBatch.mutate(selected)}
              >
                <Button type="primary" disabled={!selected.length} loading={doBatch.isPending}>
                  批量确认（{selected.length}）
                </Button>
              </Popconfirm>
            )}
          </Space>
        }
      />

      <Card>
        {/*  未确认不生效：设计稿 05 的橙色角标语义，这里升级成一眼可见的行内提示 */}
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 10 }}
          message={
            <Space size={8} wrap>
              <PanelFlag>未确认不生效</PanelFlag>
              <Typography.Text strong style={{ fontSize: 13 }}>
                下面的草稿都没有改过任何业务数据
              </Typography.Text>
            </Space>
          }
          description={
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              确认时才一次性落库：跟进记录 / 完成标准（**勾选必附证据**）/ 金额修正（必填变更依据）/ 风险登记 / 时间承诺。
              确认**不绕过**证据与环节硬门槛：缺证据的标准保持未完成，环节也不会因此自动推进。忽略则不产生任何副作用。
            </Typography.Text>
          }
        />

        {/* 批量确认失败：逐条说清为什么 + 怎么办 */}
        {batchFailed.length > 0 && (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 10 }}
            closable
            onClose={() => setBatchFailed([])}
            message={`批量确认有 ${batchFailed.length} 条未生效（成功的不受影响）`}
            description={
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                {batchFailed.map((f) => {
                  const row = items.find((i) => i.id === f.id);
                  return (
                    <Typography.Text key={f.id} style={{ fontSize: 12 }}>
                      · {row?.lead_name || f.id.slice(0, 8)}：{f.message}
                    </Typography.Text>
                  );
                })}
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  怎么办：多为「缺证据 / 硬门槛未过 / 未定位客户」。点对应条目的「查看草稿并确认」——补证据或指定客户后单独确认；
                  环节推进仍需到商机详情按硬门槛走。
                </Typography.Text>
              </Space>
            }
          />
        )}

        <Space size={12} wrap style={{ marginBottom: 8 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            逐条可「查看草稿并确认 → 修正」；也可直接确认或忽略。
          </Typography.Text>
          {status === 'pending' && pendingItems.length > 0 && (
            <Checkbox
              checked={selected.length === pendingItems.length && pendingItems.length > 0}
              indeterminate={selected.length > 0 && selected.length < pendingItems.length}
              onChange={(e) => setSelected(e.target.checked ? pendingItems.map((i) => i.id) : [])}
            >
              全选本页（仅本页 {pendingItems.length} 条待确认）
            </Checkbox>
          )}
          <Button size="small" onClick={() => refetch()} loading={isFetching}>
            刷新
          </Button>
        </Space>

        {isLoading ? (
          <Spin />
        ) : isError ? (
          <Alert
            type="error"
            showIcon
            message="草稿列表加载失败"
            description={
              <Space direction="vertical" size={4}>
                <Typography.Text style={{ fontSize: 12 }}>
                  原因：{listErr.detail}
                  {listErr.status ? `（HTTP ${listErr.status}）` : ''}
                </Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  怎么办：点「重试」再拉一次；若持续失败，多为登录态过期或后端不可用（草稿仍安全保存在服务端，不会丢）。
                </Typography.Text>
                <Button size="small" type="primary" onClick={() => refetch()}>
                  重试
                </Button>
              </Space>
            }
          />
        ) : (
          <List
            dataSource={items}
            locale={{
              emptyText: (
                <NextStepGuide
                  title={status === 'pending' ? '暂无待确认草稿' : '该筛选下暂无草稿'}
                  description="在 5.1 对话与录入里发一段拜访/沟通内容（对象、需求、预算、时间、卡点），AI 会生成更新草稿并进入这里。"
                  actions={
                    <Space>
                      <Button type="primary" onClick={() => navigate('/ai/chat')}>
                        去 5.1 发消息生成草稿
                      </Button>
                      <Button onClick={() => navigate('/ai/diagnoses')}>看历史诊断</Button>
                    </Space>
                  }
                />
              ),
            }}
            pagination={{
              current: page,
              pageSize: PAGE_SIZE,
              total: data?.total ?? 0,
              onChange: (p) => setPage(p),
              showTotal: (t) => `共 ${t} 条`,
            }}
            renderItem={(d) => (
              <List.Item key={d.id} style={{ paddingInline: 0 }}>
                <Card
                  size="small"
                  style={{ width: '100%' }}
                  title={
                    <Space size={8} wrap>
                      {d.status === 'pending' && (
                        <Checkbox
                          checked={selected.includes(d.id)}
                          onChange={(e) =>
                            setSelected((s) => (e.target.checked ? [...s, d.id] : s.filter((x) => x !== d.id)))
                          }
                        />
                      )}
                      <Typography.Text strong>{d.lead_name || '未定位客户'}</Typography.Text>
                      <Tag color={STATUS_TAG[d.status]?.color ?? 'default'}>
                        {STATUS_TAG[d.status]?.label ?? d.status}
                      </Tag>
                      <Tag color={PARSE_MODE_COLOR[d.parse_mode] ?? 'default'}>
                        {PARSE_MODE_LABEL[d.parse_mode] ?? d.parse_mode}
                      </Tag>
                      {scheduleOf(d) && (
                        <Tooltip title="AI 从消息里抽出的客户确认时间；确认后写入该商机的「本环节预计完成时间」，用于解除 R5 项目节奏不清 / R6 产品价值无法匹配客户需求。">
                          <Tag icon={<ClockCircleOutlined />} color="green">
                            时间承诺 {scheduleOf(d)}
                          </Tag>
                        </Tooltip>
                      )}
                      {!d.lead_id && <Tag color="red">未定位到客户</Tag>}
                    </Space>
                  }
                  extra={
                    <Space size={8}>
                      {d.status === 'pending' && <PanelFlag>未确认不生效</PanelFlag>}
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {fmtDateTime(d.created_at)}
                      </Typography.Text>
                    </Space>
                  }
                >
                  <Typography.Paragraph type="secondary" style={{ fontSize: 13, marginBottom: 8 }} ellipsis={{ rows: 2, expandable: true, symbol: '展开' }}>
                    {d.summary}
                  </Typography.Paragraph>
                  {/* 字段级预览：不打开抽屉也能先看「要改什么」（原值 → 拟更新） */}
                  <Space size={6} wrap style={{ marginBottom: 6 }}>
                    <DraftChangeChips draft={d} />
                  </Space>
                  <Descriptions size="small" column={1} colon={false}>
                    <Descriptions.Item label={<FileTextOutlined />}>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        来源原文：{truncate(d.source_message, 60)}
                      </Typography.Text>
                    </Descriptions.Item>
                  </Descriptions>

                  <Space style={{ marginTop: 8 }} wrap>
                    <Button type="primary" onClick={() => setViewing(d)}>
                      查看草稿{ d.status === 'pending' ? '并确认' : '' }
                    </Button>
                    {d.status === 'pending' && (
                      <>
                        <Popconfirm
                          title="直接确认这条草稿？"
                          description="将按 AI 抽取结果落库（不改字段）。需要改字段请点「查看草稿并确认 → 修正」。缺证据的标准会保持未完成。"
                          onConfirm={() => doConfirm.mutate({ id: d.id })}
                          disabled={!d.lead_id}
                        >
                          <Button disabled={!d.lead_id}>直接确认</Button>
                        </Popconfirm>
                        {!d.lead_id && (
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                             「直接确认」不可用：未定位到客户 —— 请打开草稿，在「客户信息」里指定客户后再确认（草稿本身不会改任何数据）。
                          </Typography.Text>
                        )}
                        <Popconfirm
                          title="忽略这条草稿？"
                          description="忽略不会修改任何业务数据，也不会产生待办。"
                          onConfirm={() => doIgnore.mutate(d.id)}
                        >
                          <Button danger>忽略</Button>
                        </Popconfirm>
                      </>
                    )}
                  </Space>
                </Card>
              </List.Item>
            )}
          />
        )}
      </Card>

      {/* 详情：与 5.1 右侧面板同一套呈现 */}
      <Drawer
        open={!!viewing}
        width={760}
        title="更新草稿"
        onClose={() => {
          setViewing(null);
          setLastNotes([]);
        }}
        styles={{ body: { background: 'var(--bg-layout)' } }}
      >
        {viewing && (
          <AiDraftPanel
            draft={viewing}
            confirming={doConfirm.isPending}
            ignoring={doIgnore.isPending}
            lastNotes={lastNotes}
            onConfirm={(body) => doConfirm.mutate({ id: viewing.id, body })}
            onIgnore={() => doIgnore.mutate(viewing.id)}
          />
        )}
      </Drawer>

      <Footnote>
        ⓘ 未确认的 AI 草稿不会修改任何业务数据；确认时如硬门槛证据缺失，后端会保留该项未完成并在「生效结果」里逐条说明原因。
        忽略草稿不产生任何副作用。AI 结论与建议不作为考核依据。
      </Footnote>
    </Space>
  );
}

/**
 * 列表行内的「要改什么」预览：只列字段名与关键值，点开抽屉看完整三栏对照。
 * 目的是**不打开详情也能扫一眼**（少切换）；不做编辑，编辑仍在抽屉里。
 */
function DraftChangeChips({ draft }: { draft: AiDraft }) {
  const payload = draft.payload as DraftPayloadMeta;
  const chips: string[] = [];
  if (payload.amount && payload.amount.amount_fen != null) {
    const before =
      payload.amount.before_fen == null ? '—' : `${fenToWan(payload.amount.before_fen)} 万`;
    chips.push(`预计金额 ${before} → ${fenToWan(payload.amount.amount_fen)} 万`);
  }
  if (payload.followup?.content) chips.push('新增跟进记录');
  if (payload.standards?.length) chips.push(`勾选完成标准 ${payload.standards.length} 条`);
  if (payload.risks?.length) chips.push(`新增风险 ${payload.risks.length} 条`);
  if (scheduleOf(draft)) chips.push(`时间承诺 ${scheduleOf(draft)}`);
  if (!chips.length) return <Tag>未识别到字段变更（可忽略或修正）</Tag>;
  return (
    <>
      {chips.map((c) => (
        <Tag key={c} color="blue">
          {c}
        </Tag>
      ))}
    </>
  );
}
