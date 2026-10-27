import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  Alert,
  Avatar,
  Button,
  Card,
  Collapse,
  Input,
  List,
  Space,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  CheckCircleOutlined,
  PlusOutlined,
  QuestionCircleOutlined,
  RobotOutlined,
  SendOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from '@ant-design/icons';
import {
  PARSE_MODE_COLOR,
  PARSE_MODE_LABEL,
  aiDiagnose,
  aiIntake,
  aiStatus,
  confirmDraft,
  ignoreDraft,
  listDrafts,
  type AiDiagnosis,
  type AiDraft,
} from '@/api/ai';
import { describeError } from '@/api/client';
import PageHeader from '@/components/PageHeader';
import AiContent from '@/components/AiContent';
import LeadSelect from '@/components/LeadSelect';
import GateBanner from '@/components/GateBanner';
import { AiDisclaimer, Footnote, NextStepGuide, PanelFlag } from '@/components/Guide';
import { AiDraftPanel } from '@/pages/ai/AiDrafts';
import { AiDiagnosisSections } from '@/pages/ai/AiDiagnoses';
import { useDicts } from '@/hooks/useDicts';
import { fmtDate, toLabelMap } from '@/utils/format';
import dayjs from 'dayjs';
import { getOpportunityDetail } from '@/api/opportunities';
import './AiChat.css';
import { DiagnosisActions } from '@/components/SalesActions';
import { useAuthStore } from '@/store/auth';

const EXAMPLE =
  '今天拜访了XX三中，见了信息中心刘主任，汇报了AI阅卷方案，他表示认可但要等校长定；需求是AI阅卷+智慧课堂，预算大概30万，等秋季学期';

const EXAMPLE_QUESTION = '这个项目为什么卡住？下一步我该做什么？';

/** 会话列表时间：今天显示时分，跨天显示日期（与设计稿一致） */
function sessionTime(v: string): string {
  return dayjs(v).isSame(dayjs(), 'day') ? `今天 ${dayjs(v).format('HH:mm')}` : dayjs(v).format('MM-DD');
}

/**
 * 从诊断 Markdown 里抽出「结论引用 + 关键建议」。
 *
 * 只用于**旧数据兜底**（structured 为空的记录）：新记录的结论/建议走 `AiDiagnosisSections` 的结构化字段。
 * 引用挑选口径：优先含「结论」的行，否则取最长的一段（避免把「**当前状态**：…」这种小标题误当结论）。
 */
function splitDiagnosis(content?: string | null): { quote: string; suggestions: string[] } {
  const lines = (content ?? '')
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);
  const suggestions: string[] = [];
  const paragraphs: string[] = [];
  lines.forEach((line) => {
    if (/^#{2,4}\s*(.+)$/.test(line)) return;
    const bullet = /^[-*]\s+(.+)$/.exec(line) ?? /^\d+[.、]\s*(.+)$/.exec(line);
    if (bullet) {
      suggestions.push(bullet[1].replace(/\*\*/g, ''));
      return;
    }
    paragraphs.push(line.replace(/\*\*/g, ''));
  });
  const byConclusion = paragraphs.find((p) => p.includes('结论'));
  const longest = paragraphs.slice().sort((a, b) => b.length - a.length)[0] ?? '';
  return { quote: byConclusion ?? longest, suggestions: suggestions.slice(0, 3) };
}

/**
 * 错误/不可用区块：**一律写清「为什么 + 怎么办」**（判据 C2），
 * 绝不用「假装成功」的方式掩盖 AI 失败（本仓不做规则降级：503 就是 503）。
 */
