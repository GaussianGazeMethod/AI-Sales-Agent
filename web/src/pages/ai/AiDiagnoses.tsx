import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Drawer,
  Progress,
  Space,
  Table,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  RiseOutlined,
  StopOutlined,
} from '@ant-design/icons';
import {
  PARSE_MODE_COLOR,
  PARSE_MODE_LABEL,
  listDiagnoses,
  type AiDiagnosis,
  type AiDiagnosisStandard,
  type AiDiagnosisStructured,
} from '@/api/ai';
import { describeError } from '@/api/client';
import { getOpportunityDetail } from '@/api/opportunities';
import { listLeads } from '@/api/leads';
import PageHeader from '@/components/PageHeader';
import AiContent from '@/components/AiContent';
import LeadSelect from '@/components/LeadSelect';
import { AiDisclaimer, Footnote, NextStepGuide } from '@/components/Guide';
import { useDicts } from '@/hooks/useDicts';
import { fmtDate, fmtDateTime } from '@/utils/format';
import type { Lead, OpportunityDetailResp } from '@/types';

const PAGE_SIZE = 10;

/** 采纳标记（后端字段 adopted）：当前没有写入入口，所以多数是「未标记」——如实展示，不编。 */
function adoptedTag(adopted: boolean | null | undefined) {
  if (adopted === true) return <Tag color="success">已采纳</Tag>;
  if (adopted === false) return <Tag color="default">未采纳</Tag>;
  return (
    <Tooltip title="「是否采纳」用于记录人有没有采纳这次诊断。当前系统没有标记入口（本期未做），因此新记录都是「未标记」；本页用下面的「诊断后是否真的推进」做事实对照。">
      <Tag color="warning">未标记</Tag>
    </Tooltip>
  );
}

/**
 * 5.3 诊断报告历史：历次 AI 诊断结论 + **是否采纳** + **诊断之后项目有没有真的推进**。
 *
 * 判据对照：
 * - C4 赋能边界：每条诊断都带 AiDisclaimer（不作为考核依据），可回看触发方式、结构化摘要与原始结论；
 * - C2 少判断：空态给「下一步怎么做 + 直达 5.1」；项目不在数据范围/详情拉取失败时也说清为什么 + 怎么办；
 * - C3 口径可见：停留/基准、标准完成数、以及「是否推进」的判定口径都写在界面上，不展示任何 AI 命中率。
 *
 * 诚实边界：系统只能证明「诊断之后环节是否发生了变化」，**不能证明是诊断带来的**，
 * 因此界面只做**事实陈述**（推进了 / 未推进），不做因果归因；采纳标记没有入口就显示「未标记」。
 */
