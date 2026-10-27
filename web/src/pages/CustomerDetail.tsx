import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Button,
  Card,
  Col,
  DatePicker,
  Form,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  AlertOutlined,
  EditOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  PhoneOutlined,
  PlusOutlined,
  TeamOutlined,
  WalletOutlined,
} from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';
import { createCustomerFollowup, getCustomerDetail, updateCustomer } from '@/api/customers';
import { listUsers } from '@/api/auth';
import { useDicts } from '@/hooks/useDicts';
import { useAuthStore } from '@/store/auth';
import GateBanner from '@/components/GateBanner';
import KpiCard from '@/components/KpiCard';
import PageHeader from '@/components/PageHeader';
import SimpleBar from '@/components/SimpleBar';
import { Footnote, NextStepGuide } from '@/components/Guide';
import { OrgTypeTag } from '@/components/tags';
import { BRAND } from '@/theme';
import { fenToWan, fmtDate, fmtDateTime, formatYuan, toLabelMap } from '@/utils/format';
import type { CustomerMetrics, CustomerProject } from '@/types';

/** 后端口径的兜底（`value_label` 为空时用；两者一致，不新增口径） */
function valueLabelOf(p: CustomerProject): string {
  if (p.value_label) return p.value_label;
  if (p.status === 'signed') return '已签约';
  if (p.status === 'lost') return '已丢单';
  return '过程中价值';
}

/** 价值标签的颜色（过程中价值=蓝、已签约=绿、已丢单=灰） */
function valueTagColor(label: string): string {
  if (label === '已签约') return 'success';
  if (label === '已丢单') return 'default';
  return 'blue';
}

/** 空指标兜底：详情接口失败时只拿到列表数据，此时仍要正确显示 0 与项目数 */
const EMPTY_METRICS: CustomerMetrics = {
  project_count: 0,
  in_progress_count: 0,
  signed_count: 0,
  lost_count: 0,
  in_progress_value_fen: 0,
  signed_value_fen: 0,
  lost_value_fen: 0,
  pending_risk_count: 0,
  stages: [],
};

/** 项目全名：客户名 / 项目名（项目名为空时只显示客户名） */
function projectTitle(p: { name: string; project_name?: string | null }): string {
  return p.project_name?.trim() ? `${p.name} / ${p.project_name}` : p.name;
}

/**
 * 客户详情（客户-项目两层模型）。
 *
 * 一屏回答三件事（判据 C2 少判断）：
 * 1. **这个客户值多少**：过程中价值 / 已签约额 / 已丢单额 / 待处理风险（口径就地可见）。
 * 2. **钱在哪个项目、哪个环节**：项目清单（可点进商机详情）+ 按阶段金额拆分。
 * 3. **这个客户整体聊到哪了**：客户级跟进时间线（不进任何项目环节）+ 联系人。
 */
