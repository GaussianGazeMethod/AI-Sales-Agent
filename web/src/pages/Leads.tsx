import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  AutoComplete,
  Button,
  Card,
  Col,
  Form,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  InboxOutlined,
  PlusOutlined,
  ProjectOutlined,
  UploadOutlined,
  WarningFilled,
} from '@ant-design/icons';
import { assignLeads, listLeads, loadView, recycleLeads } from '@/api/leads';
import { listUsers } from '@/api/auth';
import { useDicts } from '@/hooks/useDicts';
import { useAuthStore } from '@/store/auth';
import { fenToWan, fmtDateTime } from '@/utils/format';
import { FollowCountdown, LeadStatusTag, OrgTypeTag, SourceTag } from '@/components/tags';
import PageHeader from '@/components/PageHeader';
import { Footnote, NextStepGuide } from '@/components/Guide';
import type { Lead, LeadQuery, LeadView } from '@/types';

interface FilterValues {
  keyword?: string;
  status?: string;
  source?: string;
  org_type?: string;
  province?: string;
  city?: string;
  owner_id?: string;
}

const VIEWS: { key: LeadView; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'unassigned', label: '未指派' },
  { key: 'mine', label: '我的' },
  { key: 'pool', label: '公海' },
  { key: 'duplicate', label: '疑似重复' },
  { key: 'invalid', label: '无效' },
];

/**
 * 1.1 线索池（对齐 UIdesign/02-线索池与分配.png）
 *
 * 骨架：标题 + Excel 导入 / 新增线索 → Tabs（未指派带计数）→ 一行筛选
 *      → 左宽表（多选 + 底部批量条）/ 右「手工分配」面板（负责人负载 + 指派理由 + 规则）
 *
 * 判据：
 *   C2 少判断 —— 每个禁用按钮都有 Tooltip 说"先做什么"；空态给下一步入口
 *   C3 口径可见 —— 负载数字、SLA 天数、来源口径就地可查（Tooltip + 页脚）
 *   C4 赋能边界 —— **由管理者手工分配，系统不自动推荐**（标题副文案 + 右栏 + 页脚三处可见）
 */