export default function AiDiagnoses() {
  const navigate = useNavigate();
  const { dicts } = useDicts();
  const [page, setPage] = useState(1);
  const [leadId, setLeadId] = useState<string | undefined>();
  const [current, setCurrent] = useState<AiDiagnosis | null>(null);

  const {
    data,
    isLoading,
    isError,
    error,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: ['ai-diagnoses', { lead_id: leadId, page, page_size: PAGE_SIZE }],
    queryFn: () => listDiagnoses({ lead_id: leadId, page, page_size: PAGE_SIZE }),
  });

  const listErr = describeError(error);

  /** 环节先后顺序（用于判断「诊断后是否推进」）：口径 = 后端下发的 7 环节字典顺序 */
  const stageIndex = useMemo(() => {
    const map: Record<string, number> = {};
    (dicts.stages ?? []).forEach((s, i) => {
      map[s.value] = i;
    });
    return map;
  }, [dicts.stages]);

  /** 名单缓存（与 LeadSelect 共用 key）：用于列表里就地判断「诊断后有没有推进」 */
  const { data: myLeads } = useQuery({
    queryKey: ['leads', 'mine-options'],
    queryFn: () => listLeads({ view: 'mine', page_size: 50, page: 1 }),
    staleTime: 60 * 1000,
  });
  const leadMap = useMemo(() => {
    const map: Record<string, Lead> = {};
    (myLeads?.items ?? []).forEach((l) => {
      map[l.id] = l;
    });
    return map;
  }, [myLeads]);

  /** 诊断后是否推进（列表口径，只用可见线索做事实对照） */
  function progressOf(r: AiDiagnosis): { tag: React.ReactNode; hint: string } | null {
    if (!r.lead_id || !r.stage) return null;
    const lead = leadMap[r.lead_id];
    if (!lead) return null; // 不在可见范围：留空，不猜
    const from = stageIndex[r.stage];
    const to = lead.current_stage ? stageIndex[lead.current_stage] : undefined;
    const at = fmtDate(lead.stage_entered_at);
    if (from === undefined || to === undefined) return null;
    if (to > from) {
      return {
        tag: (
          <Tag icon={<RiseOutlined />} color="success">
            已推进 → {lead.current_stage_label}
          </Tag>
        ),
        hint: `诊断发生在「${r.stage_label || r.stage}」，现在该项目在「${lead.current_stage_label ?? lead.current_stage}」（进入时间 ${at}）。只陈事实，不代表是诊断带来的。`,
      };
    }
    if (to === from) {
      return {
        tag: (
          <Tag icon={<StopOutlined />} color="orange">
            仍停在 {lead.current_stage_label ?? lead.current_stage}
          </Tag>
        ),
        hint: `诊断之后该项目没有再流转（当前停留 ${lead.stage_stay_days ?? '—'} 天，基准 ${lead.stage_base_days ?? '—'} 天）。`,
      };
    }
    return {
      tag: <Tag color="default">已回退 → {lead.current_stage_label}</Tag>,
      hint: `诊断后该项目从「${r.stage_label || r.stage}」回退到「${lead.current_stage_label ?? lead.current_stage}」。`,
    };
  }

  const columns: ColumnsType<AiDiagnosis> = [
    {
      title: '客户',
      dataIndex: 'lead_name',
      width: 180,
      render: (v: string | null, r) =>
        r.lead_id ? <a onClick={() => navigate(`/opportunities/${r.lead_id}`)}>{v || '未命名'}</a> : (v ?? '-'),
    },
    {
      title: '诊断时环节',
      dataIndex: 'stage_label',
      width: 120,
      render: (v: string) => v || '-',
    },
    {
      title: '是否采纳',
      dataIndex: 'adopted',
      width: 100,
      render: (v: boolean | null) => adoptedTag(v),
    },
    {
      title: '诊断后是否推进',
      key: 'progress',
      width: 190,
      render: (_, r) => {
        const p = progressOf(r);
        if (!p) {
          return (
            <Tooltip title="该项目不在你当前可见的线索范围里（或这是管理视角下别人的项目），因此列表不做推断；打开右侧详情可由后端权威数据判断。">
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                —
              </Typography.Text>
            </Tooltip>
          );
        }
        return <Tooltip title={p.hint}>{p.tag}</Tooltip>;
      },
    },
    {
      title: '触发方式',
      dataIndex: 'trigger',
      width: 90,
      render: (v: string) => <Tag>{v === 'manual' ? '手动' : v}</Tag>,
    },
    {
      title: '解析方式',
      dataIndex: 'parse_mode',
      width: 110,
      render: (v: string) => (
        <Tag color={PARSE_MODE_COLOR[v] ?? 'default'}>{PARSE_MODE_LABEL[v] ?? v}</Tag>
      ),
    },
    {
      title: '结构化摘要',
      dataIndex: 'structured',
      render: (_, r) =>
        r.structured ? (
          <Typography.Text style={{ fontSize: 12 }}>
            停留 {r.structured.stay_days}/{r.structured.base_days} 天 · 标准{' '}
            {r.structured.done_count}/{r.structured.total_count}
            {r.structured.overdue && (
              <Tag color="red" style={{ marginLeft: 6 }}>
                超期
              </Tag>
            )}
          </Typography.Text>
        ) : (
          '-'
        ),
    },
    { title: '创建人', dataIndex: 'created_by_name', width: 100, render: (v: string) => v || '-' },
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 150,
      render: (v: string) => fmtDateTime(v),
    },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <PageHeader
        title="AI 诊断报告历史"
        subtitle="回看历次诊断结论、是否采纳，以及「诊断之后项目有没有真的推进」。"
        demo
        tags={<AiDisclaimer />}
        meta={[
          { label: '共', value: `${data?.total ?? 0} 份` },
          { label: '口径', value: '按客户筛选；停留/基准来自商机环节数据' },
          { label: '推进判定', value: '诊断时环节 vs 该项目当前环节（只陈事实）' },
        ]}
        actions={<LeadSelect value={leadId} onChange={setLeadId} placeholder="按客户筛选（可清空）" />}
      />

      <Card
        title="历次诊断"
        extra={
          <Space>
            <Button size="small" onClick={() => refetch()} loading={isFetching}>
              刷新
            </Button>
            <Button size="small" type="primary" onClick={() => navigate('/ai/chat')}>
              去 5.1 发起诊断
            </Button>
          </Space>
        }
      >
        {isError ? (
          <Alert
            type="error"
            showIcon
            message="诊断历史加载失败"
            description={
              <Space direction="vertical" size={4}>
                <Typography.Text style={{ fontSize: 12 }}>
                  原因：{listErr.detail}
                  {listErr.status ? `（HTTP ${listErr.status}）` : ''}
                </Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  怎么办：点「重试」再拉一次；诊断报告已留档在服务端，不会因此丢失。若持续失败多为登录态过期。
                </Typography.Text>
                <Button size="small" type="primary" onClick={() => refetch()}>
                  重试
                </Button>
              </Space>
            }
          />
        ) : (
          <Table<AiDiagnosis>
            rowKey="id"
            size="small"
            loading={isLoading}
            dataSource={data?.items ?? []}
            columns={columns}
            locale={{
              emptyText: (
                <NextStepGuide
                  title={leadId ? '该客户暂无诊断报告' : '暂无诊断报告'}
                  description="诊断在 5.1 对话与录入里发起：选好客户 → 点「AI 专家诊断」（也可直接问「这个项目为什么卡住」）。诊断只给建议，不会自动改数据，也不作为考核依据。"
                  actions={
                    <Button type="primary" onClick={() => navigate('/ai/chat')}>
                      去 5.1 发起诊断
                    </Button>
                  }
                />
              ),
            }}
            scroll={{ x: 1280 }}
            onRow={(r) => ({ onClick: () => setCurrent(r), style: { cursor: 'pointer' } })}
            pagination={{
              current: page,
              pageSize: PAGE_SIZE,
              total: data?.total ?? 0,
              onChange: setPage,
              showTotal: (t) => `共 ${t} 条`,
            }}
          />
        )}
      </Card>

      <Drawer
        open={!!current}
        width={760}
        title={current ? `${current.lead_name ?? '商机'} · ${current.stage_label || '—'}` : ''}
        onClose={() => setCurrent(null)}
        styles={{ body: { background: 'var(--bg-layout)' } }}
      >
        {current && <DiagnosisDetail diagnosis={current} stageIndex={stageIndex} />}
      </Drawer>

      <Footnote>
        ⓘ 口径：停留天数按<b>自然日</b>计；标准完成数 = 已完成标准 / 该环节全部标准。
        「是否推进」= 诊断时所在环节 vs 该项目当前环节（后端流转记录为准），**只陈事实、不推断因果**；
        「是否采纳」字段本期没有标记入口，故多为「未标记」。AI 结论不作为考核依据。
      </Footnote>
    </Space>
  );
}

