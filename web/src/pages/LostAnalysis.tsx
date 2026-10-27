import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
  Row,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  AlertOutlined,
  ClockCircleOutlined,
  PercentageOutlined,
  ReloadOutlined,
  RollbackOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import dayjs, { type Dayjs } from 'dayjs';
import { fetchStaffOptions } from '@/api/dashboard';
import { fetchLostAnalysis, revertLoss } from '@/api/risks';
import GateBanner from '@/components/GateBanner';
import KpiCard from '@/components/KpiCard';
import PageHeader from '@/components/PageHeader';
import { Footnote, NextStepGuide, NotForAssessment } from '@/components/Guide';
import { useAuthStore } from '@/store/auth';
import { fmtDate, fmtDateTime, fmtPercent, fenToWan, formatYuan } from '@/utils/format';
import type { Lead, LostOwnerRow, LostReasonRow, LostStageRow } from '@/types';

/** R1–R5 一色一因，保证条形与明细标签同色（不改业务口径，只改呈现） */
const R_COLOR: Record<string, string> = {
  r1_budget_unclear: '#465FFF',
  r2_trust_low: '#12B76A',
  r3_decision_chain_unclear: '#F79009',
  r4_demand_unclear: '#7A5AF8',
  r5_rhythm_unclear: '#F04438',
};

/**
 * 2.5 丢单登记与分析 —— 对齐设计稿 06 左栏的"KPI + 表 + 页脚口径"骨架：
 * 顶部 KPI（丢单数 / 本月 / 平均滞留 / 最集中环节）· R1–R5 分布用条形 + 条数 + 占比 ·
 * 页脚写明「原因来自 R1–R5 多选，各项之和可 > 丢单数」· 登记入口明确指向「商机详情 → 登记丢单」。
 */