function AiErrorPanel({
  title,
  error,
  onRetry,
  retryText = '重试',
  extraActions,
}: {
  title: string;
  error: { status?: number; detail: string };
  onRetry?: () => void;
  retryText?: string;
  extraActions?: React.ReactNode;
}) {
  const is503 = error.status === 503;
  const isTimeout = !error.status;
  return (
    <Alert
      type={is503 ? 'error' : 'warning'}
      showIcon
      message={`${title}：${error.detail}`}
      description={
        <Space direction="vertical" size={6} style={{ width: '100%' }}>
          <Typography.Text style={{ fontSize: 12 }}>
            <b>为什么</b>：
            {is503
              ? '模型侧不可用（未配置密钥 / provider 未激活 / 模型调用失败）。本系统不做规则降级解析 —— 与其给你一份"看起来能用"的假草稿，不如明确失败。'
              : isTimeout
                ? '请求没在约定时间内拿到结果（AI 诊断实测常在 30 秒以上，前端已放到 180 秒长超时）。'
                : '后端返回了业务错误，原文见上。'}
          </Typography.Text>
          <Typography.Text style={{ fontSize: 12 }}>
            <b>怎么办</b>：
            {is503
              ? '① 稍后重试（模型偶发抖动）；② 一直失败就去「系统设置 → 适配配置」检查模型密钥，或联系系统管理员；③ 你刚输入的消息还在输入框里，不会被清空。'
              : isTimeout
                ? '点重试再发一次；若反复超时，可把消息拆短（客户 + 对象 + 结论 + 金额 + 时间）后重发。'
                : '按上面的提示修正后重试；你的输入内容不会丢。'}
          </Typography.Text>
          <Space wrap>
            {onRetry && (
              <Button size="small" type="primary" onClick={onRetry}>
                {retryText}
              </Button>
            )}
            <Button size="small" onClick={() => window.open('/admin/adapters', '_self')}>
              去适配配置看模型状态
            </Button>
            {extraActions}
          </Space>
        </Space>
      }
      style={{ marginBottom: 10 }}
    />
  );
}

/**
 * 5.1 对话与录入（设计稿 05）
 *
 * 我从设计稿 05 上看到的结构：**左（会话列表）/ 中（对话 + AI 专家诊断）/ 右（更新草稿）**三栏；
 * 中间是「我的消息 → AI 回『已生成更新草稿，请核对后确认』」；右栏从上到下是
 * 客户信息（客户名 + 匹配度高）/ 变更内容对比（字段｜当前值｜拟更新）/ 更新依据 /
 * 标准动作建议 + 相关证据 / 需求分析报告·下一步见面「未满足·保持未完成」+ 下一步 /
 * 当前阶段（保持不变）+「草稿确认不锁定证据与环节硬门槛」/ 查看原始消息内容 / 忽略·修正·确认并写入。
 *
 * 判据对照：
 * - C4 赋能边界：右侧「更新草稿」给出**字段级变更对照 + 更新依据 + 标准证据 + 原始消息出处**，
 *                可忽略 / 可修正 / 确认后才写入；AI 诊断块与面板都带免责标识，AI 不直接改数据。
 * - C2 少判断：AI 不可用时讲清「为什么不可用 + 怎么办（去哪配 / 怎么重试）」；
 *               没有会话时给「下一步发什么」与示例；诊断按钮不可用时就地说明原因。
 * - C3 口径可见：匹配度、时间承诺、解析方式都写明含义；**不展示任何 AI 命中率/成功率**。
 * - C1 少填：消息直接生成草稿；草稿生成后可一键跳 5.2 确认；会话列表点开即回看。
 * ══════════════════════════════════════════════════════════════════════ */