/** 诊断详情：结论 + 结构化摘要 +  诊断之后项目推进了吗（用后端权威数据核对） */function DiagnosisDetail({
  diagnosis,
  stageIndex,
}: {
  diagnosis: AiDiagnosis;
  stageIndex: Record<string, number>;
}) {
  const navigate = useNavigate();

  const {
    data: detail,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ['opportunity-detail', diagnosis.lead_id],
    queryFn: () => getOpportunityDetail(diagnosis.lead_id as string),
    enabled: !!diagnosis.lead_id,
    staleTime: 30 * 1000,
  });

  const detailErr = describeError(error);

  /** 诊断之后发生的环节流转（后端流转记录为准） */
  const after = useMemo(() => {
    if (!detail) return [] as OpportunityDetailResp['transitions'];
    return (detail.transitions ?? []).filter(
      (t) => t.transition_at && t.transition_at > diagnosis.created_at,
    );
  }, [detail, diagnosis.created_at]);

  const lead = detail?.lead;
  const from = diagnosis.stage ? stageIndex[diagnosis.stage] : undefined;
  const to = lead?.current_stage ? stageIndex[lead.current_stage] : undefined;
  const advanced = from !== undefined && to !== undefined ? to > from : null;

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Space wrap size={8}>
        <AiDisclaimer />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          诊断触发方式：{diagnosis.trigger === 'manual' ? '手动诊断' : diagnosis.trigger} · 解析方式：
          {PARSE_MODE_LABEL[diagnosis.parse_mode] ?? diagnosis.parse_mode}
          {diagnosis.model_used ? ` · ${diagnosis.model_used}` : ''}
        </Typography.Text>
      </Space>

      <Descriptions size="small" column={2} bordered>
        <Descriptions.Item label="是否采纳">{adoptedTag(diagnosis.adopted)}</Descriptions.Item>
        <Descriptions.Item label="诊断时间">{fmtDateTime(diagnosis.created_at)}</Descriptions.Item>
        <Descriptions.Item label="诊断时环节">{diagnosis.stage_label || '—'}</Descriptions.Item>
        <Descriptions.Item label="创建人">{diagnosis.created_by_name ?? '-'}</Descriptions.Item>
        {diagnosis.lead_id && (
          <Descriptions.Item label="关联商机" span={2}>
            <Space size={12}>
              <a onClick={() => navigate(`/opportunities/${diagnosis.lead_id}`)}>查看商机详情</a>
              <a onClick={() => navigate(`/ai/chat?lead=${diagnosis.lead_id}`)}>
                在 5.1 继续问 AI
              </a>
            </Space>
          </Descriptions.Item>
        )}
      </Descriptions>

      {/*  诊断之后项目有没有真的推进 */}
      <Card
        size="small"
        title="诊断之后，这个项目推进了吗"
        extra={
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            口径：诊断时环节 vs 当前环节 + 后端流转记录
          </Typography.Text>
        }
      >
        {!diagnosis.lead_id ? (
          <Alert
            type="info"
            showIcon
            message="这份诊断没有关联商机，无法对照推进情况"
            description="（历史数据可能出现）可按诊断正文里的客户名到商机列表里核对。"
          />
        ) : isError ? (
          <Alert
            type="warning"
            showIcon
            message="读取该项目当前状态失败，无法判断是否推进"
            description={
              <Space direction="vertical" size={4}>
                <Typography.Text style={{ fontSize: 12 }}>
                  原因：{detailErr.detail}
                  {detailErr.status ? `（HTTP ${detailErr.status}）` : ''}
                </Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  怎么办：多为该项目不在你的数据范围（管理者看团队时会遇到）。可点「重试」，或让该项目负责人查看。
                </Typography.Text>
                <Button size="small" onClick={() => refetch()}>
                  重试
                </Button>
              </Space>
            }
          />
        ) : isLoading || !lead ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            正在读取该项目当前环节与流转记录…
          </Typography.Text>
        ) : (
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            {advanced === true && (
              <Alert
                type="success"
                showIcon
                icon={<RiseOutlined />}
                message={`诊断后已推进：${diagnosis.stage_label || diagnosis.stage} → ${lead.current_stage_label || lead.current_stage}`}
                description="只陈述事实：诊断之后该项目发生了环节流转（下面有流转记录）。是否由这次诊断促成，需要结合实际情况判断。"
              />
            )}
            {advanced === false && (
              <Alert
                type="warning"
                showIcon
                icon={<StopOutlined />}
                message={`诊断后仍停在「${lead.current_stage_label || lead.current_stage}」`}
                description={
                  <Space direction="vertical" size={2}>
                    <Typography.Text style={{ fontSize: 12 }}>
                      当前已停留 {lead.stage_stay_days ?? '—'} 天（基准 {lead.stage_base_days ?? '—'} 天
                      {lead.stage_overdue ? '，已超期' : ''}）。
                    </Typography.Text>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      怎么办：按诊断里的「关键建议」挑 1–2 条本周落地（补齐缺失标准动作 + 证据），或再发起一次诊断。
                    </Typography.Text>
                  </Space>
                }
              />
            )}
            {advanced === null && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                环节字典缺失，无法比较先后（不影响看结论）。
              </Typography.Text>
            )}

            {lead.stage_entered_at && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                <ClockCircleOutlined /> 当前环节进入时间：{fmtDateTime(lead.stage_entered_at)}
                {lead.status === 'signed' ? ' · 销售阶段已结束' : ` · 下一节点：${lead.next_node || '待确认'} · 预计时间：${lead.next_node_time || '待确认'}`}
              </Typography.Text>
            )}

            {after.length > 0 ? (
              <Timeline
                items={after.map((t) => ({
                  color: 'green',
                  children: (
                    <Space direction="vertical" size={2}>
                      <Typography.Text style={{ fontSize: 12 }}>
                        {t.from_stage_label || '—'} → <b>{t.to_stage_label}</b>（{fmtDateTime(t.transition_at)}）
                      </Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        操作人：{t.operator_name ?? '-'} · 在上一环节停留 {t.stay_days} 天 · 依据：{t.basis || '—'}
                      </Typography.Text>
                    </Space>
                  ),
                }))}
              />
            ) : (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                诊断之后本项目没有产生新的环节流转记录。
              </Typography.Text>
            )}
          </Space>
        )}
      </Card>

      {diagnosis.structured && (
        <Card size="small" title="结构化摘要（诊断时点的数据快照）">
          <Space direction="vertical" size={10} style={{ width: '100%' }}>
            <AiDiagnosisSections structured={diagnosis.structured} />
            <Typography.Text>
              停留 {diagnosis.structured.stay_days} 天 / 基准 {diagnosis.structured.base_days} 天
              {diagnosis.structured.overdue ? (
                <Tag color="red" style={{ marginLeft: 8 }}>
                  已超期
                </Tag>
              ) : (
                <Tag color="green" style={{ marginLeft: 8 }}>
                  在基准内
                </Tag>
              )}
            </Typography.Text>
            <Progress
              percent={
                diagnosis.structured.total_count
                  ? Math.round(
                      (diagnosis.structured.done_count / diagnosis.structured.total_count) * 100,
                    )
                  : 0
              }
              size="small"
              format={() =>
                `${diagnosis.structured?.done_count}/${diagnosis.structured?.total_count}`
              }
            />
            <Typography.Text>
              缺失硬门槛：
              {diagnosis.structured.missing_hard_gates.length ? (
                <Space wrap>
                  {diagnosis.structured.missing_hard_gates.map((g) => (
                    <Tag color="red" key={g}>
                      {g}
                    </Tag>
                  ))}
                </Space>
              ) : (
                <Tag color="success" icon={<CheckCircleOutlined />}>
                  无
                </Tag>
              )}
            </Typography.Text>
          </Space>
        </Card>
      )}

      <Card size="small" title="诊断正文">
        <AiContent content={diagnosis.content} />
      </Card>
    </Space>
  );
}