export default function CustomerDetail() {
  const { id } = useParams();
  const customerId = id ?? '';
  const navigate = useNavigate();
  const qc = useQueryClient();
  const role = useAuthStore((s) => s.user?.role);
  const isAdmin = role === 'admin';
  const { dicts } = useDicts();
  const methodMap = toLabelMap(dicts.follow_methods);

  const [followOpen, setFollowOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [followForm] = Form.useForm<{
    method: string;
    content: string;
    followed_at?: Dayjs | null;
    next_follow_at?: Dayjs | null;
  }>();
  const [editForm] = Form.useForm<{ level?: string; remark?: string; owner_id?: string }>();

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['customer-detail', customerId],
    queryFn: () => getCustomerDetail(customerId),
    enabled: Boolean(customerId),
  });

  const customer = data?.customer ?? null;
  const metrics: CustomerMetrics = data?.metrics ?? EMPTY_METRICS;
  const projects = data?.projects ?? [];
  const contacts = data?.contacts ?? [];
  const followups = data?.followups ?? [];
  const risks = data?.risks ?? [];
  const contracts = data?.contracts ?? [];
  const byProject = data?.followup_counts?.by_project ?? {};

  /** 负责人下拉（仅管理员编辑弹窗需要时才拉） */
  const { data: usersData } = useQuery({
    queryKey: ['users', 'customer-owner-options'],
    queryFn: () => listUsers({ page: 1, page_size: 200 }),
    enabled: editOpen && isAdmin,
  });
  const ownerOptions = useMemo(
    () =>
      (usersData?.items ?? []).map((u) => ({
        value: u.id,
        label: `${u.name}（${u.username}）`,
      })),
    [usersData],
  );

  /* ---------------- 新增客户级跟进 ---------------- */

  const doFollow = useMutation({
    mutationFn: (v: {
      method: string;
      content: string;
      followed_at?: Dayjs | null;
      next_follow_at?: Dayjs | null;
    }) =>
      createCustomerFollowup(customerId, {
        method: v.method,
        content: v.content,
        //  只用日期、不提交时分秒：toISOString() 会按 UTC 换算导致北京时间差一天，禁止使用
        followed_at: v.followed_at ? dayjs(v.followed_at).format('YYYY-MM-DD') : undefined,
        next_follow_at: v.next_follow_at
          ? dayjs(v.next_follow_at).format('YYYY-MM-DD')
          : undefined,
      }),
    onSuccess: async () => {
      message.success('客户级跟进已记录（不进任何项目环节、不计环节时长）');
      setFollowOpen(false);
      followForm.resetFields();
      await qc.invalidateQueries({ queryKey: ['customer-detail', customerId] });
      qc.invalidateQueries({ queryKey: ['customers'] });
    },
  });

  /* ---------------- 维护客户信息（仅管理员） ---------------- */

  const doSaveCustomer = useMutation({
    mutationFn: (v: { level?: string; remark?: string; owner_id?: string }) =>
      updateCustomer(customerId, {
        level: v.level ?? null,
        remark: v.remark ?? null,
        owner_id: v.owner_id ?? null,
      }),
    onSuccess: async () => {
      message.success('客户信息已更新');
      setEditOpen(false);
      await qc.invalidateQueries({ queryKey: ['customer-detail', customerId] });
      qc.invalidateQueries({ queryKey: ['customers'] });
    },
  });

  if (isLoading) return <Spin />;

  if (isError || !customer) {
    return (
      <Card>
        <NextStepGuide
          title="客户详情没能加载出来"
          description={`原因：接口 /customers/{id} 与 /customers 都返回失败（${
            (error as { message?: string })?.message || '网络或服务异常'
          }）。可以先重试；若持续失败，请把这条提示连同客户名称发给系统管理员。`}
          actions={
            <Space wrap>
              <Button type="primary" onClick={() => refetch()}>
                重新加载
              </Button>
              <Button onClick={() => navigate('/customers')}>返回客户列表</Button>
              <Button onClick={() => navigate('/opportunities')}>去商机列表</Button>
            </Space>
          }
        />
      </Card>
    );
  }

  /* ---------------- 项目清单 ---------------- */

  const projectColumns: ColumnsType<CustomerProject> = [
    {
      title: '项目',
      key: 'project',
      width: 280,
      render: (_, r) => (
        <Space direction="vertical" size={0}>
          <a onClick={() => navigate(`/opportunities/${r.id}`)}>{projectTitle(r)}</a>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {[r.city, r.district].filter(Boolean).join(' ') || '区域未填'}
            {r.source_label ? ` · 来源 ${r.source_label}` : ''}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '当前环节',
      dataIndex: 'current_stage_label',
      width: 150,
      render: (v: string, r) => (
        <a onClick={() => navigate(`/opportunities/${r.id}`)}>
          {v || r.current_stage || '未进入闭环二'}
        </a>
      ),
    },
    {
      title: '金额',
      dataIndex: 'amount_fen',
      width: 130,
      align: 'right',
      render: (v: number, r) => (
        <Tooltip
          title={
            r.status === 'signed'
              ? `已签约金额取合同额：¥${formatYuan(v)}（合同额为空时回落预算额）`
              : `未签约金额取预算额：¥${formatYuan(v)}`
          }
        >
          <span style={{ fontWeight: 600 }}>¥{fenToWan(v)} 万</span>
        </Tooltip>
      ),
    },
    {
      title: '价值口径',
      key: 'value',
      width: 120,
      render: (_, r) => {
        const label = valueLabelOf(r);
        return (
          <Tooltip
            title={
              label === '过程中价值'
                ? '未签约 → 计入「过程中价值」（还没到手的机会）'
                : label === '已签约'
                  ? '已签约 → 计入「已签约额」（按合同额）'
                  : '已丢单 → 金额只作展示与复盘，不计入客户价值'
            }
          >
            <Tag color={valueTagColor(label)}>{label}</Tag>
          </Tooltip>
        );
      },
    },
    {
      title: '负责人',
      dataIndex: 'owner_name',
      width: 100,
      render: (v: string | null) => v || <Typography.Text type="secondary">未指派</Typography.Text>,
    },
    {
      title: '项目级跟进',
      key: 'follows',
      width: 118,
      align: 'right',
      render: (_, r) => (
        <Tooltip title="该项目自己的跟进条数（项目级跟进会进环节；客户级跟进不计入这里）">
          <a onClick={() => navigate(`/opportunities/${r.id}`)}>{byProject[r.id] ?? 0} 条</a>
        </Tooltip>
      ),
    },
    {
      title: '',
      key: 'op',
      width: 96,
      render: (_, r) => (
        <Button size="small" onClick={() => navigate(`/opportunities/${r.id}`)}>
          进商机详情
        </Button>
      ),
    },
  ];

  const stageItems = (metrics.stages ?? []).map((s) => ({
    label: s.stage_label || s.stage,
    value: Math.round(s.amount_fen / 10000) / 100,
    extra: `${s.project_count} 个项目 · ¥${fenToWan(s.amount_fen)} 万${s.signed ? ' · 已签约' : ''}`,
  }));

  /* ---------------- 跟进 / 风险 / 合同 / 联系人 ---------------- */

  const followupTimeline = followups.length ? (
    <Timeline
      items={followups.map((f) => ({
        color: BRAND,
        children: (
          <Space direction="vertical" size={2} style={{ width: '100%' }}>
            <Space size={6} wrap>
              <Typography.Text strong>{f.user_name || '—'}</Typography.Text>
              <Tag color="green">{f.scope_label || '客户级（不进环节）'}</Tag>
              <Tag>{f.method_label || methodMap[f.method] || f.method}</Tag>
              {f.next_follow_at && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  下次跟进 {fmtDate(f.next_follow_at)}
                </Typography.Text>
              )}
            </Space>
            <Typography.Text>{f.content}</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {fmtDateTime(f.followed_at)}
            </Typography.Text>
          </Space>
        ),
      }))}
    />
  ) : (
    <NextStepGuide
      compact
      title="暂无客户级跟进"
      description="渠道活动、跨项目复盘、决策链澄清这类「不针对某一个项目」的沟通记在这里——它不进任何项目环节、也不计环节时长，所以不会污染 7 环节的推进判定。"
      actions={
        <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => setFollowOpen(true)}>
          新增客户级跟进
        </Button>
      }
    />
  );

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title={customer.name}
        subtitle="一个客户的整体价值与项目全景：先看哪个项目卡住、哪个已签约，再点进商机补齐门槛与证据。"
        demo
        tags={
          <Space size={6} wrap>
            <OrgTypeTag orgType={customer.org_type} />
            {customer.level && <Tag color="blue">{customer.level} 级客户</Tag>}
            {metrics.pending_risk_count > 0 && (
              <Tag color="red">待处理风险 {metrics.pending_risk_count}</Tag>
            )}
          </Space>
        }
        meta={[
          { label: '区域', value: customer.region || [customer.province, customer.city, customer.district].filter(Boolean).join('') || '未填' },
          { label: '负责人', value: customer.owner_name || '未指派' },
          { label: '项目数', value: `${metrics.project_count} 个` },
          { label: '建档时间', value: fmtDate(customer.created_at) },
        ]}
        actions={
          <Space wrap>
            {isAdmin && (
              <Button
                icon={<EditOutlined />}
                onClick={() => {
                  editForm.setFieldsValue({
                    level: customer.level ?? undefined,
                    remark: customer.remark ?? undefined,
                    owner_id: customer.owner_id ?? undefined,
                  });
                  setEditOpen(true);
                }}
              >
                编辑客户信息
              </Button>
            )}
            <Button onClick={() => navigate('/opportunities')}>去商机列表</Button>
          </Space>
        }
      />

      {/* 一行指标卡：口径就地可见 */}
      <Row gutter={[12, 12]}>
        <Col xs={24} sm={12} xl={6}>
          <KpiCard
            label="过程中价值"
            value={`¥${fenToWan(metrics.in_progress_value_fen)}`}
            suffix="万"
            icon={<TeamOutlined />}
            tone="amber"
            hint={`未签约项目金额合计（进行中 ${metrics.in_progress_count} 个）；已丢单不计入`}
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <KpiCard
            label="已签约额"
            value={`¥${fenToWan(metrics.signed_value_fen)}`}
            suffix="万"
            icon={<WalletOutlined />}
            tone="green"
            hint={`已签约 ${metrics.signed_count} 个项目的合同额合计（合同额为空时回落到预算额）`}
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <KpiCard
            label="已丢单金额"
            value={`¥${fenToWan(metrics.lost_value_fen)}`}
            suffix="万"
            icon={<FileTextOutlined />}
            tone="gray"
            hint={`已丢单 ${metrics.lost_count} 个项目的金额，仅作展示与复盘，不计入任何价值口径`}
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <KpiCard
            label="待处理风险"
            value={metrics.pending_risk_count}
            suffix="条"
            icon={<AlertOutlined />}
            tone={metrics.pending_risk_count > 0 ? 'red' : 'brand'}
            danger={metrics.pending_risk_count > 0}
            hint="该客户名下项目的「待处理」预警条数；详情页风险 Tab 与「风险与卡单中心」都可处置"
          />
        </Col>
      </Row>

      <Row gutter={12}>
        {/* 左：项目清单 + 阶段拆分 */}
        <Col xs={24} xl={16}>
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Card
              title={
                <Space size={8}>
                  <FolderOpenOutlined />
                  <span>项目清单</span>
                  <Typography.Text type="secondary" style={{ fontSize: 13, fontWeight: 400 }}>
                    {metrics.project_count} 个项目 · 每个项目各自走 7 环节
                  </Typography.Text>
                </Space>
              }
              extra={
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  点项目名进商机详情
                </Typography.Text>
              }
              styles={{ body: { paddingTop: 12 } }}
            >
              <Table<CustomerProject>
                rowKey="id"
                size="small"
                dataSource={projects}
                columns={projectColumns}
                pagination={false}
                locale={{
                  emptyText: (
                    <NextStepGuide
                      compact
                      title="这个客户名下还没有项目商机"
                      description="客户档案是线索录入时自动建的；等它的线索被判定为有效商机，就会作为项目出现在这里。"
                      actions={
                        <Button size="small" type="primary" onClick={() => navigate('/leads')}>
                          去线索池
                        </Button>
                      }
                    />
                  ),
                }}
              />
              <Footnote style={{ marginTop: 10 }}>
                金额口径：未签约取<strong>预算额</strong>并计入「过程中价值」；已签约取<strong>合同额</strong>并计入「已签约额」；
                已丢单金额只展示、不计价值。一个客户下可以有多个项目（不同预算年度 / 资金来源 / 功能点各算一个项目）。
              </Footnote>
            </Card>

            <Card
              title={
                <Space size={8}>
                  <span>按阶段的金额拆分</span>
                  <Typography.Text type="secondary" style={{ fontSize: 13, fontWeight: 400 }}>
                    钱压在哪个环节，一眼看到
                  </Typography.Text>
                </Space>
              }
            >
              {stageItems.length ? (
                <SimpleBar
                  items={stageItems}
                  color={BRAND}
                  emptyText="该客户名下项目都还没进入环节"
                />
              ) : (
                <NextStepGuide
                  compact
                  title="没有可拆分的阶段金额"
                  description="该客户名下还没有进入 7 环节的项目（或项目金额为 0）。进入闭环二后这里会按环节显示金额分布。"
                />
              )}
              <Footnote style={{ marginTop: 10 }}>
                口径：按每个项目的<strong>当前环节</strong>归集金额，逐环节相加（含已签约项目，绿色「已签约」标记表示该项目已签约）。
                条形长度按本客户内最大值归一化，只用于对比环节之间的相对大小。
              </Footnote>
            </Card>
          </Space>
        </Col>

        {/* 右：客户级跟进 + 联系人 + 风险 + 合同 */}
        <Col xs={24} xl={8}>
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Card
              size="small"
              title={
                <Space size={6} wrap>
                  <span>客户级跟进</span>
                  <Tag color="green">不进环节</Tag>
                </Space>
              }
              extra={
                <Button
                  type="primary"
                  size="small"
                  icon={<PlusOutlined />}
                  onClick={() => {
                    followForm.setFieldsValue({
                      method: (dicts.follow_methods ?? [])[0]?.value ?? 'phone',
                      followed_at: dayjs(),
                    });
                    setFollowOpen(true);
                  }}
                >
                  新增
                </Button>
              }
            >
              {followupTimeline}
            </Card>

            <Card size="small" title={`联系人${contacts.length ? `（${contacts.length}）` : ''}`}>
              {contacts.length ? (
                <Space direction="vertical" size={10} style={{ width: '100%' }}>
                  {contacts.map((c) => (
                    <div key={c.id}>
                      <Space size={6} wrap>
                        <Typography.Text strong>{c.name}</Typography.Text>
                        {c.position && <Tag>{c.position}</Tag>}
                        {c.is_primary && <Tag color="blue">关键决策人</Tag>}
                      </Space>
                      <div>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          <PhoneOutlined /> {c.phone || '未填电话'}
                          {c.wechat ? ` · 微信 ${c.wechat}` : ''}
                        </Typography.Text>
                      </div>
                    </div>
                  ))}
                </Space>
              ) : (
                <NextStepGuide
                  compact
                  title="暂无联系人"
                  description="联系人挂在该客户的项目（商机）上：去商机详情「客户信息」里补录关键决策人，补齐后才能推进环节一 → 环节二。"
                  actions={
                    projects[0] ? (
                      <Button size="small" onClick={() => navigate(`/opportunities/${projects[0].id}`)}>
                        去第一个项目补录
                      </Button>
                    ) : (
                      <Button size="small" onClick={() => navigate('/leads')}>
                        去线索池
                      </Button>
                    )
                  }
                />
              )}
            </Card>

            <Card
              size="small"
              title={`风险（待处理 ${metrics.pending_risk_count}）`}
              extra={<a onClick={() => navigate('/risks')}>风险中心 ›</a>}
            >
              {risks.length ? (
                <Space direction="vertical" size={10} style={{ width: '100%' }}>
                  {risks.slice(0, 5).map((r) => (
                    <div key={r.id}>
                      <Space size={6} wrap>
                        <Tag color="volcano">{r.risk_type_label || r.kind_label || '风险'}</Tag>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {fmtDate(r.created_at)}
                        </Typography.Text>
                      </Space>
                      <div>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {r.description || `触发口径：${r.trigger_rule || '—'}`}
                        </Typography.Text>
                      </div>
                      <Space size={10} wrap>
                        <a
                          style={{ fontSize: 12 }}
                          onClick={() => navigate(`/opportunities/${r.lead_id}`)}
                        >
                          去对应项目处置
                        </a>
                        <Tooltip title={`触发口径：${r.trigger_rule || '—'}`}>
                          <Typography.Text type="secondary" style={{ fontSize: 12, cursor: 'help' }}>
                            ⓘ 触发口径
                          </Typography.Text>
                        </Tooltip>
                      </Space>
                    </div>
                  ))}
                  {risks.length > 5 && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      还有 {risks.length - 5} 条，去风险中心看全部
                    </Typography.Text>
                  )}
                </Space>
              ) : (
                <NextStepGuide
                  compact
                  title="暂无待处理风险"
                  description="没有未处理预警。看项目觉得不踏实（预算 / 信任 / 决策链 / 需求 / 节奏）可以去商机详情登记 R1–R5 风险。"
                />
              )}
            </Card>

            <Card size="small" title={`合同（${contracts.length}）`}>
              {contracts.length ? (
                <Table
                  rowKey="id"
                  size="small"
                  dataSource={contracts}
                  pagination={false}
                  columns={[
                    {
                      title: '合同号',
                      dataIndex: 'contract_no',
                      render: (v: string, r) => (
                        <a onClick={() => navigate(`/contracts/${r.id}`)}>{v}</a>
                      ),
                    },
                    {
                      title: '金额',
                      dataIndex: 'amount_fen',
                      align: 'right' as const,
                      render: (v: number) => `¥${fenToWan(v)} 万`,
                    },
                    {
                      title: '状态',
                      dataIndex: 'status',
                      width: 90,
                      render: (v: string) => <Tag>{v || '-'}</Tag>,
                    },
                  ]}
                />
              ) : (
                <NextStepGuide
                  compact
                  title="暂无合同"
                  description="该客户名下项目还没有签约合同。项目走到「七 合同签约」并生成合同后，这里会自动出现。"
                  actions={
                    <Button size="small" onClick={() => navigate('/contracts')}>
                      去客户 / 合同
                    </Button>
                  }
                />
              )}
            </Card>
          </Space>
        </Col>
      </Row>

      <Footnote>
        「客户」与「项目」是两层：一个客户（学校 / 教育局）名下可以有多个项目商机，每个项目各自走 7 环节、各有金额。
        客户整体价值只算「进行中 + 已签约」：过程中价值 = 未签约项目金额合计，已签约额按合同额计，已丢单金额剔除（仅展示）。
        客户级跟进（渠道活动 / 跨项目复盘 / 决策链澄清）<strong>不进任何项目环节、不计环节时长</strong>，因此不影响卡单与节奏判定。
        {isAdmin ? '客户信息（等级 / 备注 / 负责人）仅管理员可改。' : '客户信息（等级 / 备注 / 负责人）仅管理员可改；需要变更请联系管理员。'}
      </Footnote>

      {/* 新增客户级跟进 */}
      <Modal
        title="新增客户级跟进"
        open={followOpen}
        onCancel={() => setFollowOpen(false)}
        onOk={() => followForm.submit()}
        confirmLoading={doFollow.isPending}
        okText="保存"
        destroyOnClose
      >
        <GateBanner
          tone="tip"
          style={{ marginBottom: 12 }}
          title="客户级跟进不进任何项目环节"
          items={[
            '用于渠道活动、跨项目复盘、决策链澄清这类「不针对某一个项目」的沟通；',
            '它不会改变任何项目的环节、不计环节时长，也不会被当成项目推进证据；',
            '针对某个项目做了什么，请在该项目的商机详情里记项目级跟进。',
          ]}
        />
        <Form
          form={followForm}
          layout="vertical"
          onFinish={(v) => doFollow.mutate(v)}
          initialValues={{ method: (dicts.follow_methods ?? [])[0]?.value ?? 'phone', followed_at: dayjs() }}
        >
          <Form.Item name="method" label="方式" rules={[{ required: true, message: '请选择跟进方式' }]}>
            <Select placeholder="请选择" options={dicts.follow_methods ?? []} />
          </Form.Item>
          <Form.Item
            name="content"
            label="内容"
            rules={[{ required: true, whitespace: true, message: '请填写跟进内容（至少 1 个字）' }]}
            extra="写清「谁 + 什么时候 + 谈了什么 + 有无确认人」，后续跨项目复盘时才有依据。"
          >
            <Input.TextArea rows={4} placeholder="如：拜访校长与信息中心主任，澄清明年 600 万预算申报窗口与决策链" />
          </Form.Item>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="followed_at" label="跟进日期">
                <DatePicker style={{ width: '100%' }} placeholder="选择日期（不含时分秒）" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="next_follow_at" label="下次跟进日期（选填）">
                <DatePicker style={{ width: '100%' }} placeholder="选择日期（不含时分秒）" />
              </Form.Item>
            </Col>
          </Row>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            日期只按天记录并原样提交（YYYY-MM-DD），不涉及时区换算。
          </Typography.Text>
        </Form>
      </Modal>

      {/* 编辑客户信息（仅管理员） */}
      <Modal
        title="编辑客户信息"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => editForm.submit()}
        confirmLoading={doSaveCustomer.isPending}
        okText="保存"
        destroyOnClose
      >
        <Form form={editForm} layout="vertical" onFinish={(v) => doSaveCustomer.mutate(v)}>
          <Form.Item name="level" label="客户等级" extra="A / B / C，选填；用于内部优先级参考。">
            <Select
              allowClear
              placeholder="未分级"
              options={[
                { value: 'A', label: 'A 级' },
                { value: 'B', label: 'B 级' },
                { value: 'C', label: 'C 级' },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="owner_id"
            label="客户负责人"
            extra="客户级负责人，与各项目的归属人相互独立。"
          >
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder="请选择"
              options={ownerOptions}
            />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={3} placeholder="客户层面的补充说明（如决策链、预算年度）" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