export default function AiChat() {
  const navigate = useNavigate();
  const user = useAuthStore(s => s.user);
  const location = useLocation();
  const qc = useQueryClient();
  const { dicts } = useDicts();
  const stageMap = toLabelMap(dicts.stages);

  const [text, setText] = useState('');
  const [leadId, setLeadId] = useState<string | undefined>(
    (location.state as { leadId?: string } | null)?.leadId,
  );
  const [draft, setDraft] = useState<AiDraft | null>(null);
  const [diagQuestion, setDiagQuestion] = useState('');
  const [diagnosis, setDiagnosis] = useState<AiDiagnosis | null>(null);
  const [intakeError, setIntakeError] = useState<{ status?: number; detail: string } | null>(null);
  const [diagError, setDiagError] = useState<{ status?: number; detail: string } | null>(null);
  const [lastNotes, setLastNotes] = useState<string[]>([]);

  /** 从工作台「帮我分析」带过来的商机：直接选中，少一次搜索（C1/C2） */
  const presetLeadId = (location.state as { leadId?: string } | null)?.leadId;
  useEffect(() => {
    if (presetLeadId) {
      setLeadId(presetLeadId);
      setDraft(null);
      setDiagnosis(null);
      setLastNotes([]);
      autoPicked.current = true;
    }
  }, [presetLeadId]);

  /** AI 能力状态：未配 key 时 AI 直接不可用（不做规则降级） */
  const {
    data: status,
    isError: statusError,
    error: statusErr,
    refetch: refetchStatus,
  } = useQuery({ queryKey: ['ai-status'], queryFn: aiStatus });

  /** 会话列表 = 待确认草稿（每条草稿就是一轮「消息 → 草稿」会话） */
  const { data: sessions, isLoading: sessionsLoading } = useQuery({
    queryKey: ['ai-drafts', { status: 'pending', page: 1, page_size: 20 }],
    queryFn: () => listDrafts({ status: 'pending', page: 1, page_size: 20 }),
  });

  const sessionItems = (sessions?.items ?? []).filter(
    (item) => !presetLeadId || item.lead_id === presetLeadId,
  );
  const { data: selectedProject } = useQuery({
    queryKey: ['opportunity-detail', leadId],
    queryFn: () => getOpportunityDetail(leadId as string),
    enabled: !!leadId,
  });
  const llmReady = !!status?.llm_configured;

  /**
   * 进页面时**自动打开最近一轮会话**（与设计稿一致：进来就看到会话 + 更新草稿），
   * 少一次点击；只自动选一次，用户点「新建对话」后不会再被抢回去。
   */
  const autoPicked = useRef(false);
  useEffect(() => {
    if (autoPicked.current || draft || !sessionItems.length) return;
    autoPicked.current = true;
    setDraft(sessionItems[0]);
    if (sessionItems[0].lead_id) setLeadId(sessionItems[0].lead_id as string);
  }, [sessionItems, draft]);

  const intake = useMutation({
    mutationFn: () => aiIntake({ message: text.trim(), lead_id: leadId ?? null }),
    onSuccess: (d) => {
      setIntakeError(null);
      setDiagnosis(null);
      setDraft(d);
      if (d.lead_id) setLeadId(d.lead_id);
      setLastNotes([]);
      autoPicked.current = true; // 刚生成的这轮就是当前会话：别被列表刷新抢回去
      if (!d.lead_id) {
        message.warning('未定位到客户：请在右侧「客户信息」指定客户后再确认（草稿不会改任何数据）');
      } else {
        message.success('已更新草稿，请核对后确认（未确认不生效）');
      }
      qc.invalidateQueries({ queryKey: ['ai-drafts'] });
      qc.invalidateQueries({ queryKey: ['workbench'] });
    },
    onError: (e) => setIntakeError(describeError(e)),
  });

  const diagnose = useMutation({
    mutationFn: () =>
      aiDiagnose({ lead_id: leadId as string, question: diagQuestion.trim() || undefined }),
    onSuccess: (d) => {
      setDiagError(null);
      setDraft(null);
      setDiagnosis(d);
      qc.invalidateQueries({ queryKey: ['ai-diagnoses'] });
      message.success('诊断完成：结论只作参考，是否采纳由你决定');
    },
    onError: (e) => setDiagError(describeError(e)),
  });

  const doConfirm = useMutation({
    mutationFn: (v: { id: string; body?: Parameters<typeof confirmDraft>[1] }) => confirmDraft(v.id, v.body),
    onSuccess: (r) => {
      const notes = r.notes ?? [];
      setLastNotes(notes);
      if (notes.length) {
        message.warning(`草稿已确认，但有 ${notes.length} 项未生效（原因见右侧说明）`);
      } else {
        message.success('草稿已确认并写入');
      }
      setDraft(r.draft);
      if (r.draft.lead_id) setLeadId(r.draft.lead_id);
      qc.invalidateQueries({ queryKey: ['ai-drafts'] });
      qc.invalidateQueries({ queryKey: ['workbench'] });
      qc.invalidateQueries({ queryKey: ['opportunities'] });
      qc.invalidateQueries({ queryKey: ['opportunity-detail'] });
      qc.invalidateQueries({ queryKey: ['leads'] });
      qc.invalidateQueries({ queryKey: ['customer-of-lead'] });
      qc.invalidateQueries({ queryKey: ['customer-detail'] });
      qc.invalidateQueries({ queryKey: ['customers'] });
      qc.invalidateQueries({ queryKey: ['standard-judgement'] });
      qc.invalidateQueries({ queryKey: ['lead-drafts'] });
      qc.invalidateQueries({ queryKey: ['risks'] });
    },
    onError: (e) => setLastNotes([describeError(e).detail]),
  });

  const doIgnore = useMutation({
    mutationFn: (id: string) => ignoreDraft(id),
    onSuccess: () => {
      message.success('草稿已忽略（未修改任何业务数据，无任何副作用）');
      setDraft(null);
      setLastNotes([]);
      qc.invalidateQueries({ queryKey: ['ai-drafts'] });
      qc.invalidateQueries({ queryKey: ['workbench'] });
    },
    onError: (e) => message.error(describeError(e).detail),
  });

  const reset = () => {
    autoPicked.current = true; // 用户主动开新对话：不要再自动抢回最近会话
    setText('');
    setDraft(null);
    setDiagnosis(null);
    setIntakeError(null);
    setDiagError(null);
    setLastNotes([]);
  };

  const currentLeadName =
    (selectedProject?.lead && [selectedProject.lead.name, selectedProject.lead.project_name].filter(Boolean).join(' · ')) ||
    draft?.lead_name ||
    diagnosis?.lead_name ||
    (leadId ? `客户 ${leadId.slice(0, 6)}…` : '新对话');

  const currentStage = selectedProject?.lead.current_stage
    ? stageMap[selectedProject.lead.current_stage] ?? selectedProject.lead.current_stage
    : draft?.payload.lead_stage
    ? stageMap[draft.payload.lead_stage] ?? draft.payload.lead_stage
    : diagnosis?.stage_label || '—';

  const diagView = useMemo(() => splitDiagnosis(diagnosis?.content), [diagnosis]);

  /** AI 建议 → 复制 + 跳转（AI 不直接写业务数据；待办/周报由人确认后登记） */
  const suggestionText = () => {
    const structured = diagnosis?.structured;
    const actions = structured?.suggested_actions?.length
      ? structured.suggested_actions
      : diagView.suggestions;
    return [
      `【AI 诊断】${currentLeadName} · ${currentStage}`,
      structured?.stage_diagnosis || diagView.quote,
      ...actions.map((s, i) => `${i + 1}. ${s}`),
      ...(structured?.next_week_plan?.length
        ? ['下周计划：', ...structured.next_week_plan.map((p, i) => `  ${i + 1}. ${p}`)]
        : []),
    ]
      .filter(Boolean)
      .join('\n');
  };

  const copySuggestions = async () => {
    try {
      await navigator.clipboard.writeText(suggestionText());
      message.success('建议已复制到剪贴板');
      return true;
    } catch {
      message.warning('浏览器未授权剪贴板，请手动选择诊断文字复制');
      return false;
    }
  };

  /** 发送按钮的禁用原因（就地说明，不让人猜） */
  const sendDisabledReason = !llmReady
    ? '按钮不可用：模型未配置（AI 不做规则降级解析）—— 去「系统设置 → 适配配置」配置密钥后重试。'
    : text.trim().length < 2
      ? '按钮不可用：先输入一段拜访/沟通内容（至少 2 个字），越具体越好：对象、需求、预算、时间、卡点。'
      : '';

  const askDisabledReason = !leadId
    ? '「问 AI」不可用：诊断要针对某个商机 —— 先在下方「关联客户」里选一个客户（不选客户时只能生成草稿）。'
    : !llmReady
      ? '「问 AI」不可用：模型未配置（AI 不做规则降级解析）。'
      : '';

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <PageHeader
        title="AI 销售助手"
        subtitle="记录一次跟进，核对更新，再决定下一步。"
        tags={<AiDisclaimer />}
        actions={
          <Space wrap>
            {leadId && <Button onClick={() => navigate(`/opportunities/${leadId}`)}>返回商机</Button>}
            <Button onClick={() => navigate('/ai/drafts')}>我的待确认</Button>
            <Button onClick={() => navigate('/ai/diagnoses')}>诊断历史</Button>
          </Space>
        }
      />

      {/* AI 状态本身拉取失败：也要说清为什么 + 怎么办 */}
      {statusError && (
        <AiErrorPanel
          title="AI 能力状态读取失败"
          error={describeError(statusErr)}
          onRetry={() => refetchStatus()}
          retryText="重新检测"
        />
      )}

      {status && !status.llm_configured && (
        <GateBanner
          title="AI 功能当前不可用：未配置模型密钥"
          items={[
            status.note || '未配置模型密钥时，AI 录入与诊断会直接返回 503。',
            '本系统不做规则降级解析 —— 避免用「看起来能用」的假结果误导判读。',
            '你仍然可以正常手工录入：去商机详情勾选标准（必附证据）、改金额（必填依据）。',
          ]}
          action={
            <Space size={8} wrap>
              <Button size="small" type="primary" onClick={() => navigate('/admin/adapters')}>
                去适配配置
              </Button>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                无权限时请联系系统管理员配置密钥。
              </Typography.Text>
            </Space>
          }
        />
      )}

      <div className="ai-workspace">
        {/* ① 会话列表 */}
        <section className="ai-workspace-sessions" aria-label="待确认会话">
          <Card
            size="small"
            title={`待确认 · ${sessions?.total ?? 0}`}
            extra={<Space size={8}>
              <Button size="small" icon={<PlusOutlined />} onClick={reset}>新对话</Button>
              <a onClick={() => navigate('/ai/drafts')}>全部</a>
            </Space>}
            style={{ height: '100%' }}
          >
            <List
              size="small"
              loading={sessionsLoading}
              dataSource={sessionItems}
              locale={{
                emptyText: (
                  <NextStepGuide
                    compact
                    title="还没有会话"
                    description="在中间发一条拜访/沟通内容，AI 会生成更新草稿并出现在这里；也可以直接问「这个项目为什么卡住」。"
                  />
                ),
              }}
              renderItem={(d) => (
                <List.Item
                  onClick={() => {
                    setDraft(d);
                    setDiagnosis(null);
                    setLastNotes([]);
                    if (d.lead_id) setLeadId(d.lead_id);
                  }}
                  style={{
                    cursor: 'pointer',
                    borderRadius: 8,
                    padding: '8px 10px',
                    background: draft?.id === d.id ? 'var(--brand-soft)' : undefined,
                  }}
                >
                  <List.Item.Meta
                    title={
                      <Space size={6} wrap>
                        <Typography.Text strong>{d.lead_name || '未定位客户'}</Typography.Text>
                        {!d.lead_id && <Tag color="red">未定位</Tag>}
                        <PanelFlag>待确认</PanelFlag>
                      </Space>
                    }
                    description={
                      <Space direction="vertical" size={2} style={{ width: '100%' }}>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }} ellipsis>
                          {d.summary}
                        </Typography.Text>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {sessionTime(d.created_at)}
                        </Typography.Text>
                      </Space>
                    }
                  />
                </List.Item>
              )}
            />
          </Card>
        </section>

        {/* ② 对话区 */}
        <section className="ai-workspace-conversation" aria-label="跟进录入与诊断">
          <Card
            size="small"
            title={
              <Space size={8} wrap>
                <RobotOutlined />
                <Typography.Text strong>{currentLeadName}</Typography.Text>
                <Tag color="blue">当前阶段：{currentStage}</Tag>
                {draft && (
                  <Tag color={PARSE_MODE_COLOR[draft.parse_mode] ?? 'default'}>
                    {PARSE_MODE_LABEL[draft.parse_mode] ?? draft.parse_mode}
                  </Tag>
                )}
              </Space>
            }
            extra={
              <Space size={8}>
                <Tooltip title={askDisabledReason}>
                  <Button
                    size="small"
                    icon={<ThunderboltOutlined />}
                    disabled={!leadId || !llmReady}
                    loading={diagnose.isPending}
                    onClick={() => diagnose.mutate()}
                  >
                    AI 专家诊断
                  </Button>
                </Tooltip>
              </Space>
            }
            style={{ height: '100%' }}
          >
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Space wrap size={8}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  关联项目
                </Typography.Text>
                <LeadSelect value={leadId} onChange={(id) => {
                  setLeadId(id);
                  setDraft(null);
                  setDiagnosis(null);
                  setLastNotes([]);
                  autoPicked.current = true;
                }} placeholder="选择客户及项目" style={{ minWidth: 200, width: '100%' }} />
                <Input
                  style={{ width: 240 }}
                  size="middle"
                  placeholder={EXAMPLE_QUESTION}
                  value={diagQuestion}
                  onChange={(e) => setDiagQuestion(e.target.value)}
                />
              </Space>
              {askDisabledReason && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {askDisabledReason}
                </Typography.Text>
              )}

              <div className="ai-conversation-history">
              {diagnose.isPending && (
                <Alert
                  type="info"
                  showIcon
                  message="正在让 AI 销售助手诊断这个项目…"
                  description="模型思考 + 重试通常需要 30 秒以上（前端已放到 180 秒长超时），请勿关闭页面。"
                />
              )}
              {diagError && (
                <AiErrorPanel
                  title="诊断失败"
                  error={diagError}
                  onRetry={() => diagnose.mutate()}
                  retryText="重试诊断"
                />
              )}

              {!draft && !diagnosis && !diagnose.isPending && !diagError ? (
                <NextStepGuide
                  title="还没有对话内容"
                  description="把拜访/沟通内容直接发过来（越具体越好：对象、需求、预算、时间、卡点），AI 只会生成草稿，确认权在你；也可以在右上问「这个项目为什么卡住」。"
                  actions={
                    <Space wrap>
                      <Button size="small" onClick={() => setText(EXAMPLE)}>
                        填入示例消息
                      </Button>
                      <Button size="small" onClick={() => setDiagQuestion(EXAMPLE_QUESTION)}>
                        填入示例问题
                      </Button>
                    </Space>
                  }
                />
              ) : (
                <Space direction="vertical" size={10} style={{ width: '100%' }}>
                  {/* 我的消息 */}
                  {draft && (
                    <>
                      <div style={{ display: 'flex', gap: 8 }}>
                        <Avatar size={28} style={{ background: 'var(--brand)', flexShrink: 0 }} icon={<UserOutlined />} />
                        <div style={{ minWidth: 0 }}>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {fmtDate(draft.created_at, 'HH:mm')} · {draft.channel === 'im' ? 'IM' : 'App'}
                          </Typography.Text>
                          <div
                            style={{
                              background: 'var(--bg-layout)',
                              borderRadius: 8,
                              padding: '8px 12px',
                              fontSize: 13,
                              lineHeight: 1.7,
                              whiteSpace: 'pre-wrap',
                            }}
                          >
                            {draft.source_message}
                          </div>
                        </div>
                      </div>

                      {/* AI 回复：设计稿原话「已生成更新草稿，请核对后确认。」 */}
                      <div style={{ display: 'flex', gap: 8 }}>
                        <Avatar
                          size={28}
                          style={{ background: 'var(--brand-soft)', color: 'var(--brand)', flexShrink: 0 }}
                          icon={<RobotOutlined />}
                        />
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {fmtDate(draft.created_at, 'HH:mm')} · AI
                          </Typography.Text>
                          {/* 气泡底色沿用卡片自身的 bg（AntD token colorBgContainer），不写死颜色 */}
                          <div
                            style={{
                              border: '1px solid var(--border)',
                              borderRadius: 8,
                              padding: '8px 12px',
                              fontSize: 13,
                              lineHeight: 1.7,
                            }}
                          >
                            已生成更新草稿，请核对后确认。
                            <div style={{ color: 'var(--text-sub)', marginTop: 4 }}>{draft.summary}</div>
                            <Space wrap size={8} style={{ marginTop: 8 }}>
                              <Button
                                size="small"
                                type="primary"
                                icon={<CheckCircleOutlined />}
                                onClick={() => navigate('/ai/drafts', { state: { draftId: draft.id } })}
                              >
                                打开草稿确认
                              </Button>
                              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                也可直接在本页右侧确认；未确认不写入任何业务数据。
                              </Typography.Text>
                            </Space>
                          </div>
                        </div>
                      </div>
                    </>
                  )}

                  {/* AI 专家诊断 */}
                  {diagnosis && (
                    <Card
                      size="small"
                      style={{ background: 'var(--brand-soft)', borderColor: 'var(--brand-soft)' }}
                      title={
                        <Space size={8} wrap>
                          <ThunderboltOutlined style={{ color: 'var(--brand)' }} />
                          <Typography.Text strong>AI 专家诊断</Typography.Text>
                          <Tag>{diagnosis.stage_label || '—'}</Tag>
                          <AiDisclaimer />
                        </Space>
                      }
                    >
                      <Space direction="vertical" size={10} style={{ width: '100%' }}>
                        {/* 结构化区块（缺失的关键动作 / 关键建议 / 话术 / 下周计划 / 风险），
                            旧记录没有 structured 时退回 Markdown 轻量解析 */}
                        {diagnosis.structured ? (
                          <AiDiagnosisSections structured={diagnosis.structured} compact />
                        ) : (
                          <>
                            {diagView.quote && (
                              <div style={{ borderLeft: '3px solid var(--brand)', paddingLeft: 10 }}>
                                <Typography.Text style={{ fontSize: 13, color: 'var(--text-sub)' }}>
                                  「{diagView.quote}」
                                </Typography.Text>
                              </div>
                            )}
                            {!!diagView.suggestions.length && (
                              <div>
                                <Typography.Text strong style={{ fontSize: 13 }}>
                                  关键建议
                                </Typography.Text>
                                <Space direction="vertical" size={4} style={{ width: '100%', marginTop: 4 }}>
                                  {diagView.suggestions.map((s, i) => (
                                    <Space key={i} size={6} align="start">
                                      <span
                                        style={{
                                          display: 'inline-flex',
                                          alignItems: 'center',
                                          justifyContent: 'center',
                                          width: 18,
                                          height: 18,
                                          borderRadius: 999,
                                          background: 'var(--brand-soft)',
                                          color: 'var(--brand)',
                                          fontSize: 12,
                                          flexShrink: 0,
                                        }}
                                      >
                                        {i + 1}
                                      </span>
                                      <Typography.Text style={{ fontSize: 13 }}>{s}</Typography.Text>
                                    </Space>
                                  ))}
                                </Space>
                              </div>
                            )}
                          </>
                        )}

                        <Collapse
                          size="small"
                          ghost
                          items={[
                            {
                              key: 'full',
                              label: '查看完整诊断正文',
                              forceRender: true,
                              children: <AiContent content={diagnosis.content} />,
                            },
                          ]}
                        />

                        <Space wrap size={8}>
                          <DiagnosisActions diagnosis={diagnosis} canWrite={
                            selectedProject?.lead.owner_id === user?.id &&
                            ['market_staff', 'market_manager', 'admin'].includes(user?.role ?? '')
                          } />
                          <Button
                            size="small"
                            onClick={async () => {
                              await copySuggestions();
                              navigate('/reports/weekly');
                            }}
                          >
                            写入周报
                          </Button>
                          <Button size="small" type="link" onClick={() => navigate('/ai/diagnoses')}>
                            看历史诊断与是否推进
                          </Button>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            写入周报会复制建议并打开周报，由你编辑后提交。
                          </Typography.Text>
                        </Space>
                      </Space>
                    </Card>
                  )}
                </Space>
              )}

              </div>
              {/* 输入区 */}
              {intakeError && (
                <AiErrorPanel
                  title="生成草稿失败"
                  error={intakeError}
                  onRetry={() => intake.mutate()}
                  retryText="重试生成"
                />
              )}
              <Input.TextArea
                rows={3}
                maxLength={2000}
                showCount
                value={text}
                placeholder={EXAMPLE}
                onChange={(e) => setText(e.target.value)}
              />
              <Space wrap>
                <Button
                  type="primary"
                  icon={<SendOutlined />}
                  loading={intake.isPending}
                  disabled={text.trim().length < 2 || !llmReady}
                  onClick={() => intake.mutate()}
                >
                  生成草稿
                </Button>
                <Tooltip title={askDisabledReason}>
                  <Button
                    icon={<QuestionCircleOutlined />}
                    disabled={!leadId || !llmReady}
                    loading={diagnose.isPending}
                    onClick={() => diagnose.mutate()}
                  >
                    问 AI：这个项目为什么卡住
                  </Button>
                </Tooltip>
                <Button onClick={reset} disabled={!text && !draft && !diagnosis}>
                  清空
                </Button>
                <Button type="link" onClick={() => setText(EXAMPLE)}>
                  填入示例
                </Button>
              </Space>
              {(sendDisabledReason || askDisabledReason) && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {sendDisabledReason || askDisabledReason}
                </Typography.Text>
              )}
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                支持文本跟进。请写明沟通对象、需求、预算、时间和下一步安排。
              </Typography.Text>
            </Space>
          </Card>
        </section>

        {/* ③ 更新草稿面板（与 5.2 详情共用同一套呈现） */}
        <section className="ai-workspace-draft" aria-label="更新草稿核对">
          {draft ? (
            <Card size="small" style={{ height: '100%' }}>
              <AiDraftPanel
                draft={draft}
                confirming={doConfirm.isPending}
                ignoring={doIgnore.isPending}
                lastNotes={lastNotes}
                onConfirm={(body) => doConfirm.mutate({ id: draft.id, body })}
                onIgnore={() => doIgnore.mutate(draft.id)}
                footerExtra={
                  <Space wrap>
                    {draft.status === 'confirmed' && draft.lead_id && (
                      <Button type="primary" onClick={() => navigate(`/opportunities/${draft.lead_id}`)}>
                        查看更新后的商机
                      </Button>
                    )}
                    <Button onClick={reset} disabled={!text && !draft}>再录一条</Button>
                  </Space>
                }
              />
            </Card>
          ) : (
            <Card size="small" style={{ height: '100%' }}>
              <NextStepGuide
                title="这里显示本次的更新草稿"
                description="发送消息或点左侧会话后，右侧给出字段级变更对照、更新依据、标准证据与原始消息出处。未确认不生效，可忽略、可修正。"
                actions={
                  <Space wrap>
                    <PanelFlag>未确认不生效</PanelFlag>
                    <Button size="small" onClick={() => setText(EXAMPLE)}>
                      填入示例
                    </Button>
                  </Space>
                }
              />
            </Card>
          )}
        </section>
      </div>

      <Footnote>
        ⓘ AI 只产出草稿：未确认不修改任何业务数据；确认时硬门槛缺证据会保留未完成、环节不会自动推进。
        AI 结论与建议不作为考核依据。模型不可用时直接报错（503），不做规则降级解析。
      </Footnote>
    </Space>
  );
}