export default function LostAnalysis() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const role = useAuthStore((s) => s.user?.role);
  const isManager = role === 'admin' || role === 'market_manager';

  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [ownerId, setOwnerId] = useState<string | undefined>(undefined);

  const params = {
    start: range?.[0]?.format('YYYY-MM-DD'),
    end: range?.[1]?.format('YYYY-MM-DD'),
    owner_id: isManager ? ownerId : undefined,
    include_leads: true,
  };

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['lost-analysis', params],
    queryFn: () => fetchLostAnalysis(params),
  });

  const { data: staff } = useQuery({
    queryKey: ['dashboard-staff-options'],
    queryFn: fetchStaffOptions,
    enabled: isManager,
    retry: false,
  });

  /** 撤销丢单 */
  const [reverting, setReverting] = useState<Lead | null>(null);
  const [revertForm] = Form.useForm<{ remark: string }>();

  const doRevert = useMutation({
    mutationFn: (v: { leadId: string; remark: string }) => revertLoss(v.leadId, v.remark),
    onSuccess: () => {
      message.success('已撤销丢单，商机恢复为跟进中');
      setReverting(null);
      revertForm.resetFields();
      qc.invalidateQueries({ queryKey: ['lost-analysis'] });
      qc.invalidateQueries({ queryKey: ['opportunities'] });
      qc.invalidateQueries({ queryKey: ['leads'] });
      qc.invalidateQueries({ queryKey: ['dashboard-risk-center'] });
      qc.invalidateQueries({ queryKey: ['payments-summary'] });
    },
  });

  /* ---------------- 派生指标（口径都写在 KPI hint 上） ---------------- */
  const items = useMemo(() => data?.items ?? [], [data]);

  /** 本月丢单数：按丢单时间落在自然月（1 号~月底），与右上筛选区间无关，口径写在 hint 里 */
  const monthLost = useMemo(() => {
    const start = dayjs().startOf('month');
    const end = dayjs().endOf('month');
    return items.filter((it) => it.lost_at && dayjs(it.lost_at).isAfter(start) && dayjs(it.lost_at).isBefore(end))
      .length;
  }, [items]);

  /** 平均滞留天数：从「有效商机时间 / 阶段进入时间」到「丢单登记时间」的自然日差平均 */
  const avgStayDays = useMemo(() => {
    const days = items
      .filter((it) => it.lost_at && (it.valid_at || it.stage_entered_at))
      .map((it) => dayjs(it.lost_at).diff(dayjs(it.valid_at || it.stage_entered_at), 'day'))
      .filter((d) => d >= 0);
    if (!days.length) return null;
    return Math.round((days.reduce((a, b) => a + b, 0) / days.length) * 10) / 10;
  }, [items]);

  /** 最集中环节：丢单环节分布里条数最多的一项 */
  const topStage = useMemo(() => {
    const rows = data?.by_stage ?? [];
    if (!rows.length) return null;
    return rows.reduce((a, b) => (b.count > a.count ? b : a));
  }, [data]);

  const reasons = data?.by_reason ?? [];
  const maxReasonCount = Math.max(...reasons.map((r) => r.count), 1);

  const reasonColumns: ColumnsType<LostReasonRow> = [
    { title: '丢单原因（R1–R5）', dataIndex: 'label' },
    { title: '数量', dataIndex: 'count', width: 80, align: 'right' },
    {
      title: '占比',
      dataIndex: 'rate',
      width: 100,
      align: 'right',
      render: (v: number) => fmtPercent(v),
    },
  ];

  const stageColumns: ColumnsType<LostStageRow> = [
    { title: '丢单环节', dataIndex: 'label' },
    { title: '数量', dataIndex: 'count', width: 80, align: 'right' },
  ];

  const ownerColumns: ColumnsType<LostOwnerRow> = [
    { title: '负责人', dataIndex: 'user_name' },
    { title: '丢单数', dataIndex: 'count', width: 90, align: 'right' },
  ];

  const detailColumns: ColumnsType<Lead> = [
    {
      title: '客户名称',
      dataIndex: 'name',
      width: 190,
      render: (v: string, r) => <a onClick={() => navigate(`/opportunities/${r.id}`)}>{v}</a>,
    },
    {
      title: '丢单时间',
      dataIndex: 'lost_at',
      width: 150,
      render: (v: string | null) => fmtDateTime(v),
    },
    {
      title: '丢单原因（R1–R5 多选）',
      dataIndex: 'lost_reason_labels',
      width: 260,
      render: (v: string[] | undefined) =>
        v?.length ? (
          <Space size={4} wrap>
            {v.map((t) => (
              <Tag color="volcano" key={t}>
                {t}
              </Tag>
            ))}
          </Space>
        ) : (
          '-'
        ),
    },
    {
      title: '丢单环节',
      dataIndex: 'current_stage_label',
      width: 150,
      render: (v: string | null, r) => v || r.current_stage || '闭环一（线索阶段）',
    },
    { title: '负责人', dataIndex: 'owner_name', width: 100, render: (v: string | null) => v || '-' },
    {
      title: '预算金额(万)',
      dataIndex: 'budget_amount_fen',
      width: 130,
      align: 'right',
      render: (v: number | null) => fenToWan(v),
    },
    { title: '丢单备注', dataIndex: 'lost_remark', ellipsis: true, render: (v: string | null) => v || '-' },
    {
      title: '操作',
      width: 110,
      fixed: 'right',
      render: (_, r) => (
        <Button
          type="link"
          size="small"
          icon={<RollbackOutlined />}
          onClick={() => {
            setReverting(r);
            revertForm.setFieldsValue({ remark: '' });
          }}
        >
          撤销丢单
        </Button>
      ),
    },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <PageHeader
        title="丢单登记与分析"
        subtitle="看清丢在哪一类原因、丢在哪个环节——丢单原因来自商机登记时的 R1–R5 多选，不是系统猜的；误判可在这里撤销。"
        demo
        actions={
          <Space wrap>
            <DatePicker.RangePicker
              value={range}
              onChange={(v) => setRange(v as [Dayjs, Dayjs] | null)}
              allowClear
              placeholder={['丢单开始日期', '丢单结束日期']}
            />
            {isManager && (
              <Select
                allowClear
                style={{ width: 160 }}
                placeholder="团队全部"
                value={ownerId}
                onChange={(v) => setOwnerId(v ?? undefined)}
                options={(staff?.items ?? []).map((s) => ({ value: s.user_id, label: s.name }))}
              />
            )}
            <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
              刷新
            </Button>
          </Space>
        }
      />

      <GateBanner
        tone="tip"
        title="登记丢单不在这里：请在「商机详情 → 顶部操作区 → 登记丢单」"
        items={[
          '登记时必须从 R1–R5 中多选原因（这是丢单原因分布的唯一数据来源），可再填一段丢单备注；',
          '操作路径：2.1 商机列表 → 点客户名进商机详情 → 右上「登记丢单」；',
          '本页只做丢单分析与撤销；误判丢单可撤销，撤销后商机恢复并留操作日志。',
        ]}
        action={
          <Space wrap>
            <Button size="small" type="primary" icon={<SearchOutlined />} onClick={() => navigate('/opportunities')}>
              去 2.1 商机列表
            </Button>
            <Button size="small" onClick={() => navigate('/workbench')}>
              去工作台
            </Button>
          </Space>
        }
      />

      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}>
          <KpiCard
            label="丢单数"
            value={data?.total_lost ?? 0}
            suffix="个"
            icon={<AlertOutlined />}
            tone={(data?.total_lost ?? 0) > 0 ? 'red' : 'gray'}
            danger={(data?.total_lost ?? 0) > 0}
            hint="口径：按丢单时间落在右上所选区间、且状态为「已丢单」的商机计数；撤销丢单后会从这里减掉。丢单金额按商机预算金额累计"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="本月丢单"
            value={monthLost}
            suffix="个"
            icon={<AlertOutlined />}
            tone={monthLost > 0 ? 'amber' : 'gray'}
            hint={`口径：丢单时间落在 ${dayjs().format('YYYY-MM')} 自然月内（${dayjs().startOf('month').format('MM-DD')} ~ ${dayjs().endOf('month').format('MM-DD')}）的丢单数，与右上区间筛选无关；当前列表 ${items.length} 条`}
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="平均滞留"
            value={avgStayDays === null ? '—' : avgStayDays}
            suffix={avgStayDays === null ? undefined : '天'}
            icon={<ClockCircleOutlined />}
            tone="purple"
            hint="口径：平均滞留 = 平均（丢单登记时间 − 有效商机时间 / 阶段进入时间），按自然日；用于看“项目拖了多久才丢”"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="最集中环节"
            value={topStage?.label ?? '暂无'}
            total={topStage ? `${topStage.count} 个` : undefined}
            icon={<PercentageOutlined />}
            tone="brand"
            hint="口径：丢单环节分布里条数最多的一项；丢单环节 = 登记丢单时商机所处的环节"
          />
        </Col>
      </Row>

      <Alert
        type="info"
        showIcon
        message="R1–R5 是多选：各原因条数之和可以大于丢单数，占比之和也可以超过 100%"
        description="一条丢单可能同时是「决策链不清 + 客户需求不清」；所以条形长度按各原因出现次数画，不用来反推丢单总数——丢单总数看上面的「丢单数」卡。"
      />

      <Row gutter={[12, 12]}>
        <Col xs={24} xl={14}>
          <Card
            size="small"
            title="R1–R5 丢单原因分布"
            extra={<Tag color="volcano">多选口径</Tag>}
          >
            {reasons.length && reasons.some((r) => r.count > 0) ? (
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                {reasons.map((r) => {
                  const pct = Math.round((r.count / maxReasonCount) * 100);
                  return (
                    <div key={r.risk_type}>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <Typography.Text style={{ fontSize: 13 }}>{r.label}</Typography.Text>
                        <Space size={8}>
                          <Typography.Text strong style={{ fontSize: 13 }}>
                            {r.count} 条
                          </Typography.Text>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            占比 {fmtPercent(r.rate)}
                          </Typography.Text>
                        </Space>
                      </div>
                      <div
                        style={{
                          marginTop: 4,
                          height: 14,
                          borderRadius: 4,
                          background: '#F2F4F7',
                          overflow: 'hidden',
                        }}
                      >
                        <Tooltip title={`${r.label}：${r.count} 条 · 占丢单数 ${fmtPercent(r.rate)}`}>
                          <div
                            style={{
                              width: `${Math.max(pct, r.count > 0 ? 6 : 0)}%`,
                              height: '100%',
                              background: R_COLOR[r.risk_type] ?? '#F79009',
                              borderRadius: 4,
                            }}
                          />
                        </Tooltip>
                      </div>
                    </div>
                  );
                })}
              </Space>
            ) : (
              <NextStepGuide
                compact
                title="暂无可统计的丢单原因"
                description="所选区间内还没有丢单登记。丢单登记在「商机详情 → 登记丢单」，且必须多选 R1–R5。"
                actions={
                  <Button size="small" type="primary" onClick={() => navigate('/opportunities')}>
                    去 2.1 商机列表
                  </Button>
                }
              />
            )}
            <div style={{ marginTop: 10, fontSize: 12, color: '#667085' }}>
              ⓘ 原因来自 R1–R5 多选，各项之和可 &gt; 丢单数（当前：各项合计{' '}
              {reasons.reduce((a, r) => a + r.count, 0)} 条 / 丢单 {data?.total_lost ?? 0} 个）。
            </div>
            <Table<LostReasonRow>
              rowKey="risk_type"
              size="small"
              style={{ marginTop: 10 }}
              loading={isLoading}
              dataSource={reasons}
              columns={reasonColumns}
              pagination={false}
            />
          </Card>
        </Col>
        <Col xs={24} xl={10}>
          <Card size="small" title="丢单环节分布">
            {data?.by_stage?.length ? (
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                {data.by_stage.map((s) => {
                  const max = Math.max(...(data.by_stage ?? []).map((x) => x.count), 1);
                  return (
                    <div key={s.stage}>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <Typography.Text style={{ fontSize: 13 }}>{s.label}</Typography.Text>
                        <Typography.Text strong style={{ fontSize: 13 }}>
                          {s.count} 个
                        </Typography.Text>
                      </div>
                      <div
                        style={{
                          marginTop: 4,
                          height: 14,
                          borderRadius: 4,
                          background: '#F2F4F7',
                          overflow: 'hidden',
                        }}
                      >
                        <div
                          style={{
                            width: `${Math.max(Math.round((s.count / max) * 100), s.count > 0 ? 6 : 0)}%`,
                            height: '100%',
                            background: '#FA8C16',
                            borderRadius: 4,
                          }}
                        />
                      </div>
                    </div>
                  );
                })}
              </Space>
            ) : (
              <NextStepGuide
                compact
                title="暂无丢单环节数据"
                description="有丢单登记后，这里会按「丢单时所处环节」分布显示，便于判断主要丢在哪个环节。"
              />
            )}
            <Table<LostStageRow>
              rowKey="stage"
              size="small"
              style={{ marginTop: 10 }}
              loading={isLoading}
              dataSource={data?.by_stage ?? []}
              columns={stageColumns}
              pagination={false}
              locale={{
                emptyText: (
                  <NextStepGuide
                    compact
                    title="暂无丢单环节数据"
                    description="有丢单登记后这里会按「丢单时所处环节」分布显示，便于判断主要丢在哪个环节。"
                  />
                ),
              }}
            />
          </Card>
        </Col>
      </Row>

      <Card size="small" title="按负责人">
        <Row gutter={12}>
          <Col xs={24} lg={14}>
            <Table<LostOwnerRow>
              rowKey="user_id"
              size="small"
              loading={isLoading}
              dataSource={data?.by_owner ?? []}
              columns={ownerColumns}
              pagination={false}
              locale={{
                emptyText: (
                  <NextStepGuide compact title="暂无负责人分布数据" description="所选区间内还没有丢单记录。" />
                ),
              }}
            />
          </Col>
          <Col xs={24} lg={10}>
            {data?.by_owner?.length ? (
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {data.by_owner.map((o) => {
                  const max = Math.max(...(data.by_owner ?? []).map((x) => x.count), 1);
                  return (
                    <div key={o.user_id ?? o.user_name}>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <Typography.Text style={{ fontSize: 13 }}>{o.user_name}</Typography.Text>
                        <Typography.Text strong style={{ fontSize: 13 }}>
                          {o.count}
                        </Typography.Text>
                      </div>
                      <div
                        style={{
                          marginTop: 4,
                          height: 12,
                          borderRadius: 4,
                          background: '#F2F4F7',
                          overflow: 'hidden',
                        }}
                      >
                        <div
                          style={{
                            width: `${Math.max(Math.round((o.count / max) * 100), 6)}%`,
                            height: '100%',
                            background: '#465FFF',
                            borderRadius: 4,
                          }}
                        />
                      </div>
                    </div>
                  );
                })}
              </Space>
            ) : (
              <NextStepGuide compact title="暂无人员分布" description="所选区间内还没有丢单记录。" />
            )}
          </Col>
        </Row>
      </Card>

      <Card
        title={
          <Space>
            丢单明细
            <Tag color="red">{items.length}</Tag>
          </Space>
        }
        extra={
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            点客户名称可进入商机详情；撤销丢单后商机恢复为跟进中
          </Typography.Text>
        }
      >
        <Table<Lead>
          rowKey="id"
          size="small"
          loading={isLoading}
          dataSource={items}
          columns={detailColumns}
          scroll={{ x: 1200 }}
          locale={{
            emptyText: (
              <NextStepGuide
                title="暂无丢单记录"
                description="所选区间与筛选条件下没有丢单。换个时间区间看看；或去商机列表处理在跟项目。"
                actions={
                  <Space wrap>
                    <Button onClick={() => setRange(null)}>清掉时间区间</Button>
                    <Button type="primary" onClick={() => navigate('/opportunities')}>
                      去 2.1 商机列表
                    </Button>
                  </Space>
                }
              />
            ),
          }}
          pagination={{ pageSize: 10, showSizeChanger: false, showTotal: (t) => `共 ${t} 条` }}
        />
      </Card>

      {/* 撤销丢单弹窗 */}
      <Modal
        open={!!reverting}
        title="撤销丢单"
        okText="确认撤销"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        confirmLoading={doRevert.isPending}
        onCancel={() => {
          setReverting(null);
          revertForm.resetFields();
        }}
        onOk={async () => {
          const v = await revertForm.validateFields();
          if (!reverting) return;
          doRevert.mutate({ leadId: reverting.id, remark: v.remark?.trim() ?? '' });
        }}
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`确认撤销「${reverting?.name}」的丢单登记？`}
          description="撤销后商机恢复为跟进中（或有效商机），丢单原因与备注会清空，系统保留一条「撤销丢单」操作日志。"
        />
        <Form form={revertForm} layout="vertical">
          <Form.Item
            label="撤销原因 / 备注"
            name="remark"
            rules={[{ required: true, message: '请写清撤销原因（会记入操作日志）' }]}
            extra="撤销是有留痕的操作：写清为什么判定它其实没丢单。"
          >
            <Input.TextArea rows={3} placeholder="如：客户重新启动预算，实际未丢单" />
          </Form.Item>
        </Form>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          当前丢单原因：{reverting?.lost_reason_labels?.join('、') || '—'}；丢单时间：
          {fmtDate(reverting?.lost_at, 'YYYY-MM-DD HH:mm')}
        </Typography.Text>
      </Modal>

      <Footnote>
        丢单原因分布来自商机「登记丢单」时的 R1–R5 多选（资金预算不清 / 信任不到 / 决策链不清 / 客户需求不清 /
        项目节奏不清）；一条丢单可多选，因此各原因数量之和可能大于丢单总数、占比之和可能超过 100%（原因来自 R1–R5
        多选，各项之和可 &gt; 丢单数）。丢单金额按商机预算金额累计（当前合计 {formatYuan(data?.lost_amount_fen)} 元）；
        平均滞留按「有效商机时间 → 丢单登记时间」的自然日平均。变更 / 撤销丢单都会留存操作日志。
        若需要比较两个区间的差异，请用百分点表述（如「较上期 +3.2 个百分点」）。
      </Footnote>
      <NotForAssessment />
    </Space>
  );
}