export default function Leads() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const role = useAuthStore((s) => s.user?.role);
  const isManager = role === 'admin' || role === 'market_manager';
  const { dicts } = useDicts();
  const [params, setParams] = useSearchParams();
  const view = (params.get('view') as LeadView) || 'all';
  const [filters, setFilters] = useState<FilterValues>({});
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [selected, setSelected] = useState<string[]>([]);
  const [assignOpen, setAssignOpen] = useState(false);
  const [recycleOpen, setRecycleOpen] = useState(false);
  const [assignForm] = Form.useForm<{ owner_id: string; remark: string }>();
  const [recycleForm] = Form.useForm<{ remark: string }>();
  /** 右栏「手工分配」面板（与 Assign.tsx 同交互：选人 + 理由 + 确认） */
  const [panelOwner, setPanelOwner] = useState<string | undefined>();
  const [panelReason, setPanelReason] = useState('');

  const query = useMemo<LeadQuery>(
    () => ({ view, ...filters, sort: 'recent', page, page_size: pageSize }),
    [view, filters, page, pageSize],
  );

  const { data, isLoading } = useQuery({
    queryKey: ['leads', query],
    queryFn: () => listLeads(query),
  });

  /** 归属人下拉（管理员接口拿全量用户，失败时不影响列表） */
  const { data: userData } = useQuery({
    queryKey: ['users', 'all'],
    queryFn: () => listUsers({ page: 1, page_size: 200 }),
    enabled: isManager,
    retry: false,
  });
  const ownerOptions = (userData?.items ?? []).map((u) => ({ value: u.id, label: u.name }));

  /** 负责人负载（右栏参考数；失败不影响指派） */
  const { data: load } = useQuery({
    queryKey: ['lead-load-view'],
    queryFn: loadView,
    enabled: isManager,
    retry: false,
  });
  const loadRows = load?.items ?? [];

  /** 「未指派」Tab 计数：单独取一次口径一致的总数（不随当前筛选变化） */
  const { data: unassignedData } = useQuery({
    queryKey: ['leads', 'unassigned-count'],
    queryFn: () => listLeads({ view: 'unassigned', page: 1, page_size: 1 }),
    enabled: isManager,
    retry: false,
  });
  const unassignedCount = unassignedData?.total ?? 0;

  /** 8 类来源口径：从字典动态取，不硬编码文案 */
  const sourceCatalog = (dicts.lead_sources ?? []).map((s) => s.label).join(' / ');

  const refresh = () => {
    setSelected([]);
    setPanelOwner(undefined);
    setPanelReason('');
    qc.invalidateQueries({ queryKey: ['leads'] });
    qc.invalidateQueries({ queryKey: ['lead-load-view'] });
  };

  const doAssign = useMutation({
    mutationFn: (v: { owner_id: string; remark: string }) =>
      assignLeads({ lead_ids: selected, owner_id: v.owner_id, remark: v.remark }),
    onSuccess: (r) => {
      message.success(`已指派 ${r.affected} 条线索`);
      setAssignOpen(false);
      assignForm.resetFields();
      refresh();
    },
  });

  const doRecycle = useMutation({
    mutationFn: (v: { remark: string }) => recycleLeads({ lead_ids: selected, remark: v.remark }),
    onSuccess: (r) => {
      message.success(`已回收 ${r.affected} 条线索至公海`);
      setRecycleOpen(false);
      recycleForm.resetFields();
      refresh();
    },
  });

  /** 列序照设计稿：客户名称 / 地区 / 来源 / 类型 / 状态 / 最近跟进（+ 本系统的 SLA 与预算） */
  const columns: ColumnsType<Lead> = [
    {
      title: '客户 / 项目',
      dataIndex: 'name',
      width: 220,
      ellipsis: true,
      render: (v: string, r) => (
        <Space direction="vertical" size={0}>
          <Space size={4}>
            <a onClick={() => navigate(`/leads/${r.id}`)}>{v}</a>
            {r.is_suspect_duplicate && (
              <Tooltip title="疑似重复客户：与已有线索在「客户名称 + 省市区」上撞单，待人工裁决">
                <Tag color="orange">疑似重复</Tag>
              </Tooltip>
            )}
          </Space>
          {/* 项目名（客户-项目两层模型）：一个客户名下可以有多个项目；没填就不显示这一行 */}
          {r.project_name?.trim() && (
            <Tooltip title="项目名：同一客户（学校 / 教育局）名下的多个项目，各自走一遍 7 环节、各有金额">
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                <ProjectOutlined /> {r.project_name}
              </Typography.Text>
            </Tooltip>
          )}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            演示数据
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '地区',
      key: 'area',
      width: 150,
      render: (_, r) => (
        <Space direction="vertical" size={0}>
          <span style={{ fontSize: 13 }}>
            {[r.province, r.city].filter(Boolean).join(' ') || '-'}
          </span>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {r.district || ''}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '来源',
      dataIndex: 'source',
      width: 110,
      render: (v: string) => <SourceTag source={v} />,
    },
    {
      title: '类型',
      dataIndex: 'org_type',
      width: 100,
      render: (v: string) => <OrgTypeTag orgType={v} />,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (v: string) => <LeadStatusTag status={v} />,
    },
    {
      title: '归属人',
      dataIndex: 'owner_name',
      width: 96,
      render: (v: string | null) => v || <Typography.Text type="secondary">未指派</Typography.Text>,
    },
    {
      title: (
        <Tooltip title="指派后 1 个工作日内需首次跟进（按工作日，截止时间由后端算好）">
          <span>首次跟进时限</span>
        </Tooltip>
      ),
      key: 'countdown',
      width: 130,
      render: (_, r) => (
        <FollowCountdown
          assignAt={r.assign_at}
          firstFollowAt={r.first_follow_at}
          deadline={r.first_follow_deadline}
        />
      ),
    },
    {
      title: '预算（万）',
      dataIndex: 'budget_amount_fen',
      width: 100,
      align: 'right',
      render: (v: number | null) => fenToWan(v),
    },
    {
      title: (
        <Tooltip title="最近一次跟进（任一方式）的时间；用于判断是否临近 7 个工作日回收线">
          <span>最近跟进</span>
        </Tooltip>
      ),
      dataIndex: 'last_follow_at',
      width: 150,
      render: (v: string | null) => fmtDateTime(v),
    },
    {
      title: '操作',
      key: 'op',
      width: 110,
      fixed: 'right',
      render: (_, r) => (
        <Space size={4}>
          <a onClick={() => navigate(`/leads/${r.id}`)}>详情</a>
          {(isManager || r.status !== 'signed') && (
            <a onClick={() => navigate(`/leads/${r.id}/edit`)}>编辑</a>
          )}
        </Space>
      ),
    },
  ];

  const canBulk = isManager && selected.length > 0;

  /** 右栏「手工分配」面板（设计稿右 30%：负责人负载 + 指派原因 + 确认 + 规则） */
  const assignPanel = (
    <Card
      size="small"
      title="手工分配"
      extra={<span style={{ fontSize: 12, color: '#667085' }}>系统不自动推荐</span>}
      styles={{ body: { paddingTop: 12 } }}
    >
      <Typography.Text strong style={{ fontSize: 13 }}>
        负责人负载
      </Typography.Text>
      <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12, marginTop: 2 }}>
        数字仅为参考，不参与自动推荐，也不作为考核依据。
      </Typography.Text>
      <div style={{ marginTop: 10 }}>
        {loadRows.length === 0 ? (
          <NextStepGuide
            compact
            title="暂无成员负载数据"
            description="负载视图没取到成员时，仍可用下方下拉框指定归属人；不影响正常指派。"
          />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {loadRows.map((m) => {
              const active = panelOwner === m.user_id;
              return (
                <div
                  key={m.user_id}
                  onClick={() => setPanelOwner(active ? undefined : m.user_id)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 8,
                    padding: '8px 12px',
                    border: `1px solid ${active ? '#465FFF' : '#EAECF0'}`,
                    background: active ? '#F5F8FF' : '#fff',
                    borderRadius: 8,
                    cursor: 'pointer',
                  }}
                >
                  <Space size={8}>
                    <span
                      style={{
                        width: 14,
                        height: 14,
                        borderRadius: '50%',
                        border: `2px solid ${active ? '#465FFF' : '#D0D5DD'}`,
                        background: active ? '#465FFF' : '#fff',
                        boxShadow: active ? 'inset 0 0 0 2px #fff' : undefined,
                        display: 'inline-block',
                      }}
                    />
                    <Typography.Text strong>{m.name}</Typography.Text>
                  </Space>
                  <Space size={10}>
                    <Tooltip title="负载视图口径：该成员状态为「跟进中」的线索数；仅为参考，不构成考核">
                      <Typography.Text style={{ fontSize: 12, color: '#667085' }}>
                        在跟进 <strong style={{ color: '#182230' }}>{m.following_count}</strong>
                      </Typography.Text>
                    </Tooltip>
                    <Tooltip title="负载视图口径：该成员已指派但尚未首次跟进（状态=已指派）的线索数——这批复最可能压到首次跟进 SLA">
                      <Typography.Text style={{ fontSize: 12, color: '#667085' }}>
                        待首次跟进 <strong style={{ color: '#F79009' }}>{m.assigned_count}</strong>
                      </Typography.Text>
                    </Tooltip>
                  </Space>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div style={{ marginTop: 12 }}>
        <Typography.Text strong style={{ fontSize: 13 }}>
          指派理由（选填）
        </Typography.Text>
        <Input.TextArea
          style={{ marginTop: 4 }}
          rows={3}
          maxLength={200}
          showCount
          value={panelReason}
          onChange={(e) => setPanelReason(e.target.value)}
          placeholder="请输入指派理由，便于后续跟进与留痕"
        />
      </div>

      <Tooltip
        title={
          selected.length === 0
            ? '先在左侧勾选要指派的线索（可多选）'
            : !panelOwner
              ? '先在上方选择负责人'
              : undefined
        }
      >
        <span>
          <Button
            type="primary"
            block
            style={{ marginTop: 12 }}
            disabled={!canBulk || !panelOwner}
            loading={doAssign.isPending}
            onClick={() => {
              if (!panelOwner) return;
              const name = ownerOptions.find((o) => o.value === panelOwner)?.label ?? '';
              doAssign.mutate({
                owner_id: panelOwner,
                remark: panelReason.trim() || `手工分配${name ? `给 ${name}` : ''}（${selected.length} 条）`,
              });
            }}
          >
            确认指派 {selected.length} 条
          </Button>
        </span>
      </Tooltip>
      {selected.length === 0 && (
        <Typography.Text
          type="secondary"
          style={{ display: 'block', fontSize: 12, marginTop: 6 }}
        >
          左侧勾选后按钮才可用；也可在表格底部用「批量指派」。
        </Typography.Text>
      )}

      <div
        style={{
          marginTop: 12,
          padding: '12px 14px',
          background: '#FEF7EC',
          border: '1px solid #FBE2BF',
          borderRadius: 8,
          fontSize: 12,
          lineHeight: 1.9,
          color: '#B54708',
        }}
      >
        <Space size={6} style={{ marginBottom: 2 }}>
          <WarningFilled style={{ color: '#F79009' }} />
          <strong>指派规则</strong>
        </Space>
        <ol style={{ margin: '4px 0 0', paddingLeft: 18 }}>
          <li>指派后 1 个工作日内首次跟进（按工作日）；</li>
          <li>7 个工作日未跟进自动回收至公海；</li>
          <li>分配 / 改派 / 回收全部留痕，可在线索详情查。</li>
        </ol>
      </div>
    </Card>
  );

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title="线索池"
        subtitle="由管理者手工分配，系统不自动推荐负责人——先处理「未指派」，再按区域与负载分给专员；分配后 1 个工作日内必须首次跟进。"
        demo
        actions={
          <Space wrap>
            <Button icon={<UploadOutlined />} onClick={() => navigate('/imports')}>
              Excel导入
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/leads/new')}>
              新增线索
            </Button>
          </Space>
        }
      />

      <Card styles={{ body: { paddingTop: 12 } }}>
        <Tabs
          activeKey={view}
          onChange={(k) => {
            setPage(1);
            setSelected([]);
            setParams({ view: k });
          }}
          items={VIEWS.map((v) => ({
            key: v.key,
            // 「未指派」带计数（设计稿写法：未指派（18））；单独取一次口径一致的总数
            label: v.key === 'unassigned' ? `${v.label}（${unassignedCount}）` : v.label,
          }))}
        />

        <Form
          layout="inline"
          style={{ marginBottom: 12, rowGap: 8 }}
          onFinish={(v: FilterValues) => {
            setPage(1);
            setFilters({ ...v, owner_id: v.owner_id || undefined });
          }}
        >
          <Form.Item name="source" label="来源">
            <Select
              allowClear
              placeholder="请选择来源"
              style={{ width: 160 }}
              options={dicts.lead_sources ?? []}
            />
          </Form.Item>
          <Form.Item name="org_type" label="类型">
            <Select
              allowClear
              placeholder="请选择类型"
              style={{ width: 140 }}
              options={dicts.org_types ?? []}
            />
          </Form.Item>
          <Form.Item label="省 / 市 / 区县">
            <Space size={6}>
              <Form.Item name="province" noStyle>
                <AutoComplete
                  allowClear
                  placeholder="省"
                  style={{ width: 100 }}
                  options={(dicts.provinces ?? []).map((p) => ({ value: p }))}
                />
              </Form.Item>
              <Form.Item name="city" noStyle>
                <Input placeholder="市" allowClear style={{ width: 92 }} />
              </Form.Item>
              <Form.Item name="keyword" noStyle>
                <Input placeholder="区县 / 客户名" allowClear style={{ width: 124 }} />
              </Form.Item>
            </Space>
          </Form.Item>
          {isManager && (
            <Form.Item name="owner_id" label="归属人">
              <Select allowClear placeholder="请选择归属人" style={{ width: 140 }} options={ownerOptions} />
            </Form.Item>
          )}
          <Form.Item name="status" label="状态">
            <Select
              allowClear
              placeholder="全部状态"
              style={{ width: 130 }}
              options={dicts.lead_status ?? []}
            />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                查询
              </Button>
              <Button
                onClick={() => {
                  setFilters({});
                  setPage(1);
                }}
              >
                重置
              </Button>
            </Space>
          </Form.Item>
        </Form>

        <Row gutter={16} align="top">
          <Col xs={24} xl={isManager ? 16 : 24}>
            <Table
              rowKey="id"
              size="small"
              loading={isLoading}
              columns={columns}
              dataSource={data?.items ?? []}
              scroll={{ x: 1180 }}
              rowSelection={
                isManager
                  ? {
                      selectedRowKeys: selected,
                      onChange: (keys) => setSelected(keys.map((k) => String(k))),
                    }
                  : undefined
              }
              pagination={{
                current: page,
                pageSize,
                total: data?.total ?? 0,
                showSizeChanger: true,
                showTotal: (t) => `共 ${t} 条`,
                onChange: (p, ps) => {
                  setPage(p);
                  setPageSize(ps);
                },
              }}
              locale={{
                emptyText: (
                  <NextStepGuide
                    title={view === 'unassigned' ? '未指派池已清空' : '这个视图下暂无线索'}
                    description={
                      view === 'unassigned'
                        ? '所有线索都分出去了。可以去「全部」或「公海」看看，也可以用 Excel 批量导入新名单。'
                        : '换个视图或清掉筛选条件再试；也可以用 Excel 导入一批新线索。'
                    }
                    actions={
                      <Space wrap>
                        {view !== 'unassigned' && (
                          <Button onClick={() => setParams({ view: 'unassigned' })}>去未指派池</Button>
                        )}
                        <Button type="primary" onClick={() => navigate('/leads/new')}>
                          新增线索
                        </Button>
                        <Button icon={<InboxOutlined />} onClick={() => navigate('/imports')}>
                          Excel 导入
                        </Button>
                      </Space>
                    }
                  />
                ),
              }}
            />

            {/*  底部批量条：已选 N 条 + 批量指派（设计稿形态） */}
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 12,
                marginTop: 12,
                padding: '10px 14px',
                background: selected.length ? '#F5F8FF' : '#FAFBFC',
                border: '1px solid #EAECF0',
                borderRadius: 8,
              }}
            >
              <Space wrap>
                <Typography.Text strong>已选 {selected.length} 条</Typography.Text>
                {isManager ? (
                  <>
                    <Tooltip title={selected.length ? undefined : '先勾选要指派的线索，再点这里'}>
                      <span>
                        <Button
                          type="primary"
                          disabled={!canBulk}
                          onClick={() => {
                            assignForm.resetFields();
                            setAssignOpen(true);
                          }}
                        >
                          批量指派
                        </Button>
                      </span>
                    </Tooltip>
                    <Tooltip title={selected.length ? undefined : '先勾选要回收的线索'}>
                      <span>
                        <Button
                          danger
                          disabled={!canBulk}
                          onClick={() => {
                            recycleForm.resetFields();
                            setRecycleOpen(true);
                          }}
                        >
                          回收至公海
                        </Button>
                      </span>
                    </Tooltip>
                    {selected.length > 0 && <a onClick={() => setSelected([])}>清空选择</a>}
                  </>
                ) : (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    批量指派 / 回收仅市场部管理人员与管理员可用；需要改派请找直属管理者。
                  </Typography.Text>
                )}
              </Space>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                共 {data?.total ?? 0} 条
              </Typography.Text>
            </div>
          </Col>

          {isManager && (
            <Col xs={24} xl={8}>
              {assignPanel}
            </Col>
          )}
        </Row>
      </Card>

      <Footnote>
        分配权在管理者，<strong>系统不自动推荐、不自动分配负责人</strong>——右栏负载数字仅供参考，决定权在人。
        来源共 8 类：
        {sourceCatalog || '公司客户池 / 客户转介绍 / 渠道商 / 内部人员介绍 / 协会·机构 / 市场活动 / 公开数据平台 / 运营商'}。
        首次跟进按<strong>工作日</strong>计算（指派后 1 个工作日内）；7 个工作日未跟进自动回收至公海。
        疑似重复 = 与已有线索在「客户名称 + 省 / 市 / 区」上撞单且尚未人工裁决，请先裁决再跟进（只提示、不拦截录入）。
      </Footnote>

      <Modal
        title={`批量指派（已选 ${selected.length} 条）`}
        open={assignOpen}
        onCancel={() => setAssignOpen(false)}
        onOk={() => assignForm.submit()}
        confirmLoading={doAssign.isPending}
        destroyOnClose
      >
        <Form form={assignForm} layout="vertical" onFinish={(v) => doAssign.mutate(v)}>
          <Form.Item name="owner_id" label="新归属人" rules={[{ required: true, message: '请选择归属人' }]}>
            <Select placeholder="请选择" options={ownerOptions} showSearch optionFilterProp="label" />
          </Form.Item>
          <Form.Item
            name="remark"
            label="分配 / 改派原因（必填）"
            rules={[{ required: true, message: '请填写操作备注' }]}
            extra="分配操作留痕，可在每条线索详情的「分配与操作日志」里查。"
          >
            <Input.TextArea rows={2} placeholder="如：按区域就近分配，浙江片区归陈悦" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`回收至公海（已选 ${selected.length} 条）`}
        open={recycleOpen}
        onCancel={() => setRecycleOpen(false)}
        onOk={() => recycleForm.submit()}
        confirmLoading={doRecycle.isPending}
        destroyOnClose
      >
        <Form form={recycleForm} layout="vertical" onFinish={(v) => doRecycle.mutate({ remark: v.remark })}>
          <Form.Item
            name="remark"
            label="回收原因（必填）"
            rules={[{ required: true, message: '请填写回收原因' }]}
          >
            <Input.TextArea rows={2} placeholder="如：长期未跟进，回收重新分配" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
