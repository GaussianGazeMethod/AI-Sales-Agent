import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import type { AxiosError } from 'axios';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Collapse,
  Descriptions,
  Divider,
  Drawer,
  Dropdown,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Progress,
  Row,
  Select,
  Skeleton,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  CheckCircleFilled,
  CheckOutlined,
  CloseCircleFilled,
  ExclamationCircleFilled,
  FileTextOutlined,
  FormOutlined,
  FundOutlined,
  MoreOutlined,
  PaperClipOutlined,
  RobotOutlined,
  ThunderboltOutlined,
  WalletOutlined,
  WarningFilled,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { aiDiagnose, listDiagnoses, PARSE_MODE_COLOR, PARSE_MODE_LABEL, type AiDiagnosis } from '@/api/ai';
import { assistant } from '@/api/assistant';
import {
  addRisks,
  advanceStage,
  autoJudgeStandards,
  confirmStageCandidates,
  changeAmount,
  getOpportunityDetail,
  getStageProgressDetail,
  getStandardJudgement,
  rollbackStage,
  toggleStandard,
  updateRisk,
} from '@/api/opportunities';
import ProjectDossierDrawer from '@/components/ProjectDossierDrawer';
import './OpportunityDetail.css';
import { getCustomerOfLead } from '@/api/customers';
import AiContent from '@/components/AiContent';
import CompletedStandardReview from '@/components/CompletedStandardReview';
import SignOpportunity from '@/components/SignOpportunity';
import GateBanner from '@/components/GateBanner';
import KpiCard from '@/components/KpiCard';
import PageHeader from '@/components/PageHeader';
import { AiDisclaimer, Footnote, NextStepGuide } from '@/components/Guide';
import { LeadStatusTag } from '@/components/tags';
import { useDicts } from '@/hooks/useDicts';
import { listReviews } from '@/api/reports';
import { loseOpportunity, revertLoss } from '@/api/risks';
import { useAuthStore } from '@/store/auth';
import SalesActions, { DiagnosisActions } from '@/components/SalesActions';
import { BRAND, WARNING } from '@/theme';
import {
  VERDICT_COLOR,
  fenToWan,
  fenToYuanNumber,
  fmtDate,
  fmtDateTime,
  fmtPercent,
  formatYuan,
  toLabelMap,
  yuanToFen,
} from '@/utils/format';
import type {
  AmountChange,
  AutoJudgeResp,
  CustomerProject,
  ProjectReview,
  RiskAlert,
  StageProgressItem,
  StageTransition,
  StandardJudgementRecord,
} from '@/types';

/** 取后端 409/422 的 detail 文案（不吞掉硬门槛等业务提示） */
function detailOf(err: unknown): string {
  const e = err as AxiosError<{ detail?: string }>;
  return e?.response?.data?.detail ?? e?.message ?? '操作失败';
}

/** 去掉「30.00」这类无意义的小数尾巴，仅用于指标卡展示（口径不变） */
function num(v: string): string {
  const n = Number(v);
  return Number.isFinite(n) ? String(n) : v;
}

/**
 * 7 环节顺序（与后端 `backend/app/core/dicts.py` 的 STAGE_LABELS 一致）。
 * 环节中文名一律用后端下发的 `current_stage_label` 口径，这里只用于**排列顺序与箭头步骤条**。
 */
const STAGE_ORDER = [
  'S1_VALID_OPPORTUNITY',
  'S2_CUSTOMER_REACHED',
  'S3_PROPOSAL_QUOTE',
  'S4_BUDGET_CONFIRMED',
  'S5_PROJECT_REVIEWED',
  'S6_BIDDING',
  'S7_CONTRACT_SIGNED',
] as const;

/** 步骤条上的短名（设计稿：有效商机 / 客户触达 / 方案报价 …） */
const STAGE_SHORT: Record<string, string> = {
  S1_VALID_OPPORTUNITY: '有效商机',
  S2_CUSTOMER_REACHED: '客户触达',
  S3_PROPOSAL_QUOTE: '方案报价',
  S4_BUDGET_CONFIRMED: '预算确认',
  S5_PROJECT_REVIEWED: '项目过会',
  S6_BIDDING: '招标采购',
  S7_CONTRACT_SIGNED: '合同签约',
};

/**
 * 箭头式步骤条（设计稿 04 形态）：7 个 chevron 分开的小箭头连成一排。
 * 已完成=浅灰底 + 灰勾；当前=蓝色实心箭头 + 白色数字序号；未开始=浅灰底 + 灰色数字。
 *
 *  业务规则：「要能点击项目已完成阶段，并显示证据结果」→
 * 每个环节（含历史环节、当前环节、未开始环节）都可点，打开**只读**抽屉看
 * 该环节的完成标准与达成依据（`GET /opportunities/{id}/stages/{stage}/progress`）。
 */