/* ══════════════════════════════════════════════════════════════════════
 * 诊断结构化区块（5.3 详情 与 5.1 诊断卡片**共用**）
 *
 * 为什么单独抽出来：spec/06 C1 要求 AI 输出 = 环节诊断 + 卡点 + **缺失的标准动作** +
 * 话术 + 风险提示 + 下周计划。后端已把这些结构化下发（structured.*），
 * 只贴一段 Markdown 正文会让人自己找，违背「少判断」。
 *
 * 只做**只读呈现**：AI 不直接改数据，采纳与否由人决定（C4 赋能边界）。
 * ══════════════════════════════════════════════════════════════════════ */
export function AiDiagnosisSections({
  structured,
  compact,
}: {
  structured?: AiDiagnosisStructured | null;
  /** 5.1 中栏空间窄：默认折叠次要区块 */
  compact?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  if (!structured) return null;

  const missing = structured.missing_actions ?? [];
  const risks = structured.risk_findings ?? [];
  const actions = structured.suggested_actions ?? [];
  const talks = structured.talk_tracks ?? [];
  const plans = structured.next_week_plan ?? [];
  const standards = structured.standards ?? [];
  const warnings = structured.warnings ?? [];

  const copyTalks = async () => {
    try {
      await navigator.clipboard.writeText(talks.map((t, i) => `${i + 1}. ${t}`).join('\n'));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      {structured.stage_diagnosis && (
        <div style={{ borderLeft: '3px solid var(--brand)', paddingLeft: 10 }}>
          <Typography.Text style={{ fontSize: 13, color: 'var(--text-sub)' }}>
            「{structured.stage_diagnosis}」
          </Typography.Text>
        </div>
      )}

      {missing.length > 0 && (
        <div>
          <Typography.Text strong style={{ fontSize: 13 }}>
            缺失的关键动作（{missing.length} 条）
          </Typography.Text>
          <Space direction="vertical" size={6} style={{ width: '100%', marginTop: 6 }}>
            {missing.map((m, i) => (
              <Alert
                key={i}
                type="warning"
                showIcon
                message={`标准 ${m.standard_key} 还没真正达成`}
                description={
                  <Typography.Text style={{ fontSize: 12 }}>{m.why}</Typography.Text>
                }
              />
            ))}
          </Space>
        </div>
      )}

      {actions.length > 0 && (
        <div>
          <Typography.Text strong style={{ fontSize: 13 }}>
            关键建议
          </Typography.Text>
          <Space direction="vertical" size={4} style={{ width: '100%', marginTop: 4 }}>
            {actions.map((s, i) => (
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

      {(talks.length > 0 || plans.length > 0 || risks.length > 0 || standards.length > 0) && (
        <Collapse
          size="small"
          ghost
          defaultActiveKey={compact ? [] : ['talks', 'plans', 'risks', 'standards']}
          items={[
            ...(talks.length
              ? [
                  {
                    key: 'talks',
                    label: `话术（${talks.length} 条，可直接复制）`,
                    forceRender: true,
                    children: (
                      <Space direction="vertical" size={6} style={{ width: '100%' }}>
                        {talks.map((t, i) => (
                          <div
                            key={i}
                            style={{
                              background: 'var(--bg-layout)',
                              border: '1px solid var(--border)',
                              borderRadius: 8,
                              padding: '8px 10px',
                              fontSize: 13,
                            }}
                          >
                            {t}
                          </div>
                        ))}
                        <Button size="small" onClick={copyTalks}>
                          {copied ? '已复制' : '复制全部话术'}
                        </Button>
                      </Space>
                    ),
                  },
                ]
              : []),
            ...(plans.length
              ? [
                  {
                    key: 'plans',
                    label: `下周计划（${plans.length} 条）`,
                    forceRender: true,
                    children: (
                      <Space direction="vertical" size={4} style={{ width: '100%' }}>
                        {plans.map((p, i) => (
                          <Typography.Text key={i} style={{ fontSize: 13 }}>
                            {i + 1}. {p}
                          </Typography.Text>
                        ))}
                      </Space>
                    ),
                  },
                ]
              : []),
            ...(risks.length
              ? [
                  {
                    key: 'risks',
                    label: `风险提示（${risks.length} 条）`,
                    forceRender: true,
                    children: (
                      <Space direction="vertical" size={6} style={{ width: '100%' }}>
                        {risks.map((r, i) => (
                          <Space key={i} direction="vertical" size={2} style={{ width: '100%' }}>
                            <Typography.Text strong style={{ fontSize: 12 }}>
                              {riskLabel(r.risk_type)}
                            </Typography.Text>
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                              依据：{r.evidence}
                            </Typography.Text>
                          </Space>
                        ))}
                      </Space>
                    ),
                  },
                ]
              : []),
            ...(standards.length
              ? [
                  {
                    key: 'standards',
                    label: `诊断时点的标准完成情况（${structured.done_count}/${structured.total_count}）`,
                    forceRender: true,
                    children: (
                      <Table<AiDiagnosisStandard>
                        rowKey="key"
                        size="small"
                        pagination={false}
                        dataSource={standards}
                        columns={[
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
                            title: '状态',
                            dataIndex: 'is_done',
                            width: 90,
                            render: (v: boolean) =>
                              v ? <Tag color="success">已完成</Tag> : <Tag color="red">未完成</Tag>,
                          },
                          {
                            title: '证据 / 下一步',
                            dataIndex: 'evidence',
                            width: 240,
                            render: (v: string, r) =>
                              String(v ?? '').trim() ? (
                                <Typography.Text style={{ fontSize: 12 }}>{v}</Typography.Text>
                              ) : (
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                  下一步：{r.evidence_hint || '补充证据'}
                                </Typography.Text>
                              ),
                          },
                        ]}
                      />
                    ),
                  },
                ]
              : []),
          ]}
        />
      )}

      {warnings.length > 0 && (
        <Space direction="vertical" size={2} style={{ width: '100%' }}>
          {warnings.map((w, i) => (
            <Typography.Text key={i} type="warning" style={{ fontSize: 12 }}>
              · {w}
            </Typography.Text>
          ))}
        </Space>
      )}
    </Space>
  );
}

/** R1–R5 风险代码的中文口径（与 spec/08 S2-6 一致） */
function riskLabel(code: string): string {
  const map: Record<string, string> = {
    r1_budget_unclear: 'R1 资金预算不清',
    r2_trust: 'R2 信任不到',
    r3_decision_chain: 'R3 决策链不清',
    r4_demand_unclear: 'R4 需求不清',
    r5_project_rhythm: 'R5 项目节奏不清',
  };
  return map[code] ?? code;
}
