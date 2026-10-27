import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  Button,
  Card,
  Col,
  Form,
  Input,
  Modal,
  Pagination,
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
import { TeamOutlined, ThunderboltOutlined, UserOutlined, WarningFilled } from '@ant-design/icons';
import { assignLeads, listLeads, loadView, recycleLeads } from '@/api/leads';
import { listUsers } from '@/api/auth';
import { useDicts } from '@/hooks/useDicts';
import { fmtDateTime } from '@/utils/format';
import { LeadStatusTag, OrgTypeTag, SourceTag } from '@/components/tags';
import SimpleBar from '@/components/SimpleBar';
import KpiCard from '@/components/KpiCard';
import PageHeader from '@/components/PageHeader';
import { Footnote, NextStepGuide, PanelFlag } from '@/components/Guide';
import type { Lead, LoadViewRow } from '@/types';

/** 1.6 分配管理：未指派池（可多选）+ 负载参考面板 + 批量指派/回收（需填备注） */
export default function Assign() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { dicts } = useDicts();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [selected, setSelected] = useState<string[]>([]);
  const [assignOpen, setAssignOpen] = useState(false);
  const [recycleOpen, setRecycleOpen] = useState(false);
  const [assignForm] = Form.useForm<{ owner_id: string; remark: string }>();
  const [recycleForm] = Form.useForm<{ remark: string }>();
  const [sourceFilter, setSourceFilter] = useState<string | undefined>();
  /** 右栏「手工分配」面板的本地状态 */
  const [panelOwner, setPanelOwner] = useState<string | undefined>();
  const [panelReason, setPanelReason] = useState('');

  // 未指派池
  const { data, isLoading } = useQuery({
    queryKey: ['assign-pool', page, pageSize, sourceFilter],
    queryFn: () =>
      listLeads({
        view: 'unassigned',
        source: sourceFilter,
        page,
        page_size: pageSize,
        sort: 'recent',
      }),
  });

  // 负载视图
  const { data: load, isLoading: loadLoading } = useQuery({
    queryKey: ['lead-load-view'],
    queryFn: loadView,
  });

  const { data: userData } = useQuery({
    queryKey: ['users', 'all'],
    queryFn: () => listUsers({ page: 1, page_size: 200 }),
    retry: false,
  });
  const ownerOptions = (userData?.items ?? []).map((u) => ({ value: u.id, label: u.name }));
  const nameOf = (userId: string) =>
    ownerOptions.find((o) => o.value === userId)?.label ?? '该成员';
  /** 负载行按 user_id 建索引，右栏选人时显示「在跟进 N」 */
  const loadByUser = new Map((load?.items ?? []).map((i) => [i.user_id, i]));
  const followingTotal = (load?.items ?? []).reduce((sum, i) => sum + (i.following_count ?? 0), 0);

  const refresh = () => {
    setSelected([]);
    qc.invalidateQueries({ queryKey: ['assign-pool'] });
    qc.invalidateQueries({ queryKey: ['lead-load-view'] });
    qc.invalidateQueries({ queryKey: ['leads'] });
    qc.invalidateQueries({ queryKey: ['workbench'] });
  };

  const doAssign = useMutation({
    mutationFn: (v: { owner_id: string; remark: string }) =>
      assignLeads({ lead_ids: selected, owner_id: v.owner_id, remark: v.remark }),
    onSuccess: (r) => {
      message.success(`已指派 ${r.affected} 条线索`);
      setAssignOpen(false);
      assignForm.resetFields();
      setPanelOwner(undefined);
      setPanelReason('');
      refresh();
    },
  });

  const doRecycle = useMutation({
    mutationFn: (v: { remark: string }) => recycleLeads({ lead_ids: selected, remark: v.remark }),
    onSuccess: (r) => {
      message.success(`已回收 ${r.affected} 条线索`);
      setRecycleOpen(false);
      recycleForm.resetFields();
      refresh();
    },
  });

  /** 列序照设计稿 02：客户名称 / 地区 / 来源 / 类型 / 状态 / 最近跟进 */
  const columns: ColumnsType<Lead> = [
    {
      title: '客户名称',
      dataIndex: 'name',
      width: 220,
      ellipsis: true,
      render: (v: string, r) => (
        <Space direction="vertical" size={0}>
          <a onClick={() => navigate(`/leads/${r.id}`)}>{v}</a>
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
      width: 120,
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
      title: (
        <Tooltip title="最近一次跟进时间；未指派线索通常为「-」。指派后 1 个工作日内需首次跟进（按工作日）">
          <span>最近跟进</span>
        </Tooltip>
      ),
      dataIndex: 'last_follow_at',
      width: 150,
      render: (v: string | null) => (v ? fmtDateTime(v) : '-'),
    },
  ];

  const loadColumns: ColumnsType<LoadViewRow> = [
    { title: '成员', dataIndex: 'name', width: 120 },
    { title: '未指派', dataIndex: 'unassigned_count', width: 90 },
    { title: '已指派', dataIndex: 'assigned_count', width: 90 },
    {
      title: (
        <Tooltip title="负载视图里各成员「跟进中」的条数，仅为参考，不构成考核">在跟进</Tooltip>
      ),
      dataIndex: 'following_count',
      width: 90,
      render: (v: number) => <Tag color="processing">{v}</Tag>,
    },
    {
      title: '手上合计',
      dataIndex: 'total_count',
      width: 100,
      render: (v: number) => <Typography.Text strong>{v}</Typography.Text>,
    },
  ];

  /**
   * 右栏「手工分配」面板（对齐设计稿 02 右侧）：
   *   手工分配 / 负责人负载 双 Tab → 选择负责人（每人 在跟进 N ｜ 待首次跟进 N + 单选）
   *   → 指派理由（选填）→ 确认指派 N 条 → 指派规则框（工作日口径 + 留痕）
   *
   * C4 赋能边界：**由管理者手工分配，系统不自动推荐**——负载只作参考，选人权在人。
   */
  const assignPanel = (
    <Card size="small" styles={{ body: { paddingTop: 12 } }}>
      <Tabs
        defaultActiveKey="rule"
        size="small"
        items={[
          {
            key: 'rule',
            label: '手工分配',
            children: (
              <>
                <Typography.Text strong style={{ fontSize: 13 }}>
                  选择负责人
                </Typography.Text>
                <Typography.Text
                  type="secondary"
                  style={{ display: 'block', fontSize: 12, marginTop: 2 }}
                >
                  由管理者手工分配，系统不自动推荐；数字仅供参考，不作为考核依据。
                </Typography.Text>
                <div style={{ marginTop: 10 }}>
                  {(load?.items ?? []).length === 0 ? (
                    <NextStepGuide
                      compact
                      title="暂无成员负载数据"
                      description="负载视图没取到成员时，仍可用下方「指定其他成员」下拉指定归属人，不影响指派。"
                    />
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                      {(load?.items ?? []).map((m) => {
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
                              <Tooltip title="负载视图口径：该成员状态为「跟进中」的线索数；仅为参考">
                                <Typography.Text style={{ fontSize: 12, color: '#667085' }}>
                                  在跟进 <strong style={{ color: '#182230' }}>{m.following_count}</strong>
                                </Typography.Text>
                              </Tooltip>
                              <Tooltip title="负载视图口径：已指派但尚未首次跟进（状态=已指派）的条数——这批复最可能压到首次跟进 SLA">
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
                    指定其他成员
                  </Typography.Text>
                  <Select
                    style={{ width: '100%', marginTop: 4 }}
                    allowClear
                    placeholder="下拉选择（含未出现在负载视图里的成员）"
                    options={ownerOptions}
                    value={panelOwner}
                    onChange={(v) => setPanelOwner(v)}
                    showSearch
                    optionFilterProp="label"
                  />
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
                        ? '先选择负责人'
                        : undefined
                  }
                >
                  <span>
                    <Button
                      type="primary"
                      block
                      style={{ marginTop: 12 }}
                      disabled={selected.length === 0 || !panelOwner}
                      loading={doAssign.isPending}
                      onClick={() => {
                        if (!panelOwner) return;
                        const name = nameOf(panelOwner);
                        doAssign.mutate({
                          owner_id: panelOwner,
                          remark:
                            panelReason.trim() ||
                            `手工分配给 ${name}（${selected.length} 条）`,
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
                    左侧勾选后按钮才可用；也可用表格左下角「批量指派」走弹窗（必填备注）。
                  </Typography.Text>
                )}

                {/*  指派规则框：口径就地可见（工作日 vs 自然日要说清） */}
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
                    <li>指派后 1 个工作日内首次跟进（按工作日，非自然日）；</li>
                    <li>7 个工作日未跟进，系统自动回收至公海；</li>
                    <li>分配操作留痕，可在每条线索详情的「分配与操作日志」里查。</li>
                  </ol>
                </div>
              </>
            ),
          },
          {
            key: 'load',
            label: '负责人负载',
            children: (
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                <Space wrap size={8}>
                  <Typography.Text strong>负责人负载</Typography.Text>
                  <PanelFlag>仅为参考</PanelFlag>
                </Space>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  负载数字不参与自动推荐，也不作为考核依据——分配决策权在管理者。
                </Typography.Text>
                <Table
                  rowKey="user_id"
                  size="small"
                  loading={loadLoading}
                  columns={loadColumns}
                  dataSource={load?.items ?? []}
                  pagination={false}
                  locale={{
                    emptyText: (
                      <NextStepGuide
                        compact
                        title="暂无成员负载数据"
                        description="还没有成员手上有线索，或后端未返回负载视图；不影响正常指派。"
                        actions={
                          <Button size="small" onClick={() => navigate('/leads?view=unassigned')}>
                            去未指派池
                          </Button>
                        }
                      />
                    ),
                  }}
                />
                <SimpleBar
                  items={(load?.items ?? []).map((i) => ({
                    label: i.name,
                    value: i.total_count,
                    extra: `在跟进 ${i.following_count}`,
                  }))}
                />
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  手上合计 = 该成员名下未删除线索总数（自然日口径不适用，纯计数）。
                </Typography.Text>
              </Space>
            ),
          },
        ]}
      />
    </Card>
  );

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title="分配管理"
        subtitle="手工把未指派线索分给专员——系统不自动推荐，右侧负载数字仅供参考，决定权在人。"
        demo
        actions={
          <Space wrap>
            <Tooltip title={selected.length ? undefined : '先在「未指派池」勾选线索'}>
              <span>
                <Button
                  danger
                  disabled={!selected.length}
                  onClick={() => {
                    recycleForm.resetFields();
                    setRecycleOpen(true);
                  }}
                >
                  回收至公海
                </Button>
              </span>
            </Tooltip>
            <Button onClick={() => navigate('/leads?view=unassigned')}>在线索池查看</Button>
          </Space>
        }
      />

      <Row gutter={[12, 12]}>
        <Col xs={24} sm={12} lg={8}>
          <KpiCard
            label="未指派总量"
            value={data?.total ?? 0}
            icon={<UserOutlined />}
            tone="brand"
            hint="当前筛选条件下「未指派」线索的条数；分配后从该池移出"
          />
        </Col>
        <Col xs={24} sm={12} lg={8}>
          <KpiCard
            label="在册成员"
            value={load?.items?.length ?? 0}
            icon={<TeamOutlined />}
            tone="purple"
            hint="负载视图里有线索记录的成员数，不代表全部可指派账号"
          />
        </Col>
        <Col xs={24} sm={12} lg={8}>
          <KpiCard
            label="在跟进合计"
            value={followingTotal}
            icon={<ThunderboltOutlined />}
            tone="green"
            hint="负载视图中各成员「跟进中」条数之和；仅为参考，不作为分配或考核依据"
          />
        </Col>
      </Row>

      <Card styles={{ body: { paddingTop: 12 } }}>
        <Space style={{ marginBottom: 12 }} wrap size={12}>
          <Select
            allowClear
            placeholder="请选择来源"
            style={{ width: 180 }}
            value={sourceFilter}
            onChange={(v) => {
              setPage(1);
              setSelected([]);
              setSourceFilter(v);
            }}
            options={dicts.lead_sources ?? []}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            勾选后可在右侧选负责人一次性指派（系统不自动推荐），或用左下角「批量指派」走弹窗。
          </Typography.Text>
        </Space>
        <Row gutter={16}>
                  <Col xs={24} xl={16}>
                    <Table
                      rowKey="id"
                      size="small"
                      loading={isLoading}
                      columns={columns}
                      dataSource={data?.items ?? []}
                      rowSelection={{
                        selectedRowKeys: selected,
                        onChange: (keys) => setSelected(keys.map((k) => String(k))),
                      }}
                      locale={{
                        emptyText: (
                          <NextStepGuide
                            title="未指派池已清空"
                            description="当前筛选条件下没有待分配的线索。可以清掉来源筛选，或去线索池用 Excel 导入新名单。"
                            actions={
                              <Space wrap>
                                {sourceFilter && (
                                  <Button
                                    onClick={() => {
                                      setSourceFilter(undefined);
                                      setPage(1);
                                    }}
                                  >
                                    清掉来源筛选
                                  </Button>
                                )}
                                <Button type="primary" onClick={() => navigate('/imports')}>
                                  去 Excel 导入
                                </Button>
                              </Space>
                            }
                          />
                        ),
                      }}
                      pagination={false}
                    />

                    {/* 底部批量条（与线索池一致的交互） */}
                    <div
                      style={{
                        display: 'flex',
                        flexWrap: 'wrap',
                        alignItems: 'center',
                        gap: 12,
                        marginTop: 12,
                        padding: '10px 14px',
                        background: selected.length ? '#F5F8FF' : '#FAFBFC',
                        border: '1px solid #EAECF0',
                        borderRadius: 8,
                      }}
                    >
                      <Typography.Text strong>已选 {selected.length} 条</Typography.Text>
                      <Tooltip title={selected.length ? undefined : '先勾选要指派的线索'}>
                        <span>
                          <Button
                            type="primary"
                            disabled={!selected.length}
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
                            disabled={!selected.length}
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
                      <Pagination
                        style={{ marginLeft: 'auto' }}
                        size="small"
                        current={page}
                        pageSize={pageSize}
                        total={data?.total ?? 0}
                        showSizeChanger
                        showTotal={(n) => `共 ${n} 条`}
                        onChange={(p, ps) => {
                          setPage(p);
                          setPageSize(ps);
                        }}
                      />
                    </div>
                  </Col>
                  <Col xs={24} xl={8}>
                    {assignPanel}
                  </Col>
                </Row>
              ),
      </Card>

      <Footnote>
        负载视图（在跟进 / 已指派 / 手上合计）仅为参考，分配决策权在人，系统不自动推荐负责人。
        首次跟进与自动回收均按工作日计算：指派后 1 个工作日内首次跟进，7 个工作日未跟进自动回收至公海。
        分配 / 改派 / 回收必须填备注并全部留痕，可在每条线索详情的「分配与操作日志」里查。
      </Footnote>

      <Modal
        title={`批量指派 / 改派（已选 ${selected.length} 条）`}
        open={assignOpen}
        onCancel={() => setAssignOpen(false)}
        onOk={() => assignForm.submit()}
        confirmLoading={doAssign.isPending}
        destroyOnClose
      >
        <Form form={assignForm} layout="vertical" onFinish={(v) => doAssign.mutate(v)}>
          <Form.Item
            name="owner_id"
            label="新归属人"
            rules={[{ required: true, message: '请选择归属人' }]}
            extra={
              panelOwner
                ? `负载参考：${nameOf(panelOwner)} 手上合计 ${
                    loadByUser.get(panelOwner)?.total_count ?? 0
                  } 条（仅为参考）`
                : undefined
            }
          >
            <Select placeholder="请选择" options={ownerOptions} showSearch optionFilterProp="label" />
          </Form.Item>
          <Form.Item
            name="remark"
            label="操作备注"
            rules={[{ required: true, message: '分配需填写备注（留痕）' }]}
          >
            <Input.TextArea rows={2} placeholder="如：按区域就近分配" />
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
        <Form form={recycleForm} layout="vertical" onFinish={(v) => doRecycle.mutate(v)}>
          <Form.Item
            name="remark"
            label="回收原因"
            rules={[{ required: true, message: '回收需填写原因（留痕）' }]}
          >
            <Input.TextArea rows={2} placeholder="如：长期未跟进，回收重新分配" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