function StageArrowBar({
  current,
  onSelect,
  activeStage,
}: {
  current: string | null;
  onSelect?: (stageCode: string) => void;
  activeStage?: string | null;
}) {
  //  后端下发的环节 code 是小写（如 `s2_customer_reached`），STAGE_ORDER 用大写常量 → 必须归一化，
  // 否则「当前环节」永远匹配不上：箭头不亮、按钮也拿不到下一环节中文名。
  const currentIndex = current
    ? STAGE_ORDER.indexOf(current.toUpperCase() as (typeof STAGE_ORDER)[number])
    : -1;
  const last = STAGE_ORDER.length - 1;
  return (
    <div style={{ display: 'flex', alignItems: 'stretch', width: '100%', gap: 4 }}>
      {STAGE_ORDER.map((stage, index) => {
        const isCurrent = index === currentIndex;
        const isDone = currentIndex > -1 && index < currentIndex;
        const bg = isCurrent ? BRAND : 'var(--fill-soft)';
        // 已完成用更深的灰（保证可读），未开始用浅灰（设计稿里未开始就是"淡"的）
        const fg = isCurrent ? 'var(--bg-card)' : isDone ? 'var(--text-sub)' : 'var(--text-muted)';
        const code = stage.toLowerCase();
        const clip =
          index === 0
            ? 'polygon(0 0, calc(100% - 14px) 0, 100% 50%, calc(100% - 14px) 100%, 0 100%)'
            : index === last
              ? 'polygon(0 0, 100% 0, 100% 100%, 0 100%, 14px 50%)'
              : 'polygon(0 0, calc(100% - 14px) 0, 100% 50%, calc(100% - 14px) 100%, 0 100%, 14px 50%)';
        const stateText = isCurrent ? '（当前环节）' : isDone ? '（已完成）' : '（未开始）';
        return (
          <button
            key={stage}
            type="button"
            className="stage-chevron"
            data-active={activeStage === code ? 'true' : undefined}
            aria-label={`查看第 ${index + 1} 环节「${STAGE_SHORT[stage]}」的完成标准与达成依据`}
            title={`${index + 1} ${STAGE_SHORT[stage]}${stateText}｜点击查看该环节的完成标准与达成依据`}
            onClick={() => onSelect?.(code)}
            style={{
              flex: 1,
              minWidth: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 8,
              height: 46,
              background: bg,
              color: fg,
              fontSize: 13,
              fontWeight: isCurrent ? 600 : 400,
              clipPath: clip,
              paddingLeft: index === 0 ? 12 : 24,
              paddingRight: index === last ? 12 : 26,
            }}
          >
            <span
              style={{
                width: 20,
                height: 20,
                borderRadius: '50%',
                flexShrink: 0,
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 11,
                fontWeight: 600,
                background: isCurrent ? 'rgba(255,255,255,0.28)' : 'var(--bg-card)',
                color: isCurrent ? 'var(--bg-card)' : 'var(--text-muted)',
              }}
            >
              {isDone ? <CheckOutlined /> : index + 1}
            </span>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {STAGE_SHORT[stage]}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** 历史环节抽屉只读，勾选与登记仍由当前环节的原入口负责。 */
function StageEvidenceDrawer({
  leadId,
  stage,
  open,
  currentStageCode,
  onClose,
}: {
  leadId: string;
  stage: string | null;
  open: boolean;
  currentStageCode: string;
  onClose: () => void;
}) {
  const detailQuery = useQuery({
    queryKey: ['stage-detail', leadId, stage],
    queryFn: () => getStageProgressDetail(leadId, stage as string),
    enabled: open && !!leadId && !!stage,
    retry: false,
  });
  const detail = detailQuery.data;
  const index = stage
    ? STAGE_ORDER.indexOf(stage.toUpperCase() as (typeof STAGE_ORDER)[number])
    : -1;
  const shortName = stage ? STAGE_SHORT[stage.toUpperCase()] : '';
  const isCurrent = detail?.is_current ?? (!!stage && stage === currentStageCode);
  const notStarted = !!detail && !detail.entered_at;

  const statusTag = isCurrent ? (
    <Tag color="blue">当前环节</Tag>
  ) : notStarted ? (
    <Tag>未开始</Tag>
  ) : detail ? (
    <Tag color="green">已完成</Tag>
  ) : null;

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={560}
      title={
        <Space size={8} wrap>
          <span>
            第{index + 1}环节 · {detail?.stage_label || shortName || '环节详情'}
          </span>
          {statusTag}
        </Space>
      }
    >
      {detailQuery.isLoading ? <Skeleton active paragraph={{ rows: 6 }} /> : null}

      {detailQuery.isError ? (
        <Alert
          type="error"
          showIcon
          message="没能加载这个环节的进度"
          description={
            <Typography.Text style={{ fontSize: 12 }}>
              {detailOf(detailQuery.error)}
              <br />
              下一步：① 刷新页面重试；② 若提示无权限，说明该商机不在你名下，请联系市场部管理；
              ③ 仍失败请把这条提示发给管理员（接口：GET /opportunities/…/stages/{stage}/progress）。
            </Typography.Text>
          }
        />
      ) : null}

      {detail ? (
        <Space direction="vertical" size={14} style={{ width: '100%' }}>
          {isCurrent ? (
            <Alert
              type="info"
              showIcon
              message="当前环节的进度可直接在下方「环节进度」Tab 操作"
              description="本抽屉只读：勾选 / 登记依据仍到下方做，避免两处都能改造成口径混乱。"
            />
          ) : null}

          {notStarted ? (
            <Alert
              type="info"
              showIcon
              message="该环节还没开始"
              description="下面是该环节已配置的完成标准（灰显）；进入该环节后才会逐条生效。"
            />
          ) : null}

          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="进入时间">
              {detail.entered_at ? fmtDateTime(detail.entered_at) : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="离开时间">
              {detail.left_at ? fmtDateTime(detail.left_at) : detail.is_current ? '当前环节' : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="停留天数">
              {detail.stay_days == null ? '待确认' : `${detail.stay_days} 天`}
              {detail.base_days ? (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {' '}
                  / 基准 {detail.base_days} 天
                </Typography.Text>
              ) : null}
              {detail.overdue ? (
                <Tag color="orange" style={{ marginLeft: 6 }}>
                  超期
                </Tag>
              ) : null}
            </Descriptions.Item>
            <Descriptions.Item label="完成度">
              {detail.done_count} / {detail.total_count}
            </Descriptions.Item>
            <Descriptions.Item label="硬门槛" span={2}>
              {detail.hard_gate_passed ? (
                <Tag color="green">已通过</Tag>
              ) : (
                <Tag color="red">未通过</Tag>
              )}
              {!detail.hard_gate_passed && detail.missing_hard_gates.length ? (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  缺：{detail.missing_hard_gates.join('；')}
                </Typography.Text>
              ) : null}
            </Descriptions.Item>
          </Descriptions>

          <div style={{ opacity: notStarted ? 0.6 : 1 }}>
            <Typography.Text strong style={{ fontSize: 13 }}>
              完成标准与达成依据（{detail.done_count}/{detail.total_count}）
            </Typography.Text>
            <List
              size="small"
              dataSource={detail.standards}
              locale={{ emptyText: '该环节还没有配置完成标准' }}
              renderItem={({ standard, completion }) => (
                <List.Item style={{ alignItems: 'flex-start', paddingLeft: 0, paddingRight: 0 }}>
                  <div style={{ width: '100%' }}>
                    <Space size={6} wrap>
                      {completion.is_done ? (
                        <CheckCircleFilled style={{ color: 'var(--success)' }} />
                      ) : (
                        <CloseCircleFilled style={{ color: 'var(--text-muted)' }} />
                      )}
                      <Typography.Text strong style={{ fontSize: 13 }}>
                        {standard.seq}. {standard.content}
                      </Typography.Text>
                      {standard.is_hard_gate ? <Tag color="red">硬门槛</Tag> : null}
                      {completion.is_done ? (
                        <Tag>{completion.source_label || '已登记'}</Tag>
                      ) : null}
                    </Space>

                    {completion.is_done ? (
                      <div
                        style={{
                          marginTop: 6,
                          background: 'var(--fill-soft)',
                          border: '1px solid var(--border)',
                          borderRadius: 6,
                          padding: '8px 10px',
                        }}
                      >
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          达成依据：
                        </Typography.Text>
                        <Typography.Text
                          style={{ fontSize: 13, display: 'block', wordBreak: 'break-all' }}
                        >
                          {completion.evidence || '（这条标准当时没有留下依据文本）'}
                        </Typography.Text>
                        <Space size={12} wrap style={{ marginTop: 4 }}>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            完成时间：{completion.done_at ? fmtDateTime(completion.done_at) : '—'}
                          </Typography.Text>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            完成人：{completion.done_by_name || '—'}
                          </Typography.Text>
                        </Space>
                        {completion.remark ? (
                          <Typography.Text
                            type="secondary"
                            style={{ fontSize: 12, display: 'block', marginTop: 2 }}
                          >
                            备注：{completion.remark}
                          </Typography.Text>
                        ) : null}
                      </div>
                    ) : (
                      <Typography.Text
                        type="secondary"
                        style={{ fontSize: 12, display: 'block', marginTop: 4 }}
                      >
                        未完成
                        {standard.evidence_hint ? `（需要：${standard.evidence_hint}）` : ''}
                      </Typography.Text>
                    )}
                  </div>
                </List.Item>
              )}
            />
          </div>

          {detail.transitions.length ? (
            <div>
              <Typography.Text strong style={{ fontSize: 13 }}>
                该环节的流转记录
              </Typography.Text>
              <div style={{ marginTop: 6 }}>
                {detail.transitions.map((t) => (
                  <div key={t.id} style={{ fontSize: 12, color: 'var(--text-sub)', lineHeight: 1.9 }}>
                    {fmtDateTime(t.transition_at)} · {t.from_stage_label || '（起点）'} →{' '}
                    {t.to_stage_label}
                    {t.operator_name ? ` · ${t.operator_name}` : ''}
                    {t.basis ? ` · ${t.basis}` : ''}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </Space>
      ) : null}
    </Drawer>
  );
}

/**
 * 商机详情顶部的「标签在上 / 值在下」指标块（设计稿 04 的 5 张等宽白卡）。
 *
 * 为什么不用公共 `KpiCard`：那套是「彩色图标 + 值 + 进度条」的工作台形态，
 * 本页稿子是「大数字 + 右侧胶囊」，两者形态不同，硬套会两边都不像。
 * `hint` 依旧走 Tooltip（判据 C3：口径就地可见），值缺失时给 `empty` 与「去填写」入口（判据 C2）。
 */
function MetricTile({
  label,
  value,
  valueColor,
  unit,
  badge,
  icon,
  empty,
  emptyHint,
  onGo,
  hint,
}: {
  label: ReactNode;
  value: ReactNode;
  valueColor?: string;
  unit?: ReactNode;
  badge?: ReactNode;
  icon?: ReactNode;
  empty?: boolean;
  emptyHint?: string;
  onGo?: () => void;
  hint?: ReactNode;
}) {
  return (
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
      <Tooltip title={hint}>
        <div style={{ fontSize: 13, color: 'var(--text-sub)', marginBottom: 6, cursor: hint ? 'help' : undefined }}>
          {label}
        </div>
      </Tooltip>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span
          style={{
            fontSize: 28,
            fontWeight: 600,
            lineHeight: 1.15,
            color: empty ? 'var(--text-muted)' : (valueColor ?? 'var(--text-main)'),
            whiteSpace: 'nowrap',
          }}
        >
          {value}
        </span>
        {unit && <span style={{ fontSize: 14, color: 'var(--text-sub)' }}>{unit}</span>}
        {badge}
      </div>
      {empty && (
        <div style={{ marginTop: 4, fontSize: 12, color: 'var(--text-sub)' }}>
          {emptyHint}
          {onGo && (
            <>
              {' '}
              <a onClick={onGo}>去填写</a>
            </>
          )}
        </div>
      )}
      {icon && <div style={{ marginTop: 2 }}>{icon}</div>}
    </div>
  );
}

/** 数值胶囊（设计稿里的「超期 2 天」「中风险」这类小标签） */
function Pill({ children, tone = 'amber' }: { children: ReactNode; tone?: 'amber' | 'purple' | 'gray' }) {
  const map = {
    amber: { background: 'var(--warning-fill)', borderColor: 'var(--warning-line)', color: 'var(--warning-strong)' },
    purple: { background: 'var(--violet-soft)', borderColor: 'var(--violet-line)', color: 'var(--brand-deep)' },
    gray: { background: 'var(--fill-soft)', borderColor: 'var(--fill-line)', color: 'var(--text-sub)' },
  } as const;
  const c = map[tone];
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        background: c.background,
        border: `1px solid ${c.borderColor}`,
        color: c.color,
        borderRadius: 999,
        padding: '2px 10px',
        fontSize: 12,
        lineHeight: '18px',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  );
}

interface BlockerItem {
  key: string;
  standardId: string;
  title: string;
  reason: string;
}

/** 2.2 商机详情：门槛与证据优先｜环节进度｜金额与变更｜跟进记录｜风险｜流转｜评审 */
export default function OpportunityDetail() {
  const { id } = useParams();
  const leadId = id ?? '';
  const navigate = useNavigate();
  const qc = useQueryClient();
  const user = useAuthStore(s => s.user);
  const role = user?.role;
  const { dicts } = useDicts();
  const riskMap = toLabelMap(dicts.risk_types);
  const methodMap = toLabelMap(dicts.follow_methods);
  const riskOptions = (dicts.risk_types ?? []).map((r) => ({ value: r.value, label: r.label }));

  /** 方案商务部门按矩阵只看合同相关，商机详情动作只读 */
  const readOnly = role === 'biz_support';

  const [evidenceItem, setEvidenceItem] = useState<StageProgressItem | null>(null);

  /**
   * 最近一条跟进内容 —— 「登记达成依据」时一键带入。
   *
   * 业务规则：**环节判断不需要上传凭证**，应让市场专员在跟进里
   * 反馈结构化信息，由系统抽取判定；所以这里把"已经写过的跟进内容"直接当作依据候选，
   * 不让人再上传/再重复描述一遍（简化事、赋能人）。
   */

  /** 已上传证据的只读查看（设计稿 04 证据列的「查看」） */
  const [viewItem, setViewItem] = useState<StageProgressItem | null>(null);
  const [moveType, setMoveType] = useState<'advance' | 'rollback' | null>(null);
  const [moveError, setMoveError] = useState('');
  const [amountOpen, setAmountOpen] = useState(false);
  const [riskOpen, setRiskOpen] = useState(false);
  const [handling, setHandling] = useState<RiskAlert | null>(null);
  const [diagOpen, setDiagOpen] = useState(false);
  const [dossierOpen, setDossierOpen] = useState(false);
  const [generatedDiagnosis, setDiagnosis] = useState<AiDiagnosis | null>(null);
  const savedDiagnoses = useQuery({
    queryKey: ['ai-diagnoses', leadId],
    queryFn: () => listDiagnoses({ lead_id: leadId, page_size: 20 }),
    enabled: !!leadId,
  });
  const diagnosis = generatedDiagnosis?.lead_id === leadId ? generatedDiagnosis
    : savedDiagnoses.data?.items.find(item => item.trigger !== 'requirements') ?? null;
  /** P1-d：丢单登记 / 撤销丢单 */
  const [loseOpen, setLoseOpen] = useState(false);
  const [revertOpen, setRevertOpen] = useState(false);
  /** 评审记录 Tab 懒加载 */
  const [activeTab, setActiveTab] = useState('progress');
  /** 点 7 环节步骤条打开的抽屉（环节 code；null = 关闭） */
  const [stageDrawer, setStageDrawer] = useState<string | null>(null);
  /** 门槛横幅「去补齐」用：滚到第一条缺口并打开证据入口 */
  const standardRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [evidenceForm] = Form.useForm<{ evidence: string }>();
  const [moveForm] = Form.useForm<{ basis: string }>();
  const [amountForm] = Form.useForm<{ amount_yuan: number; reason: string }>();
  const [riskForm] = Form.useForm<{ risk_types: string[]; description: string }>();
  const [handleForm] = Form.useForm<{ handle_note: string }>();
  const [loseForm] = Form.useForm<{ lost_reason_types: string[]; remark: string }>();
  const [revertForm] = Form.useForm<{ remark: string }>();

  const {
    data,
    isLoading,
    isError: detailIsError,
    error: detailError,
    refetch: refetchDetail,
    isFetching: detailFetching,
  } = useQuery({
    queryKey: ['opportunity-detail', leadId],
    queryFn: () => getOpportunityDetail(leadId),
    enabled: !!leadId,
  });
  const dossierQuery = useQuery({
    queryKey: ['assistant-dossier', leadId],
    queryFn: () => assistant.dossier(leadId),
    enabled: !!leadId && !readOnly,
    retry: false,
  });

  /**
   * 同客户其他项目（客户-项目两层模型）。
   *
   * 一个客户（学校 / 教育局）名下可以有多个项目商机，各自走 7 环节；
   * 没有这个区块时，专员要切换项目只能回商机列表按客户名搜（多切换、少判断）。
   *
   *  该接口自身可用；但同客户若**有待处理风险**，客户详情接口会 500（后端缺陷），
   * 本区块不受影响（它只走 of-lead）。查询失败时给出"为什么 + 怎么办"，不静默消失。
   */
  const siblingsQuery = useQuery({
    queryKey: ['customer-of-lead', leadId],
    queryFn: () => getCustomerOfLead(leadId),
    enabled: !!leadId,
    retry: false,
  });

  const lead = data?.lead;
  const dossierFacts = dossierQuery.data?.facts ?? {};
  const progress = data?.stage_progress ?? null;
  const siblings = siblingsQuery.data?.siblings ?? [];
  const siblingCustomer = siblingsQuery.data?.customer ?? null;
  /**
   * 标题口径：**客户名 / 项目名**。
   * `project_name` 为空（历史数据 / 未填项目名）时退回只显示客户名，不出现「客户名 / 」这种半截标题。
   */
  const projectName = (lead?.project_name ?? '').trim();
  const pageTitle = projectName ? `${lead?.name ?? ''} / ${projectName}` : (lead?.name ?? '商机详情');

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['opportunity-detail', leadId] });
    qc.invalidateQueries({ queryKey: ['stage-detail', leadId] });
    qc.invalidateQueries({ queryKey: ['assistant-dossier', leadId] });
    qc.invalidateQueries({ queryKey: ['opportunities'] });
    qc.invalidateQueries({ queryKey: ['workbench'] });
    qc.invalidateQueries({ queryKey: ['leads'] });
    // 同客户其他项目（环节 / 金额 / 价值口径）也要跟着变
    qc.invalidateQueries({ queryKey: ['customer-of-lead', leadId] });
    qc.invalidateQueries({ queryKey: ['customer-detail'] });
    qc.invalidateQueries({ queryKey: ['customers'] });
  };

  /* ---------------- 门槛与证据缺口（本次核心：不靠 409 才告诉人） ---------------- */

  /**
   * 缺口清单（不在 Hook 之后调用，纯派生）。
   * 判定口径与后端一致但**只用于提前提示**：硬门槛标准未完成、或已完成但没填证据、或后端点名缺失的硬门槛。
   */
  const blockers = useMemo<BlockerItem[]>(() => {
    if (!progress) return [];
    const items: BlockerItem[] = [];
    (progress.standards ?? []).forEach((item) => {
      const { standard, completion } = item;
      if (completion.is_done) {
        if (!completion.evidence) {
          items.push({
            key: `evidence-${standard.id}`,
            standardId: standard.id,
            title: standard.content,
            reason: '已勾选但没有可依据的跟进信息（请先写跟进，或用「按跟进信息自动判定」）',
          });
        }
        return;
      }
      if (standard.is_hard_gate) {
        items.push({
          key: `hard-${standard.id}`,
          standardId: standard.id,
          title: standard.content,
          reason: `硬门槛未完成（证据要求：${standard.evidence_hint || '拜访记录 / 文档 / 截图 / 确认人+时间'}）`,
        });
      }
    });
    (progress.missing_hard_gates ?? []).forEach((content, index) => {
      if (items.some((i) => i.title === content)) return;
      items.push({
        key: `gate-${index}`,
        standardId: '',
        title: content,
        reason: '后端判定为缺失的硬门槛，补齐并附证据后即可推进',
      });
    });
    return items;
  }, [progress]);

  /** 最近一条跟进（右栏「近期跟进时间线」高亮用） */

  /** 最近一条跟进内容（按跟进日期倒序取第一条）—— 「用最近跟进登记」的依据候选 */
  const recentFollowContent = (() => {
    const list = [...(data?.followups ?? [])].sort(
      (a, b) => dayjs(b.followed_at).valueOf() - dayjs(a.followed_at).valueOf(),
    );
    return (list[0]?.content ?? '').trim();
  })();

  const [judgeResult, setJudgeResult] = useState<AutoJudgeResp | null>(null);
  const [judgeStatus, setJudgeStatus] = useState<'loading' | 'ready' | 'unavailable'>('loading');
  const [judgeError, setJudgeError] = useState('');
  /** 本次候选是否已由负责人确认（AI satisfied 本身不会写入阶段）。 */
  const [judgeApplied, setJudgeApplied] = useState(false);
  const [selectedStandardIds, setSelectedStandardIds] = useState<string[]>([]);
  const [confirmationTexts, setConfirmationTexts] = useState<Record<string, string>>({});
  const [confirmationNote, setConfirmationNote] = useState('');
  const [correctionFollowupId, setCorrectionFollowupId] = useState('');
  const [correctArrangement, setCorrectArrangement] = useState(false);
  const [arrangementAfter, setArrangementAfter] = useState('multiple');
  const [arrangementSourceQuote, setArrangementSourceQuote] = useState('');
  const [correctContractPlan, setCorrectContractPlan] = useState(false);
  const [contractPlanAfter, setContractPlanAfter] = useState('');
  const [contractPlanSourceQuote, setContractPlanSourceQuote] = useState('');

  /** 最新一次判定（只读；系统自动产生） */
  const judgementQuery = useQuery({
    queryKey: ['standard-judgement', leadId],
    queryFn: () => getStandardJudgement(leadId),
    enabled: !!leadId && !readOnly,
    retry: false,
  });

  /** 判定记录 → 与 auto-judge 返回**同构**的渲染模型（复用同一块结果 UI） */
  const toJudgeView = (rec: StandardJudgementRecord): AutoJudgeResp => ({
    judgement_id: rec.id,
    stage: rec.stage,
    stage_label: rec.stage_label,
    judged_at: rec.judged_at,
    model_used: rec.model_used,
    parse_mode: rec.parse_mode,
    items: rec.items ?? [],
    already_done: rec.already_done ?? [],
    hard_gate_passed: rec.hard_gate_passed,
    missing_hard_gates: rec.missing_hard_gates ?? [],
  });

  const doAutoJudge = useMutation({
    mutationFn: () => autoJudgeStandards(leadId),
    onSuccess: (r) => {
      setJudgeResult(r);
      setJudgeStatus('ready');
      setJudgeError('');
      setSelectedStandardIds([]);
      setConfirmationTexts({});
      setConfirmationNote('');
      setCorrectArrangement(false);
      setCorrectContractPlan(false);
      // 判定结果已覆盖落库，刷新「最新判定」以免下次进页面重复判
      qc.invalidateQueries({ queryKey: ['standard-judgement', leadId] });
    },
    onError: (err) => {
      // 明确失败：不假装成功、也不一直转圈（503 = 模型不可用）
      setJudgeStatus('unavailable');
      setJudgeError(detailOf(err));
    },
  });

  /** 同一份判定只静默重判一次（避免每次 refetch 都打模型） */
  const autoJudgeKeyRef = useRef('');
  const judgeRecordIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (readOnly || !judgementQuery.isSuccess) return;
    if (dossierQuery.isPending) return;
    const resp = judgementQuery.data;
    if (resp.judgement) {
      setJudgeResult(toJudgeView(resp.judgement));
      if (judgeRecordIdRef.current !== resp.judgement.id) {
        // 换了一条判定（新跟进触发覆盖）→ 采纳状态按后端 status 重置
        judgeRecordIdRef.current = resp.judgement.id;
        setJudgeApplied(resp.judgement.status === 'accepted');
      }
    }
    // 没有判定结果，或判定依据早于最新跟进（有新跟进还没判过）→ 自动重判
    if (!resp.judgement || resp.stale) {
      const key = [
        leadId,
        resp.judgement?.id ?? 'none',
        resp.judgement?.judged_at ?? 'none',
        resp.latest_followup_at ?? 'none',
      ].join(':');
      if (autoJudgeKeyRef.current !== key) {
        autoJudgeKeyRef.current = key;
        setJudgeStatus('loading');
        doAutoJudge.mutate();
      }
      return;
    }
    setJudgeStatus('ready');
  }, [judgementQuery.isSuccess, judgementQuery.data, dossierQuery.isPending, dossierQuery.data, readOnly, leadId]);

  const doSalesConfirm = useMutation({
    mutationFn: async () => {
      if (!judgeResult?.judgement_id || !user?.id || !lead?.current_stage) {
        throw new Error('缺少当前候选版本或负责人身份，请刷新后重试');
      }
      const corrections = [] as NonNullable<Parameters<typeof confirmStageCandidates>[1]['corrections']>;
      if (correctArrangement) {
        if (!correctionFollowupId || !arrangementSourceQuote.trim()) {
          throw new Error('请为合同安排更正选择来源跟进并填写其中的原文');
        }
        corrections.push({
          fact_key: 'contract_arrangement_status',
          before_value: typeof dossierFacts.contract_arrangement_status === 'string'
            ? dossierFacts.contract_arrangement_status : null,
          after_value: arrangementAfter,
          followup_id: correctionFollowupId,
          source_quote: arrangementSourceQuote.trim(),
        });
      }
      if (correctContractPlan) {
        if (!correctionFollowupId || !contractPlanAfter.trim() || !contractPlanSourceQuote.trim()) {
          throw new Error('请为合同安排说明更正选择来源跟进并填写更正内容和原文');
        }
        corrections.push({
          fact_key: 'contract_plan',
          before_value: typeof dossierFacts.contract_plan === 'string'
            ? dossierFacts.contract_plan : null,
          after_value: contractPlanAfter.trim(),
          followup_id: correctionFollowupId,
          source_quote: contractPlanSourceQuote.trim(),
        });
      }
      const request = {
        judgement_id: judgeResult.judgement_id,
        stage: judgeResult.stage,
        expected_dossier_revision: dossierQuery.data?.revision ?? null,
        confirmation_note: confirmationNote.trim(),
        items: selectedStandardIds.map((standard_id) => ({
          standard_id,
          confirmation_text: (confirmationTexts[standard_id] ?? '').trim(),
        })),
        corrections,
      };
      const canonical = JSON.stringify(request);
      const bytes = new TextEncoder().encode(canonical);
      const digest = await crypto.subtle.digest('SHA-256', bytes);
      const idempotency_key = `sales:${user.id}:${Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('').slice(0, 48)}`;
      return confirmStageCandidates(leadId, { ...request, idempotency_key });
    },
    onSuccess: (r) => {
      message.success(`已记录负责人确认 ${r.applied_count} 项；AI原分类保留为候选历史`);
      setJudgeApplied(true);
      setSelectedStandardIds([]);
      setConfirmationTexts({});
      setConfirmationNote('');
      qc.setQueryData(['opportunity-detail', leadId], (old: typeof data) =>
        old ? { ...old, stage_progress: r.stage_progress } : old,
      );
      qc.invalidateQueries({ queryKey: ['standard-judgement', leadId] });
      refresh();
    },
    onError: (err) => message.error(detailOf(err)),
  });

  /* ---------------- 完成标准勾选 ---------------- */
  const doToggle = useMutation({
    mutationFn: (v: { standardId: string; isDone: boolean; evidence?: string }) =>
      toggleStandard(leadId, v.standardId, { is_done: v.isDone, evidence: v.evidence }),
    onSuccess: (r) => {
      message.success('完成标准已更新');
      // 用返回的 stage_progress 直接回填
      qc.setQueryData(['opportunity-detail', leadId], (old: typeof data) =>
        old ? { ...old, stage_progress: r.stage_progress } : old,
      );
      setEvidenceItem(null);
      evidenceForm.resetFields();
      refresh();
    },
  });

  /** 打开证据入口（勾选即弹，或从门槛横幅/证据列点「上传证据」补） */
  const openEvidence = (item: StageProgressItem, preset?: string) => {
    if (readOnly) return;
    setEvidenceItem(item);
    evidenceForm.setFieldsValue({ evidence: preset ?? item.completion.evidence ?? '' });
  };

  /** 门槛横幅「去补齐」：滚到第一条缺口，能就地填写就直接打开证据入口 */
  const goFixFirstBlocker = () => {
    const first = blockers[0];
    if (!first) return;
    const node = standardRefs.current[first.standardId];
    node?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const item = progress?.standards.find((s) => s.standard.id === first.standardId);
    if (item) openEvidence(item);
  };

  /* ---------------- 环节推进 / 回退 ---------------- */
  const doMove = useMutation({
    mutationFn: (v: { type: 'advance' | 'rollback'; basis: string }) =>
      v.type === 'advance'
        ? advanceStage(leadId, v.basis)
        : rollbackStage(leadId, v.basis),
    onSuccess: () => {
      message.success(moveType === 'advance' ? '已推进到下一环节' : '已回退上一环节');
      setMoveType(null);
      setMoveError('');
      moveForm.resetFields();
      refresh();
    },
    onError: (err) => {
      // 硬门槛未过（409）等业务提示原样展示，不关闭弹窗，让用户看到原因
      setMoveError(detailOf(err));
    },
  });

  /* ---------------- 金额修正 ---------------- */
  const doAmount = useMutation({
    mutationFn: (v: { amountFen: number; reason: string }) =>
      changeAmount(leadId, v.amountFen, v.reason),
    onSuccess: () => {
      message.success('金额已修正并留痕');
      setAmountOpen(false);
      amountForm.resetFields();
      refresh();
    },
  });

  /* ---------------- 风险 ---------------- */
  const doAddRisk = useMutation({
    mutationFn: (v: { risk_types: string[]; description: string }) =>
      addRisks(leadId, v.risk_types, v.description),
    onSuccess: () => {
      message.success('风险已登记');
      setRiskOpen(false);
      riskForm.resetFields();
      refresh();
    },
  });

  const doHandleRisk = useMutation({
    mutationFn: (v: { riskId: string; note: string }) =>
      updateRisk(v.riskId, { status: 'resolved', handle_note: v.note }),
    onSuccess: () => {
      message.success('风险已标记为已处理');
      setHandling(null);
      handleForm.resetFields();
      refresh();
    },
  });

  /* ---------------- AI 诊断 ---------------- */
  const doDiagnose = useMutation({
    mutationFn: () => aiDiagnose({ lead_id: leadId }),
    onSuccess: (d) => {
      setDiagnosis(d);
      qc.invalidateQueries({ queryKey: ['ai-diagnoses'] });
    },
  });

  /* ---------------- 丢单登记 / 撤销 ---------------- */
  const doLose = useMutation({
    mutationFn: (v: { reasons: string[]; remark: string }) =>
      loseOpportunity(leadId, { lost_reason_types: v.reasons, remark: v.remark }),
    onSuccess: () => {
      message.success('已登记丢单');
      setLoseOpen(false);
      loseForm.resetFields();
      refresh();
      qc.invalidateQueries({ queryKey: ['lost-analysis'] });
      qc.invalidateQueries({ queryKey: ['dashboard-risk-center'] });
    },
  });

  const doRevertLoss = useMutation({
    mutationFn: (remark: string) => revertLoss(leadId, remark),
    onSuccess: () => {
      message.success('已撤销丢单');
      setRevertOpen(false);
      revertForm.resetFields();
      refresh();
      qc.invalidateQueries({ queryKey: ['lost-analysis'] });
    },
  });

  /* ---------------- 评审记录（2.7 只读回显） ---------------- */
  const reviewsQuery = useQuery({
    queryKey: ['opportunity-reviews', leadId],
    queryFn: () => listReviews({ lead_id: leadId, page: 1, page_size: 20 }),
    enabled: !!leadId && activeTab === 'reviews',
  });

  /* ---------------- 加载 / 失败 / 不存在 三态（判据 C2：每个状态都要说"为什么 + 怎么办"） ---------------- */

  if (isLoading) {
    // 骨架屏按真实骨架铺（标题区 + 指标行 + 步骤条 + 左内容 + 右栏），避免白屏/光秃秃转圈
    return (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card size="small">
          <Skeleton active paragraph={{ rows: 2 }} title={{ width: 260 }} />
        </Card>
        <Row gutter={[12, 12]}>
          {[0, 1, 2, 3, 4].map((i) => (
            <Col key={i} xs={12} md={8} xl={4}>
              <Card size="small">
                <Skeleton active paragraph={{ rows: 1 }} title={{ width: 80 }} />
              </Card>
            </Col>
          ))}
        </Row>
        <Card size="small">
          <Skeleton active paragraph={{ rows: 4 }} title={{ width: 180 }} />
        </Card>
      </Space>
    );
  }

  if (!leadId) {
    return (
      <Card>
        <NextStepGuide
          title="链接里没有商机 ID，无法打开详情"
          description="这条地址缺少必要参数（/opportunities/:id）。请从「2.1 商机列表」点一行进来。"
          actions={
            <Space>
              <Button type="primary" onClick={() => navigate('/opportunities')}>
                去商机列表
              </Button>
              <Button onClick={() => navigate('/workbench')}>回工作台</Button>
            </Space>
          }
        />
      </Card>
    );
  }

  if (detailIsError) {
    const status = (detailError as AxiosError)?.response?.status;
    const notFound = status === 404;
    const denied = status === 403;
    return (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card>
          <NextStepGuide
            title={
              notFound
                ? '这条商机不存在（或已被删除）'
                : denied
                  ? '你没有权限查看这条商机'
                  : '商机详情没读出来（接口 /opportunities/{id} 返回失败）'
            }
            description={
              notFound
                ? '为什么：后端返回 404——链接里的商机 ID 找不到记录，可能链接过期、记录被删，或该客户尚未进入闭环二（只有进入闭环二的商机才有详情页）。怎么办：回商机列表重新点进去。'
                : denied
                  ? '为什么：后端返回 403——专员只能看本人负责的商机，管理人员可看团队。怎么办：找市场部管理人员确认这条商机的负责人，或改由负责人查看。'
                  : `为什么：请求没成功（${detailOf(detailError)}），可能是登录已过期、网络中断或后端服务暂时不可用。怎么办：先点「重试」；仍失败请刷新页面重新登录；持续失败把这条提示发给系统管理员。`
            }
            actions={
              <Space wrap>
                <Button type="primary" loading={detailFetching} onClick={() => refetchDetail()}>
                  重试
                </Button>
                <Button onClick={() => navigate('/opportunities')}>去商机列表</Button>
                <Button onClick={() => navigate('/workbench')}>回工作台</Button>
              </Space>
            }
          />
        </Card>
      </Space>
    );
  }

  if (!lead) {
    return (
      <Card>
        <NextStepGuide
          title="商机不存在或无权访问"
          description="接口返回成功但没有商机数据。为什么：可能是链接失效，或该客户尚未进入闭环二（只有进入闭环二的商机才有详情页）。怎么办：回商机列表重新点进去。"
          actions={
            <Space>
              <Button type="primary" onClick={() => navigate('/opportunities')}>
                去商机列表
              </Button>
              <Button onClick={() => navigate('/leads')}>去线索池</Button>
            </Space>
          }
        />
      </Card>
    );
  }

  const canReview = role === 'admin' || role === 'market_manager';
  const leadLost = lead.status === 'lost';
  const stayDays = progress ? progress.stay_days : lead.stage_stay_days;
  const baseDays = progress?.base_days ?? lead.stage_base_days ?? 0;
  const overdueDays = lead.status !== 'signed' && baseDays > 0 && stayDays != null ? Math.max(0, stayDays - baseDays) : 0;
  const hasBlocker = blockers.length > 0;
  /** 完成标准进度百分比（设计稿 04 卡片标题下的进度条 + 33%） */
  const donePercent =
    progress && progress.total_count > 0
      ? Math.round((progress.done_count / progress.total_count) * 100)
      : 0;
  /** 「推进至 X」按钮上的下一环节中文名（后端口径 STAGE_SHORT 只用于显示顺序） */
  const currentStageKey = (lead.current_stage ?? progress?.stage ?? '').toUpperCase();
  const currentStageIndex = currentStageKey
    ? STAGE_ORDER.indexOf(currentStageKey as (typeof STAGE_ORDER)[number])
    : -1;
  const nextStageLabel =
    currentStageIndex >= 0 && currentStageIndex < STAGE_ORDER.length - 1
      ? `至${STAGE_SHORT[STAGE_ORDER[currentStageIndex + 1]]}`
      : '';
  const advanceBlockedReason = readOnly
    ? '方案商务部门为只读视图，不能推进环节'
    : !progress
      ? '该客户尚未进入闭环二，没有可推进的环节'
      : hasBlocker
        ? `还有 ${blockers.length} 项未补齐（硬门槛 / 证据），补齐后按钮自动可点`
        : '';

  /* ---------------- 子视图 ---------------- */

  const progressTab = (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {progress && !progress.standards.length && (
        <GateBanner
          tone="tip"
          title="该环节后端还没有配置完成标准，因此这里没有可勾选项。"
          items={['请管理员到「系统设置 → 环节标准配置」补标准卡后再推进；补齐前「推进」按钮会保持置灰。']}
        />
      )}

      <Card
        size="small"
        title={
          <Space wrap size={8}>
            <span>{progress?.stage_label || lead.current_stage_label || '当前环节'}完成标准</span>
            <span style={{ color: BRAND, fontWeight: 600 }}>
              {progress?.done_count ?? 0}/{progress?.total_count ?? 0}
            </span>
          </Space>
        }
      >
        {/* 进度条 + 百分比（设计稿 04：标题右上角一行 33%） */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
          <Progress
            percent={donePercent}
            showInfo={false}
            size={['100%', 8]}
            strokeColor={BRAND}
            trailColor="var(--brand-soft)"
            style={{ flex: 1, margin: 0 }}
          />
          <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-main)', minWidth: 40, textAlign: 'right' }}>
            {donePercent}%
          </span>
        </div>

        {/*  AI 读本项目的跟进记录自动逐条判定标准是否满足（依据引用的跟进原话，可核对） */}
        {!readOnly && user?.id === lead.owner_id && (
          <div style={{ margin: '12px 0' }}><CompletedStandardReview key={leadId} leadId={leadId} /></div>
        )}
        {!readOnly && user?.id === lead.owner_id && (
          <Collapse size="small" style={{ marginBottom: 16 }} items={[{
            key: 'standard-judgement',
            label: '核对 AI 标准判定与原文依据',
            children: (          <div
            style={{
              border: `1px solid ${BRAND}`,
              background: 'var(--brand-soft)',
              borderRadius: 8,
              padding: 12,
              marginBottom: 12,
            }}
          >
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Space wrap size={8}>
                <RobotOutlined style={{ color: BRAND }} />
                <Typography.Text strong>按跟进信息自动判定</Typography.Text>
                <Tag color="blue">系统自动</Tag>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  系统已根据本项目已提交的 {(data?.followups ?? []).length}{' '}
                  条跟进自动逐条判定，并**引用跟进原话**当依据（可核对）；
                  <Typography.Text strong style={{ fontSize: 12 }}>
                    采纳后才生效
                  </Typography.Text>
                </Typography.Text>
              </Space>

              <Space wrap size={8}>
                {doAutoJudge.isPending ? (
                  <Button type="primary" icon={<ThunderboltOutlined />} loading>
                    AI 正在判定…
                  </Button>
                ) : null}
                {judgeResult && !judgeApplied && !doSalesConfirm.isPending && (
                  <Button
                    type="primary"
                    ghost
                    disabled={
                      !selectedStandardIds.length
                      || !confirmationNote.trim()
                      || selectedStandardIds.some(id => !confirmationTexts[id]?.trim())
                      || judgementQuery.data?.stale === true
                      || judgeResult.stage !== lead?.current_stage
                    }
                    onClick={() => doSalesConfirm.mutate()}
                  >
                    确认采纳所选 {selectedStandardIds.length} 项
                  </Button>
                )}
                {judgeApplied && <Tag color="green">本版候选已有负责人确认</Tag>}
                {doSalesConfirm.isPending && <Button loading>正在保存负责人确认…</Button>}
                {selectedStandardIds.length > 0 && !doSalesConfirm.isPending && (
                  <Button
                    onClick={() => {
                      setSelectedStandardIds([]);
                      setConfirmationTexts({});
                      setConfirmationNote('');
                      setCorrectArrangement(false);
                      setCorrectContractPlan(false);
                      setArrangementSourceQuote('');
                      setContractPlanSourceQuote('');
                    }}
                  >
                    取消本次确认
                  </Button>
                )}
                {/* 次要入口：判定结果不对 / 有新跟进想立刻重跑时才需要点 */}
                <Button
                  size="small"
                  loading={doAutoJudge.isPending}
                  onClick={() => doAutoJudge.mutate()}
                >
                  重新判定
                </Button>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  未选择或取消时，候选不会写入阶段或档案。销售确认记录会标明负责人和来源，不会冒充AI自动达成。
                </Typography.Text>
              </Space>

              {judgeStatus === 'unavailable' && (
                <Alert
                  type="warning"
                  showIcon
                  message="模型暂不可用，本次没能给出判定（这不等于「判定为未满足」）"
                  description={
                    <Typography.Text style={{ fontSize: 12 }}>
                      {judgeError || '模型接口返回错误（常见 503 / 超时）。'}
                      下一步：① 稍后会自动重试——再写一条跟进，或重新进入本页即可触发；
                      ② 也可以直接在下面的标准清单里手工「登记达成依据」；
                      ③ 若长时间不可用，请管理员检查后端 AI 密钥与模型服务是否正常。
                      {judgeResult ? '下方仍是上一次的判定结果，可继续核对采纳。' : ''}
                    </Typography.Text>
                  }
                />
              )}

              {judgementQuery.data?.stale && doAutoJudge.isPending && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  检测到有新跟进还没判定过，正在重新判定…
                </Typography.Text>
              )}
              {judgementQuery.isError && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  读取系统自动判定结果失败（{detailOf(judgementQuery.error)}）。可以点「重新判定」
                  立即判一次；判定不成功也不会影响已写入的跟进。
                </Typography.Text>
              )}
              {judgeStatus === 'loading' &&
                !judgeResult &&
                !doAutoJudge.isPending &&
                !judgementQuery.isError && (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    正在读取系统自动判定结果…
                  </Typography.Text>
                )}

              {judgeResult && (
                <div
                  style={{
                    background: 'var(--bg-card)',
                    border: '1px solid var(--border)',
                    borderRadius: 8,
                    padding: '10px 12px',
                  }}
                >
                    <Space direction="vertical" size={10} style={{ width: '100%' }}>
                    <Space wrap size={8}>
                      <Typography.Text strong style={{ fontSize: 13 }}>
                        阶段候选（{judgeResult.stage_label}）
                      </Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {judgeResult.model_used ?? '—'} · {fmtDateTime(judgeResult.judged_at)}
                        {judgeResult.already_done.length
                          ? ` · 已完成 ${judgeResult.already_done.length} 项（未重复判定）`
                          : ''}
                      </Typography.Text>
                    </Space>
                      <Alert
                        type="info"
                        showIcon
                        message="AI结果只是待核对候选，不会自动写入完成标准或改档案。请先核对项目、版本和依据；只有你明确选择并确认的项目才会保存。"
                      />

                    {judgeResult.items.length === 0 && (
                      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                        当前版本没有新的阶段候选可供确认。
                      </Typography.Text>
                    )}

                    {judgeResult.items.map((it) => (
                      <div key={it.standard_id} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 10 }}>
                        <Space align="start" style={{ width: '100%' }}>
                          <Checkbox
                            checked={selectedStandardIds.includes(it.standard_id)}
                            disabled={judgeApplied || judgementQuery.data?.stale === true || judgeResult.stage !== lead?.current_stage}
                            onChange={(event) => {
                              setSelectedStandardIds((current) => event.target.checked
                                ? [...current, it.standard_id]
                                : current.filter(id => id !== it.standard_id));
                            }}
                          />
                          <div style={{ minWidth: 0, flex: 1 }}>
                            <Space wrap size={6}>
                              <Typography.Text strong style={{ fontSize: 13 }}>{it.seq}. {it.content}</Typography.Text>
                              {it.is_hard_gate && <Tag color="red">硬门槛</Tag>}
                              <Tag color={it.satisfied ? 'blue' : 'default'}>AI候选：{it.satisfied ? '满足' : '未满足'}</Tag>
                            </Space>
                            <div style={{ marginTop: 4 }}>
                              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                AI判断理由：{it.reason || '无补充理由'}{it.evidence_hint ? `（标准要求：${it.evidence_hint}）` : ''}
                              </Typography.Text>
                            </div>
                            {it.evidence_quote && (
                              <div style={{ marginTop: 4 }}>
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>AI展示依据候选：</Typography.Text>
                                <Typography.Text style={{ fontSize: 13 }}>「{it.evidence_quote}」</Typography.Text>
                                {it.followup_at && <Typography.Text type="secondary" style={{ fontSize: 12 }}> · {fmtDate(it.followup_at)}</Typography.Text>}
                              </div>
                            )}
                            {selectedStandardIds.includes(it.standard_id) && (
                              <Input.TextArea
                                style={{ marginTop: 8 }}
                                autoSize={{ minRows: 2, maxRows: 5 }}
                                maxLength={2000}
                                value={confirmationTexts[it.standard_id] ?? ''}
                                onChange={(event) => setConfirmationTexts(current => ({ ...current, [it.standard_id]: event.target.value }))}
                                placeholder="填写你作为负责人核对后的依据。这里记录销售确认，不会冒充AI确认或客户原话。"
                              />
                            )}
                          </div>
                        </Space>
                      </div>
                    ))}

                    {selectedStandardIds.length > 0 && (
                      <Space direction="vertical" style={{ width: '100%' }} size={8}>
                        <Typography.Text strong>本次负责人确认说明</Typography.Text>
                        <Input.TextArea
                          autoSize={{ minRows: 2, maxRows: 4 }}
                          maxLength={2000}
                          value={confirmationNote}
                          onChange={(event) => setConfirmationNote(event.target.value)}
                          placeholder="确认你已核对上方项目、当前环节、标准版本和依据。"
                        />
                        {(dossierFacts.contract_arrangement_status === 'unclear' || dossierFacts.contract_arrangement_status === 'explicitly_pending') && (
                          <Card size="small" title="档案冲突（可选更正；未选中的字段不会改动）">
                            <Typography.Text type="secondary">
                              当前合同安排：{String(dossierFacts.contract_arrangement_status)}。如需更正，请勾选对应字段，核对明确的更正前后值和原话来源。
                            </Typography.Text>
                            <Select
                              style={{ width: '100%', marginTop: 8 }}
                              value={correctionFollowupId || undefined}
                              placeholder="选择支持更正的本项目跟进"
                              onChange={setCorrectionFollowupId}
                              options={(data?.followups ?? []).map(f => ({ label: `${fmtDateTime(f.followed_at)} · ${f.content.slice(0, 80)}`, value: f.id }))}
                            />
                            <Space direction="vertical" style={{ width: '100%', marginTop: 8 }}>
                              <Checkbox checked={correctArrangement} onChange={e => setCorrectArrangement(e.target.checked)}>
                                同时更正字段 contract_arrangement_status
                              </Checkbox>
                              {correctArrangement && (
                                <>
                                  <Typography.Text>更正前：{String(dossierFacts.contract_arrangement_status)}；更正后：</Typography.Text>
                                  <Select
                                    style={{ width: '100%' }}
                                    value={arrangementAfter}
                                    onChange={setArrangementAfter}
                                    options={[
                                      { label: 'single · 单份合同', value: 'single' },
                                      { label: 'multiple · 多份分期合同', value: 'multiple' },
                                      { label: 'unclear · 尚不清楚', value: 'unclear' },
                                      { label: 'explicitly_pending · 明确待确认', value: 'explicitly_pending' },
                                      { label: 'not_discussed · 尚未讨论', value: 'not_discussed' },
                                    ]}
                                  />
                                  <Input.TextArea value={arrangementSourceQuote} onChange={e => setArrangementSourceQuote(e.target.value)} maxLength={2000} placeholder="填写所选跟进中可逐字核对的原文" />
                                </>
                              )}
                              <Checkbox checked={correctContractPlan} onChange={e => setCorrectContractPlan(e.target.checked)}>
                                同时更正字段 contract_plan
                              </Checkbox>
                              {correctContractPlan && (
                                <>
                                  <Typography.Text>更正前：{String(dossierFacts.contract_plan ?? '未记录')}；更正后：</Typography.Text>
                                  <Input.TextArea value={contractPlanAfter} onChange={e => setContractPlanAfter(e.target.value)} maxLength={2000} placeholder="填写明确的分期安排说明；未知的期数、金额和付款节点仍保持未知" />
                                  <Input.TextArea value={contractPlanSourceQuote} onChange={e => setContractPlanSourceQuote(e.target.value)} maxLength={2000} placeholder="填写所选跟进中可逐字核对的原文" />
                                </>
                              )}
                            </Space>
                          </Card>
                        )}
                      </Space>
                    )}

                    {judgeResult.warnings?.length ? (
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                         {judgeResult.warnings.join('；')}
                      </Typography.Text>
                    ) : null}
                  </Space>
                </div>
              )}
            </Space>
          </div>),
          }]} />
        )}

        {progress?.standards.length ? (
          <Space direction="vertical" size={0} style={{ width: '100%' }}>
            {/* 列头：与下面的行对齐（设计稿的「证据材料 / 完成时间」表头） */}
            <div style={{ display: 'flex', gap: 20, padding: '6px 0 4px' }}>
              <div style={{ flex: '1 1 auto', minWidth: 0 }} />
              <div style={{ flex: '0 0 200px', fontSize: 12, color: 'var(--text-muted)' }}>达成依据</div>
              <div style={{ flex: '0 0 110px', fontSize: 12, color: 'var(--text-muted)' }}>完成时间</div>
            </div>

            {progress.standards.map((item) => {
              const { standard, completion } = item;
              return (
                <div
                  key={standard.id}
                  ref={(node) => {
                    standardRefs.current[standard.id] = node;
                  }}
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 20,
                    padding: '14px 0',
                    borderTop: '1px solid var(--fill-soft)',
                    background:
                      !completion.is_done && standard.is_hard_gate ? 'var(--warning-soft)' : undefined,
                    borderRadius: 6,
                  }}
                >
                  {/* 左：勾选 + 标题 + 一行灰色描述 */}
                  <div style={{ flex: '1 1 auto', minWidth: 0, display: 'flex', gap: 10 }}>
                    <Checkbox
                      checked={completion.is_done}
                      disabled={readOnly}
                      style={{ marginTop: 3 }}
                      onChange={(e) => {
                        if (e.target.checked) {
                          openEvidence(item);
                        } else {
                          doToggle.mutate({ standardId: standard.id, isDone: false });
                        }
                      }}
                    />
                    <div style={{ minWidth: 0 }}>
                      <Space size={6} wrap>
                        {standard.is_hard_gate && <Tag color="red">硬门槛</Tag>}
                        {completion.source === 'ai_confirmed' && <Tag color="geekblue">AI 生成·已确认</Tag>}
                        <Typography.Text strong style={{ fontSize: 14 }}>
                          {standard.content}
                        </Typography.Text>
                      </Space>
                      <div>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {standard.evidence_hint || '需附证据：拜访记录 / 文档 / 截图 / 确认人+时间'}
                        </Typography.Text>
                      </div>
                      {!completion.is_done && standard.is_hard_gate && (
                        <div>
                          <Typography.Text style={{ fontSize: 12, color: WARNING }}>
                            硬门槛：未完成并附证据前，不能推进到下一环节
                          </Typography.Text>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* 中：达成依据列（已登记 = 依据文本 + 查看；未登记 = 登记按钮 + 一键按最近跟进） */}
                  <div style={{ flex: '0 0 200px', minWidth: 160, paddingTop: 2 }}>
                    {completion.evidence ? (
                      <Space size={6} align="start">
                        <FileTextOutlined style={{ color: BRAND, marginTop: 3 }} />
                        <div style={{ minWidth: 0 }}>
                          <Typography.Text
                            style={{ fontSize: 13, wordBreak: 'break-all', display: 'block' }}
                          >
                            {completion.evidence}
                          </Typography.Text>
                          <a onClick={() => setViewItem(item)}>查看</a>
                        </div>
                      </Space>
                    ) : (
                      <Space direction="vertical" size={4} style={{ width: '100%' }}>
                        <Tooltip
                          title={`判定依据（可来自结构化跟进信息，**不需要上传文件**）。参考：${standard.evidence_hint || '谁 + 何时 + 做了什么 + 有无确认人'}`}
                        >
                          <Button
                            size="small"
                            icon={<FormOutlined />}
                            disabled={readOnly}
                            style={{ borderColor: BRAND, color: BRAND, background: 'var(--bg-card)' }}
                            onClick={() => openEvidence(item)}
                          >
                            登记达成依据
                          </Button>
                        </Tooltip>
                        {recentFollowContent && (
                          <Tooltip
                            title={`用最近一条跟进内容作为依据：${recentFollowContent.slice(0, 40)}…`}
                          >
                            <a
                              style={{ fontSize: 12 }}
                              onClick={() => openEvidence(item, recentFollowContent)}
                            >
                              用最近跟进登记
                            </a>
                          </Tooltip>
                        )}
                      </Space>
                    )}
                  </div>

                  {/* 右：完成时间列 */}
                  <div style={{ flex: '0 0 110px', paddingTop: 2 }}>
                    {completion.is_done ? (
                      <div>
                        <Typography.Text style={{ fontSize: 13 }}>
                          {fmtDate(completion.done_at)}
                        </Typography.Text>
                        {completion.done_by_name && (
                          <div>
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                              {completion.done_by_name}
                            </Typography.Text>
                          </div>
                        )}
                      </div>
                    ) : (
                      <Typography.Text type="secondary">--</Typography.Text>
                    )}
                  </div>
                </div>
              );
            })}
          </Space>
        ) : (
          <NextStepGuide
            title="该环节未配置完成标准"
            description="后端「环节标准配置」里这个环节还没有标准卡，因此没有可勾选项。请管理员到 6.6 环节标准配置里补标准。"
            actions={
              canReview ? (
                <Button onClick={() => navigate('/admin/stage-standards')}>去配置环节标准</Button>
              ) : undefined
            }
          />
        )}

        {/*  琥珀门槛横幅：缺什么 + 为什么不能推进 + 就地补齐入口 */}
        {hasBlocker && (
          <GateBanner
            style={{ marginTop: 12 }}
            title={
              blockers[0].standardId
                ? `缺少《${blockers[0].title}》，暂不能推进。`
                : `缺少「${blockers[0].title}」，暂不能推进。`
            }
            items={blockers.map((b) => `${b.title}｜${b.reason}`)}
            action={
              <Space wrap>
                <Button type="primary" size="small" disabled={readOnly} onClick={goFixFirstBlocker}>
                  就地补齐并附证据
                </Button>
                <Typography.Text style={{ fontSize: 12, color: 'var(--warning-strong)' }}>
                  硬门槛未完成或证据缺失时系统会拒绝推进（409）；补齐后「推进至下一环节」自动可点。
                </Typography.Text>
              </Space>
            }
          />
        )}

        <div style={{ marginTop: 12 }}>
          <Footnote>
            报告须含痛点、场景、确认人、确认时间。完成标准的依据**优先由「按跟进信息自动判定」自动带出**；
            手工登记只是兜底（确实没写进跟进里时才用）。
          </Footnote>
        </div>

        {/* 底部操作行：左「回退环节」+ 右「推进至方案报价」 */}
        <div
          style={{
            marginTop: 14,
            paddingTop: 14,
            borderTop: '1px solid var(--fill-soft)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
            flexWrap: 'wrap',
          }}
        >
          <Tooltip
            title={
              readOnly
                ? '方案商务部门为只读视图，不能回退环节'
                : !progress
                  ? '该客户尚未进入闭环二'
                  : undefined
            }
          >
            <span>
              <Button
                disabled={readOnly || !progress || lead.status === 'signed' || leadLost}
                onClick={() => {
                  setMoveError('');
                  moveForm.resetFields();
                  setMoveType('rollback');
                }}
              >
                回退环节
              </Button>
            </span>
          </Tooltip>

          <Space wrap size={10} style={{ justifyContent: 'flex-end' }}>
            {advanceBlockedReason && !readOnly && (
              <Typography.Text style={{ fontSize: 12, color: WARNING }}>
                <WarningFilled /> {advanceBlockedReason}
              </Typography.Text>
            )}
            <Tooltip title={advanceBlockedReason || undefined}>
              <span>
                <Button
                  type="primary"
                  size="large"
                  disabled={readOnly || !progress || hasBlocker || !nextStageLabel || lead.status === 'signed'}
                  onClick={() => {
                    setMoveError('');
                    moveForm.resetFields();
                    setMoveType('advance');
                  }}
                >
                  {lead.status === 'signed' ? '已完成签约' : nextStageLabel ? `推进${nextStageLabel}` : '已到合同签约环节'}
                </Button>
              </span>
            </Tooltip>
          </Space>
        </div>
      </Card>
    </Space>
  );

  const amountTab = (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Row gutter={12}>
        <Col xs={12} md={8}>
          <KpiCard
            label="当前项目金额"
            value={fenToWan(lead.budget_amount_fen)}
            suffix="万元"
            icon={<WalletOutlined />}
            tone="brand"
            hint="当前项目金额保留申报、报价、中标或合同口径；不代表客户资金已落实。修正须留依据。"
          />
        </Col>
        <Col xs={12} md={8}>
          <KpiCard
            label="加权预测金额"
            value={fenToWan(lead.weighted_amount_fen)}
            suffix="万元"
            icon={<FundOutlined />}
            tone="purple"
            hint="加权预测 = 当前金额 × 该环节成交概率"
          />
        </Col>
        <Col xs={12} md={8}>
          <KpiCard
            label="成交概率"
            value={fmtPercent(lead.win_probability)}
            icon={<FundOutlined />}
            tone="green"
            hint="成交概率由当前环节决定（环节越靠后越高），不是人工估的"
          />
        </Col>
      </Row>
      <Space>
        <Button type="primary" disabled={readOnly} onClick={() => setAmountOpen(true)}>
          修正金额
        </Button>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          修正金额必须填写变更依据，系统会保留改前/改后/环节/人/时间留痕。
        </Typography.Text>
      </Space>
      <Table<AmountChange>
        rowKey="id"
        size="small"
        dataSource={data?.amount_changes ?? []}
        locale={{
          emptyText: (
            <NextStepGuide
              compact
              title="暂无金额变更记录"
              description="金额没改过就不会有留痕；客户预算变化时请点上方「修正金额」，系统会记下改前 / 改后 / 依据 / 人 / 时间。"
            />
          ),
        }}
        pagination={false}
        columns={[
          {
            title: '改前 → 改后',
            width: 220,
            render: (_, r) => (
              <Space size={4}>
                <span>{formatYuan(r.before_fen)}</span>
                <Typography.Text type="secondary">→</Typography.Text>
                <Typography.Text strong>{formatYuan(r.after_fen)}</Typography.Text>
              </Space>
            ),
          },
          {
            title: '变化额',
            dataIndex: 'delta_fen',
            width: 140,
            align: 'right',
            render: (v: number | null) =>
              v === null ? '-' : (
                <Typography.Text type={v >= 0 ? 'success' : 'danger'}>
                  {v >= 0 ? '+' : ''}
                  {formatYuan(v)}
                </Typography.Text>
              ),
          },
          { title: '依据', dataIndex: 'reason', ellipsis: true },
          { title: '环节', dataIndex: 'stage_label', width: 130, render: (v: string) => v || '-' },
          { title: '操作人', dataIndex: 'operator_name', width: 100, render: (v: string | null) => v || '-' },
          {
            title: '时间',
            dataIndex: 'created_at',
            width: 160,
            render: (v: string) => fmtDateTime(v),
          },
        ]}
      />
    </Space>
  );

  const followupTab = data?.followups?.length ? (
    <Timeline
      items={(data?.followups ?? []).map((f) => ({
        color: f.source === 'ai_confirmed' ? 'blue' : 'gray',
        children: (
          <Space direction="vertical" size={2}>
            <Space size={6} wrap>
              <Typography.Text strong>{f.user_name}</Typography.Text>
              <Tag>{f.method_label ?? methodMap[f.method] ?? f.method}</Tag>
              {f.stage_label && <Tag color="default">{f.stage_label}</Tag>}
              {f.source === 'ai_confirmed' && <Tag color="geekblue">AI 生成·已确认</Tag>}
              <Typography.Text type="secondary">{fmtDateTime(f.followed_at)}</Typography.Text>
            </Space>
            <Typography.Text>{f.content}</Typography.Text>
          </Space>
        ),
      }))}
    />
  ) : (
    <NextStepGuide
      title="暂无跟进记录"
      description="跟进记录在 1.2 线索详情的「跟进记录」里新增；录入后这里会自动出现，并可用于佐证完成标准的证据。"
      actions={<Button onClick={() => navigate(`/leads/${leadId}`)}>去线索详情新增跟进</Button>}
    />
  );

  const riskTab = (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Space>
        <Button type="primary" disabled={readOnly} onClick={() => setRiskOpen(true)}>
          登记风险
        </Button>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          R1–R5：资金预算不清 / 信任不到 / 决策链不清 / 客户需求不清 / 项目节奏不清
        </Typography.Text>
      </Space>
      <Table<RiskAlert>
        rowKey="id"
        size="small"
        dataSource={data?.risks ?? []}
        locale={{
          emptyText: (
            <NextStepGuide
              compact
              title="暂无风险"
              description="看项目觉得哪里不踏实就登记一条 R1–R5 风险；登记后风险中心会跟踪到解除为止。"
            />
          ),
        }}
        pagination={false}
        columns={[
          {
            title: '风险类型',
            dataIndex: 'risk_label',
            width: 160,
            render: (v: string, r) => <Tag color="volcano">{v || riskMap[r.risk_type] || r.risk_type}</Tag>,
          },
          { title: '描述', dataIndex: 'description', ellipsis: true },
          {
            title: '状态',
            dataIndex: 'status_label',
            width: 100,
            render: (v: string, r) => (
              <Tag color={r.status === 'resolved' ? 'success' : 'warning'}>{v || r.status}</Tag>
            ),
          },
          { title: '处理人', dataIndex: 'handler_name', width: 100, render: (v: string | null) => v || '-' },
          {
            title: '操作',
            width: 120,
            render: (_, r) =>
              r.status === 'resolved' ? (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {r.handle_note || '已处理'}
                </Typography.Text>
              ) : (
                <Button
                  type="link"
                  size="small"
                  disabled={readOnly}
                  onClick={() => {
                    setHandling(r);
                    handleForm.setFieldsValue({ handle_note: '' });
                  }}
                >
                  标记已处理
                </Button>
              ),
          },
        ]}
      />
    </Space>
  );

  const transitionTab = (
    <Table<StageTransition>
      rowKey="id"
      size="small"
      dataSource={data?.transitions ?? []}
      locale={{
        emptyText: (
          <NextStepGuide
            compact
            title="暂无环节流转记录"
            description="推进 / 回退环节后这里会逐条留痕（含上一环节停留天数与流转依据）。"
          />
        ),
      }}
      pagination={false}
      columns={[
        {
          title: '流转',
          width: 260,
          render: (_, r) => (
            <Space size={4}>
              <span>{r.from_stage_label || '—'}</span>
              <Typography.Text type="secondary">→</Typography.Text>
              <Typography.Text strong>{r.to_stage_label}</Typography.Text>
            </Space>
          ),
        },
        {
          title: '上一环节停留',
          dataIndex: 'stay_days',
          width: 120,
          render: (v: number) => `${v ?? 0} 天`,
        },
        { title: '依据', dataIndex: 'basis', ellipsis: true, render: (v: string) => v || '-' },
        { title: '操作人', dataIndex: 'operator_name', width: 100, render: (v: string | null) => v || '-' },
        {
          title: '时间',
          dataIndex: 'transition_at',
          width: 160,
          render: (v: string) => fmtDateTime(v),
        },
      ]}
    />
  );

  const reviewTab = (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Space wrap>
        {canReview && (
          <Button type="primary" onClick={() => navigate(`/reviews?lead_id=${leadId}`)}>
            填写评审卡
          </Button>
        )}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          评审卡 = 管理者 5 看逐项判定 + ⭐真执行判定（能力问题 / 态度问题 / 正常）
          {canReview ? '' : '；仅市场部管理人员与管理员可填写'}
        </Typography.Text>
      </Space>
      <Table<ProjectReview>
        rowKey="id"
        size="small"
        loading={reviewsQuery.isLoading}
        dataSource={reviewsQuery.data?.items ?? []}
        locale={{
          emptyText: (
            <NextStepGuide
              compact
              title="暂无评审记录"
              description={
                canReview
                  ? '上会 / 卡单项目建议留下评审记录，便于复盘辅导。'
                  : '评审卡由市场部管理人员与管理员填写；填完这里会自动显示。'
              }
              actions={
                canReview ? (
                  <Button size="small" onClick={() => navigate(`/reviews?lead_id=${leadId}`)}>
                    填写评审卡
                  </Button>
                ) : undefined
              }
            />
          ),
        }}
        pagination={false}
        expandable={{
          expandedRowRender: (r) => (
            <Table
              rowKey="key"
              size="small"
              pagination={false}
              dataSource={r.items}
              columns={[
                { title: '5 看', dataIndex: 'label', width: 200 },
                {
                  title: '结论',
                  dataIndex: 'result_label',
                  width: 110,
                  render: (v: string) => <Tag>{v || '-'}</Tag>,
                },
                { title: '备注 / 证据', dataIndex: 'note', render: (v: string) => v || '-' },
              ]}
            />
          ),
        }}
        columns={[
          {
            title: '评审时间',
            dataIndex: 'review_at',
            width: 150,
            render: (v: string) => fmtDateTime(v),
          },
          { title: '评审人', dataIndex: 'reviewer_name', width: 100 },
          { title: '环节', dataIndex: 'stage_label', width: 140 },
          {
            title: '真执行判定',
            dataIndex: 'verdict',
            width: 120,
            render: (v: string, r) => (
              <Tag color={VERDICT_COLOR[v] ?? 'default'}>{r.verdict_label || v}</Tag>
            ),
          },
          {
            title: '综合结论',
            dataIndex: 'conclusion',
            ellipsis: true,
            render: (v: string | null) => v || '-',
          },
          {
            title: '下次评审',
            dataIndex: 'next_review_at',
            width: 120,
            render: (v: string | null) => fmtDate(v),
          },
          {
            title: '备注',
            dataIndex: 'remark',
            width: 180,
            ellipsis: true,
            render: (v: string | null) => v || '-',
          },
        ]}
      />
    </Space>
  );


  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        /* 标题口径：客户名 / 项目名（project_name 为空时退回只显示客户名） */
        title={pageTitle}
        tags={
          <Space size={6} wrap>
            <LeadStatusTag status={lead.status} />
            <Tag color="blue">{lead.current_stage_label || '未进入闭环二'}</Tag>
            {lead.status !== 'signed' && lead.stage_overdue && <Tag color="red">卡单</Tag>}
            {lead.status !== 'signed' && !leadLost && lead.rhythm_clear === false && <Tag>下一节点安排待确认</Tag>}
          </Space>
        }
        meta={[
          {
            label: '客户',
            value: siblingCustomer ? (
              <a onClick={() => navigate(`/customers/${siblingCustomer.id}`)}>
                {lead.name}（查看客户全景）
              </a>
            ) : (
              lead.name
            ),
          },
          ...(lead.project_name ? [{ label: '项目', value: lead.project_name }] : []),
          { label: '行业', value: lead.org_type_label || lead.org_type || '未填' },
          {
            label: '区域',
            value: [lead.province, lead.city, lead.district].filter(Boolean).join(' ') || '未填',
          },
          { label: '负责人', value: lead.owner_name || '未指派' },
          { label: '创建时间', value: fmtDate(lead.created_at) },
        ]}
        actions={
          <Space wrap>
            {user?.id === lead.owner_id && !leadLost && lead.status !== 'signed' && (
              <Button type="primary" onClick={() => navigate(`/ai/chat?lead=${leadId}`)}>
                记录跟进 / 获取建议
              </Button>
            )}
            {user?.id === lead.owner_id && lead.current_stage === 's7_contract_signed' && lead.status !== 'signed' && !leadLost && (
              <SignOpportunity leadId={leadId} amountFen={lead.budget_amount_fen || 0} />
            )}
            <Button onClick={() => navigate(`/leads/${leadId}/edit`)}>编辑商机</Button>
            <Dropdown
              trigger={['click']}
              menu={{
                items: [
                  ...(canReview && !leadLost
                    ? [{ key: 'review', label: '填写评审卡' }]
                    : []),
                  ...(!readOnly && !leadLost && lead.status !== 'signed'
                    ? [{ key: 'lose', label: '登记丢单', danger: true }]
                    : []),
                  ...(leadLost ? [{ key: 'revert', label: '撤销丢单' }] : []),
                  { type: 'divider' as const },
                  { key: 'back', label: '返回商机列表' },
                ],
                onClick: ({ key }) => {
                  if (key === 'review') navigate(`/reviews?lead_id=${leadId}`);
                  if (key === 'lose') {
                    loseForm.resetFields();
                    setLoseOpen(true);
                  }
                  if (key === 'revert') {
                    revertForm.setFieldsValue({ remark: '' });
                    setRevertOpen(true);
                  }
                  if (key === 'back') navigate('/opportunities');
                },
              }}
            >
              <Button icon={<MoreOutlined />} aria-label="更多操作" />
            </Dropdown>
          </Space>
        }
      />

      {leadLost && (
        <Alert
          type="error"
          showIcon
          message={
            <Space wrap>
              <Tag color="default">已丢单</Tag>
              <span>丢单原因：{lead.lost_reason_labels?.join('、') || '—'}</span>
              <span>丢单时间：{fmtDateTime(lead.lost_at)}</span>
            </Space>
          }
          description={`丢单备注：${lead.lost_remark || '—'}（误判可点右上角「撤销丢单」恢复）`}
        />
      )}
      {readOnly && (
        <Alert type="info" showIcon message="方案商务部门为只读视图（只涉及合同相关字段）" />
      )}

      {dossierQuery.data && dossierQuery.data.revision !== null && (
        <Card size="small" title="项目档案 · 最新事实" extra={<Button type="link" onClick={() => setDossierOpen(true)}>查看记录与证据</Button>}>
          <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }}>
            <Descriptions.Item label="客户需求">{String(dossierFacts.need || '待确认')}</Descriptions.Item>
            <Descriptions.Item label="建设范围">{String(dossierFacts.scope || '待确认')}</Descriptions.Item>
            {Boolean(dossierFacts.implementation_progress) && <Descriptions.Item label="方案与材料进度">{String(dossierFacts.implementation_progress)}</Descriptions.Item>}
            <Descriptions.Item label="下一节点">{String(dossierFacts.next_node || '待安排')}</Descriptions.Item>
            <Descriptions.Item label="预计时间">{String(dossierFacts.next_node_time || '待确认')}</Descriptions.Item>
            <Descriptions.Item label="资金来源">{String(dossierFacts.funding_source || '待确认')}</Descriptions.Item>
            <Descriptions.Item label="采购进展">{String(dossierFacts.procurement_step || '待确认')}</Descriptions.Item>
            {Boolean(dossierFacts.project_amount_basis) && <Descriptions.Item label="项目金额口径">{({ application_plan: '申报规模（待审批）', customer_budget: '客户预算', quote: '方案报价', award: '中标金额', contract: '合同金额' } as Record<string, string>)[String(dossierFacts.project_amount_basis)] || String(dossierFacts.project_amount_basis)}</Descriptions.Item>}
            {typeof dossierFacts.proposal_amount_fen === 'number' && <Descriptions.Item label="方案报价">{fenToWan(dossierFacts.proposal_amount_fen)} 万元</Descriptions.Item>}
            {(dossierFacts.budget_range || typeof dossierFacts.budget_amount_fen === 'number') && <Descriptions.Item label="客户预算">{String(dossierFacts.budget_range || `${fenToWan(dossierFacts.budget_amount_fen as number)} 万元`)}</Descriptions.Item>}
            {typeof dossierFacts.available_amount_fen === 'number' && <Descriptions.Item label="已确认可用资金">{fenToWan(dossierFacts.available_amount_fen)} 万元</Descriptions.Item>}
            {Boolean(dossierFacts.budget_plan) && <Descriptions.Item label="预算申报计划">{String(dossierFacts.budget_plan)}</Descriptions.Item>}
            {typeof dossierFacts.contract_amount_fen === 'number' && <Descriptions.Item label="合同金额">{fenToWan(dossierFacts.contract_amount_fen)} 万元</Descriptions.Item>}
          </Descriptions>
          {dossierQuery.data.record?.customer_feedback && <Typography.Text type="secondary">最近客户反馈：{dossierQuery.data.record.customer_feedback}</Typography.Text>}
        </Card>
      )}

      {/* 保留原有四个指标卡；已签约只展示历史耗时。 */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'stretch' }}>
        <MetricTile
          label="当前环节"
          value={progress?.stage_label || lead.current_stage_label || '未进入闭环二'}
          valueColor={BRAND}
          hint="商机共 7 个环节（一 有效商机 … 七 合同签约），口径见 spec/03 §2；环节由系统按完成标准与硬门槛判定"
        />
        <MetricTile
          label={lead.status === 'signed' ? '签约阶段耗时' : '停留时长'}
          value={stayDays == null ? '待确认' : `${stayDays}`}
          unit={stayDays == null ? '' : '自然日'}
          valueColor={overdueDays > 0 ? WARNING : undefined}
          badge={
            overdueDays > 0 ? (
              <Pill>
                <ExclamationCircleFilled /> 超期 {overdueDays} 天
              </Pill>
            ) : (
              <Pill tone="gray">{lead.status === 'signed' ? '已结束·停止计时' : '未超期'}</Pill>
            )
          }
          hint={`停留时长按自然日计时（含周末与节假日）；基准 ${baseDays} 天来自该环节的标准卡配置，超出即判卡单`}
        />
        <MetricTile
          label="当前金额"
          value={`¥ ${num(fenToWan(lead.budget_amount_fen))}`}
          unit="万"
          hint="当前项目金额；具体性质见项目档案，不等同于已获批预算。修正须留依据。"
        />
        <MetricTile
          label="加权预测"
          value={`¥ ${num(fenToWan(lead.weighted_amount_fen))}`}
          unit="万"
          hint={`加权预测 = 当前金额 × 该环节成交概率（本环节 ${fmtPercent(lead.win_probability)}，由环节决定、非人工估）`}
        />
      </div>

      {/*  7 环节箭头式步骤条（每个环节都可点：查看该环节的完成标准与达成依据） */}
      <Card size="small" styles={{ body: { padding: 0 } }}>
        <StageArrowBar
          current={lead.current_stage ?? progress?.stage ?? null}
          activeStage={stageDrawer}
          onSelect={(code) => setStageDrawer(code)}
        />
      </Card>

      {/* 点步骤条打开的环节详情抽屉（只读） */}
      <StageEvidenceDrawer
        leadId={leadId}
        stage={stageDrawer}
        open={!!stageDrawer}
        currentStageCode={(lead.current_stage ?? progress?.stage ?? '').toLowerCase()}
        onClose={() => setStageDrawer(null)}
      />

      <Row gutter={12} className="opportunity-panels">
        <Col xs={24} lg={16} xl={17}>
          <Card>
            <Tabs
              activeKey={activeTab}
              onChange={setActiveTab}
              tabBarStyle={{ marginBottom: 12 }}
              items={[
                { key: 'progress', label: '环节进度', children: <div className="opportunity-tab-body">{progressTab}</div> },
                { key: 'amount', label: '金额与变更', children: <div className="opportunity-tab-body">{amountTab}</div> },
                { key: 'followups', label: '跟进记录', children: <div className="opportunity-tab-body">{followupTab}</div> },
                { key: 'risks', label: '风险', children: <div className="opportunity-tab-body">{riskTab}</div> },
                { key: 'reviews', label: '评审记录', children: <div className="opportunity-tab-body">{reviewTab}</div> },
                { key: 'transitions', label: '环节流转', children: <div className="opportunity-tab-body">{transitionTab}</div> },
              ]}
            />
          </Card>
        </Col>

        {/* 右栏固定窗口：行动在上、建议摘要在下。 */}
        <Col xs={24} lg={8} xl={7}>
          <Space direction="vertical" size={16} className="opportunity-side" style={{ width: '100%' }}>
            <SalesActions leadId={leadId} ownerId={lead.owner_id} active={!leadLost && lead.status !== 'signed'} />
            <Card size="small" title="销售专家建议" className="opportunity-advice-card">
              <AiDisclaimer />
              {diagnosis ? (
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <Space wrap size={6}>
                    <Tag color={PARSE_MODE_COLOR[diagnosis.parse_mode] ?? 'default'}>
                      {PARSE_MODE_LABEL[diagnosis.parse_mode] ?? diagnosis.parse_mode}
                    </Tag>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {fmtDateTime(diagnosis.created_at)}
                    </Typography.Text>
                  </Space>
                  <Typography.Paragraph ellipsis={{ rows: 5 }} style={{ whiteSpace: 'pre-wrap' }}>{diagnosis.content}</Typography.Paragraph>
                </Space>
              ) : (
                <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                  还没生成建议。点「帮我分析」，系统会按当前环节、完成标准与风险给一份可核对的草稿建议；
                  建议只是草稿，可以忽略或自己改，它不作为考核依据。
                </Typography.Text>
              )}
              <div style={{ marginTop: 12 }}>
                <Space wrap>
                  <Button
                    type="primary"
                    size="small"
                    icon={<RobotOutlined />}
                    loading={doDiagnose.isPending}
                    onClick={() => {
                      setDiagOpen(true);
                      doDiagnose.mutate();
                    }}
                  >
                    帮我分析
                  </Button>
                  {diagnosis && <Button size="small" onClick={() => setDiagOpen(true)}>查看详情</Button>}
                  <a onClick={() => navigate('/ai/diagnoses')}>查看全部历史</a>
                </Space>
              </div>
            </Card>

          </Space>
        </Col>
      </Row>


      {/*  同客户其他项目（客户-项目两层模型）：一个客户下多个项目可在此直接切换 */}
      <Card
        size="small"
        title={
          <Space size={8} wrap>
            <span>同客户其他项目</span>
            {siblingCustomer && (
              <Typography.Text type="secondary" style={{ fontSize: 13, fontWeight: 400 }}>
                {siblingCustomer.name} 名下共 {siblingCustomer.project_count} 个项目
              </Typography.Text>
            )}
          </Space>
        }
        extra={
          siblingCustomer ? (
            <Space size={12} wrap>
              <a onClick={() => navigate(`/customers/${siblingCustomer.id}`)}>查看客户全景 ›</a>
              <Tooltip title="客户名下每个项目各自走一遍 7 环节、各有金额；切换项目不会影响各项目的环节进度">
                <Typography.Text type="secondary" style={{ fontSize: 12, cursor: 'help' }}>
                  ⓘ 同一客户 · 不同项目
                </Typography.Text>
              </Tooltip>
            </Space>
          ) : undefined
        }
        styles={{ body: { paddingTop: 12 } }}
      >
        {siblingsQuery.isLoading ? (
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            正在读取该客户的其他项目…
          </Typography.Text>
        ) : siblingsQuery.isError ? (
          <GateBanner
            tone="tip"
            title="同客户其他项目没能读出来（接口 /customers/of-lead 返回失败）"
            items={[
              '原因：本次请求失败（网络或服务异常），与当前商机数据无关。',
              '要看同一客户的其他项目：去商机列表按客户名「' + lead.name + '」搜索，或稍后重试。',
            ]}
            action={
              <Space wrap>
                <Button size="small" onClick={() => siblingsQuery.refetch()}>
                  重试
                </Button>
                <Button
                  size="small"
                  type="primary"
                  onClick={() => navigate(`/opportunities?keyword=${encodeURIComponent(lead.name)}`)}
                >
                  去商机列表搜索该客户
                </Button>
              </Space>
            }
          />
        ) : siblings.length ? (
          <Row gutter={[12, 12]}>
            {siblings.map((p: CustomerProject) => (
              <Col xs={24} md={12} xl={8} key={p.id}>
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => navigate(`/opportunities/${p.id}`)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') navigate(`/opportunities/${p.id}`);
                  }}
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: 8,
                    padding: '12px 14px',
                    cursor: 'pointer',
                    height: '100%',
                    background: 'var(--bg-card)',
                  }}
                >
                  <Space direction="vertical" size={4} style={{ width: '100%' }}>
                    <Typography.Text strong ellipsis style={{ maxWidth: '100%' }}>
                      {p.project_name?.trim() ? `${p.name} / ${p.project_name}` : p.name}
                    </Typography.Text>
                    <Space size={6} wrap>
                      <Tag color="blue">{p.current_stage_label || '未进入闭环二'}</Tag>
                      <Tag color={p.value_label === '已签约' ? 'success' : p.value_label === '已丢单' ? 'default' : 'blue'}>
                        {p.value_label || (p.is_process_value ? '过程中价值' : '已签约')}
                      </Tag>
                    </Space>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      ¥{fenToWan(p.amount_fen)} 万 · 负责人 {p.owner_name || '未指派'}
                      {p.status_label ? ` · ${p.status_label}` : ''}
                    </Typography.Text>
                  </Space>
                </div>
              </Col>
            ))}
          </Row>
        ) : (
          /* 只有这一个项目时给一行紧凑说明，不再占一大块高度（7 环节步骤条才是这一页的主角） */
          <Space size={10} wrap>
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              该客户名下目前只有这一个项目。同一客户（学校 / 教育局）可以有多个项目商机——不同预算年度、不同资金来源、
              不同功能点各算一个项目；新增项目走「新增线索」，同一客户名会自动归集到同一个客户下。
            </Typography.Text>
            <Space size={8}>
              <Button size="small" onClick={() => navigate('/leads/new')}>
                新增线索（新项目）
              </Button>
              <Button size="small" onClick={() => navigate('/opportunities')}>
                去商机列表
              </Button>
            </Space>
          </Space>
        )}
      </Card>



      <Footnote>
        完成标准必须登记达成依据（可用跟进信息，不需要上传文件）（勾选即要求填写证据，后端也会校验）；硬门槛未完成不允许推进到下一环节（后端返回 409，
        本页已提前用琥珀横幅列出缺口与补齐入口）。停留时长按自然日计时，首次跟进按工作日计时。
        「加权预测 = 金额 × 该环节成交概率」；AI 建议为草稿，仅供参考、不作为考核依据。
      </Footnote>

      {/* 查看已登记的达成依据 */}
      <Modal
        open={!!viewItem}
        title="查看达成依据"
        width={560}
        onCancel={() => setViewItem(null)}
        footer={
          <Space>
            <Button onClick={() => setViewItem(null)}>关闭</Button>
            <Button
              type="primary"
              disabled={readOnly}
              onClick={() => {
                const it = viewItem;
                setViewItem(null);
                if (it) openEvidence(it);
              }}
            >
              修正依据
            </Button>
          </Space>
        }
      >
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Typography.Text strong>{viewItem?.standard.content}</Typography.Text>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              达成依据（来自结构化跟进信息）
            </Typography.Text>
            <div>
              <Space size={6} align="start">
                <PaperClipOutlined style={{ color: BRAND, marginTop: 3 }} />
                <Typography.Text style={{ wordBreak: 'break-all' }}>
                  {viewItem?.completion.evidence}
                </Typography.Text>
              </Space>
            </div>
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            完成时间：{fmtDate(viewItem?.completion.done_at)} · 完成人：
            {viewItem?.completion.done_by_name || '—'}
          </Typography.Text>
          {viewItem && !viewItem.completion.is_done && (
            <Alert
              type="info"
              showIcon
              message="这条标准还没勾选完成：补齐内容后可点下方「修正证据」，在弹窗里勾选完成（勾选必须附证据）。"
            />
          )}
        </Space>
      </Modal>

      {/* 勾选证据弹窗：勾选完成标准必须填证据 */}
      <Modal
        open={!!evidenceItem}
        title="填写证据"
        okText="提交"
        cancelText="取消"
        confirmLoading={doToggle.isPending}
        onCancel={() => {
          setEvidenceItem(null);
          evidenceForm.resetFields();
        }}
        onOk={async () => {
          const v = await evidenceForm.validateFields();
          if (!evidenceItem) return;
          doToggle.mutate({
            standardId: evidenceItem.standard.id,
            isDone: true,
            evidence: v.evidence.trim(),
          });
        }}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="勾选完成标准必须登记达成依据（可用跟进信息，不需要上传文件）"
          description={`达成依据（可来自结构化跟进信息，不需要上传文件）。参考：${evidenceItem?.standard.evidence_hint || '谁 + 何时 + 做了什么 + 有无确认人'}`}
        />
        <Typography.Paragraph>{evidenceItem?.standard.content}</Typography.Paragraph>
        <Form form={evidenceForm} layout="vertical">
          <Form.Item
            label="证据"
            name="evidence"
            rules={[{ required: true, message: '请填写证据后再勾选' }]}
          >
            <Input.TextArea rows={3} placeholder="如：2026-09-30 上门拜访刘主任，现场确认方案" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 推进 / 回退弹窗 */}
      <Modal
        open={!!moveType}
        title={moveType === 'advance' ? '推进到下一环节' : '回退上一环节'}
        okText="提交"
        cancelText="取消"
        okButtonProps={{ disabled: moveType === 'advance' && hasBlocker }}
        confirmLoading={doMove.isPending}
        onCancel={() => {
          setMoveType(null);
          setMoveError('');
        }}
        onOk={async () => {
          const v = await moveForm.validateFields();
          if (!moveType) return;
          doMove.mutate({ type: moveType, basis: v.basis.trim() });
        }}
      >
        {moveType === 'advance' && hasBlocker && (
          <GateBanner
            style={{ marginBottom: 12 }}
            title="还有缺口未补齐，提交会被系统拒绝（409）"
            items={blockers.map((b) => `${b.title}｜${b.reason}`)}
            action={
              <Button
                size="small"
                onClick={() => {
                  setMoveType(null);
                  goFixFirstBlocker();
                }}
              >
                去补齐
              </Button>
            }
          />
        )}
        {moveError && (
          <Alert type="error" showIcon style={{ marginBottom: 12 }} message="操作未通过" description={moveError} />
        )}
        <Form form={moveForm} layout="vertical">
          <Form.Item
            label="流转依据"
            name="basis"
            rules={[{ required: true, message: '请填写流转依据' }]}
          >
            <Input.TextArea rows={3} placeholder="如：已完成《客户需求分析报告》并获客户确认" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 金额修正弹窗 */}
      <Modal
        open={amountOpen}
        title="修正金额"
        okText="提交"
        cancelText="取消"
        confirmLoading={doAmount.isPending}
        onCancel={() => {
          setAmountOpen(false);
          amountForm.resetFields();
        }}
        onOk={async () => {
          const v = await amountForm.validateFields();
          const fen = yuanToFen(v.amount_yuan);
          if (fen === undefined) {
            message.error('请输入有效金额');
            return;
          }
          doAmount.mutate({ amountFen: fen, reason: v.reason.trim() });
        }}
      >
        <Form
          form={amountForm}
          layout="vertical"
          initialValues={{ amount_yuan: fenToYuanNumber(lead.budget_amount_fen) }}
        >
          <Form.Item
            label="修正后金额（元）"
            name="amount_yuan"
            rules={[{ required: true, message: '请输入金额' }]}
          >
            <InputNumber min={0} precision={2} style={{ width: '100%' }} placeholder="如 300000" />
          </Form.Item>
          <Form.Item
            label="变更依据（必填）"
            name="reason"
            rules={[{ required: true, message: '修正金额必须填写变更依据' }]}
          >
            <Input.TextArea rows={3} placeholder="如：客户明确本期预算 30 万，先做 AI 阅卷单模块" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 登记风险弹窗 */}
      <Modal
        open={riskOpen}
        title="登记风险（R1–R5 可多选）"
        okText="提交"
        cancelText="取消"
        confirmLoading={doAddRisk.isPending}
        onCancel={() => {
          setRiskOpen(false);
          riskForm.resetFields();
        }}
        onOk={async () => {
          const v = await riskForm.validateFields();
          doAddRisk.mutate({ risk_types: v.risk_types, description: v.description ?? '' });
        }}
      >
        <Form form={riskForm} layout="vertical">
          <Form.Item
            label="风险类型"
            name="risk_types"
            rules={[{ required: true, message: '请至少选择一类风险' }]}
          >
            <Select mode="multiple" options={riskOptions} placeholder="多选 R1–R5" />
          </Form.Item>
          <Form.Item label="风险描述" name="description">
            <Input.TextArea rows={3} placeholder="如：未拿到关键决策人联系方式，一直等校长定" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 处理风险弹窗 */}
      <Modal
        open={!!handling}
        title="标记风险已处理"
        okText="确认"
        cancelText="取消"
        confirmLoading={doHandleRisk.isPending}
        onCancel={() => setHandling(null)}
        onOk={async () => {
          const v = await handleForm.validateFields();
          if (!handling) return;
          doHandleRisk.mutate({ riskId: handling.id, note: v.handle_note?.trim() || '已处理' });
        }}
      >
        <Typography.Paragraph type="secondary">
          {handling?.risk_label}：{handling?.description}
        </Typography.Paragraph>
        <Form form={handleForm} layout="vertical">
          <Form.Item label="处理说明" name="handle_note">
            <Input.TextArea rows={3} placeholder="如：已通过校长办公室电话确认预算与时间" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 登记丢单弹窗（必须多选 R1–R5） */}
      <Modal
        open={loseOpen}
        title="登记丢单"
        okText="确认丢单"
        okButtonProps={{ danger: true }}
        cancelText="取消"
        confirmLoading={doLose.isPending}
        onCancel={() => {
          setLoseOpen(false);
          loseForm.resetFields();
        }}
        onOk={async () => {
          const v = await loseForm.validateFields();
          doLose.mutate({ reasons: v.lost_reason_types, remark: v.remark?.trim() ?? '' });
        }}
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="登记丢单后该商机状态变为「已丢单」，并从漏斗中剔除"
          description="丢单原因必须从 R1–R5 中多选（这是丢单原因分布的唯一数据来源）；误判可撤销。"
        />
        <Form form={loseForm} layout="vertical">
          <Form.Item
            label="丢单原因（R1–R5，可多选）"
            name="lost_reason_types"
            rules={[{ required: true, message: '请至少选择 1 个丢单原因' }]}
          >
            <Select mode="multiple" options={riskOptions} placeholder="至少选择 1 个原因" />
          </Form.Item>
          <Form.Item label="丢单备注" name="remark">
            <Input.TextArea rows={3} placeholder="如：预算被砍，本年度不再启动" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 撤销丢单弹窗 */}
      <Modal
        open={revertOpen}
        title="撤销丢单"
        okText="确认撤销"
        cancelText="取消"
        confirmLoading={doRevertLoss.isPending}
        onCancel={() => {
          setRevertOpen(false);
          revertForm.resetFields();
        }}
        onOk={async () => {
          const v = await revertForm.validateFields();
          doRevertLoss.mutate(v.remark?.trim() ?? '');
        }}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="撤销后商机恢复为跟进中（或有效商机），丢单原因与备注清空，系统保留操作日志"
        />
        <Form form={revertForm} layout="vertical">
          <Form.Item label="撤销原因 / 备注" name="remark">
            <Input.TextArea rows={3} placeholder="如：客户重新启动预算，实际未丢单" />
          </Form.Item>
        </Form>
      </Modal>

      <ProjectDossierDrawer leadId={leadId} open={dossierOpen} onClose={() => setDossierOpen(false)} />

      {/* AI分析详情在当前商机右侧查看。 */}
      <Drawer
        open={diagOpen}
        width={760}
        footer={null}
        title={`AI 诊断 · ${lead.name}`}
        onClose={() => setDiagOpen(false)}
      >
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <AiDisclaimer text="AI 诊断仅供参考 · 不作为考核依据，可忽略或自行修正" />
        </Space>
        {doDiagnose.isPending && <Spin />}
        {!doDiagnose.isPending && diagnosis && (
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            <Space>
              <Tag color={PARSE_MODE_COLOR[diagnosis.parse_mode] ?? 'default'}>
                {PARSE_MODE_LABEL[diagnosis.parse_mode] ?? diagnosis.parse_mode}
              </Tag>
              <Typography.Text type="secondary">{fmtDateTime(diagnosis.created_at)}</Typography.Text>
              <a onClick={() => navigate('/ai/diagnoses')}>查看全部历史</a>
            </Space>
            <Divider style={{ margin: '4px 0' }} />
            <AiContent content={diagnosis.content} />
            <DiagnosisActions diagnosis={diagnosis} canWrite={user?.id === lead.owner_id && !readOnly && !leadLost && lead.status !== 'signed'} />
          </Space>
        )}
        {!doDiagnose.isPending && !diagnosis && (
          <NextStepGuide compact title="暂无诊断结果" description="点「帮我分析」生成一份可核对的草稿建议。" />
        )}
      </Drawer>
    </Space>
  );
}
