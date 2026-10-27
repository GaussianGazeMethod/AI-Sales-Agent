import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  Button,
  Card,
  Col,
  Input,
  Row,
  Segmented,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  AlertOutlined,
  FolderOpenOutlined,
  ShopOutlined,
  TeamOutlined,
  WalletOutlined,
} from '@ant-design/icons';
import { listCustomers } from '@/api/customers';
import { useAuthStore } from '@/store/auth';
import KpiCard from '@/components/KpiCard';
import PageHeader from '@/components/PageHeader';
import { Footnote, NextStepGuide, PanelFlag } from '@/components/Guide';
import { OrgTypeTag } from '@/components/tags';
import { fenToWan, fmtDate } from '@/utils/format';
import type { Customer } from '@/types';

const PAGE_SIZE = 10;

/**
 * 客户列表。
 *
 * 骨架对齐 spec/11：标题与筛选同一行 + 一行 KPI 大数字 + 密集但可读的表。
 * 判据落点：
 * * **C3 口径可见**：`value_note`（后端口径原文）直接显示在 KPI 行下方，
 *   「过程中价值 / 已签约 / 已丢单」三者关系写在界面上，不让人猜。
 * * **C2 少判断**：空态给"下一步去哪"；风险数非 0 时点得到处置入口。
 * * **C1 少填**：所有数字都来自后端聚合，页面不做任何前端重算。
 */
export default function Customers() {
  const navigate = useNavigate();
  const role = useAuthStore((s) => s.user?.role);
  const [keyword, setKeyword] = useState('');
  const [scope, setScope] = useState<'all' | 'mine'>('all');
  const [page, setPage] = useState(1);

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['customers', { keyword, scope, page, page_size: PAGE_SIZE }],
    queryFn: () =>
      listCustomers({
        keyword: keyword || undefined,
        only_mine: scope === 'mine' ? true : undefined,
        page,
        page_size: PAGE_SIZE,
      }),
  });

  const summary = data?.summary;
  /** 后端口径原文（接口同时给了 summary.value_note 与顶层 value_note，优先用顶层的） */
  const valueNote = data?.value_note || summary?.value_note || '';

  const columns: ColumnsType<Customer> = [
    {
      title: '客户名称',
      dataIndex: 'name',
      width: 220,
      ellipsis: true,
      render: (v: string, r) => (
        <Space direction="vertical" size={0}>
          <Space size={6} wrap>
            <a onClick={() => navigate(`/customers/${r.id}`)}>{v}</a>
            {r.level && <Tag color="blue">{r.level} 级</Tag>}
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {r.region || [r.province, r.city, r.district].filter(Boolean).join('') || '区域未填'}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '类型',
      dataIndex: 'org_type',
      width: 110,
      render: (v: string) => <OrgTypeTag orgType={v} />,
    },
    {
      title: '负责人',
      dataIndex: 'owner_name',
      width: 100,
      render: (v: string | null) => v || <Typography.Text type="secondary">未指派</Typography.Text>,
    },
    {
      title: '项目数',
      dataIndex: 'project_count',
      width: 130,
      align: 'right',
      render: (v: number, r) => (
        <Tooltip
          title={`进行中 ${r.in_progress_count} · 已签约 ${r.signed_count}${
            r.lost_count ? ` · 已丢单 ${r.lost_count}` : ''
          }（点进客户详情看每个项目的环节与金额）`}
        >
          <a onClick={() => navigate(`/customers/${r.id}`)}>{v} 个</a>
        </Tooltip>
      ),
    },
    {
      title: '过程中价值',
      dataIndex: 'in_progress_value_fen',
      width: 140,
      align: 'right',
      render: (v: number) => (
        <Tooltip title="过程中价值 = 未签约项目金额合计（已签约按合同额另行统计，已丢单不算价值）">
          <span style={{ fontWeight: 600 }}>¥{fenToWan(v)} 万</span>
        </Tooltip>
      ),
    },
    {
      title: '已签约额',
      dataIndex: 'signed_value_fen',
      width: 130,
      align: 'right',
      render: (v: number) => (
        <Tooltip title="已签约额 = 已签约项目的合同额合计（合同额为空时回落到预算额）">
          <span style={{ color: 'var(--success)', fontWeight: 600 }}>¥{fenToWan(v)} 万</span>
        </Tooltip>
      ),
    },
    {
      title: '已丢单金额',
      key: 'lost',
      width: 130,
      align: 'right',
      render: (_, r) =>
        r.lost_value_fen > 0 ? (
          <Tooltip title="已丢单金额仅作展示与复盘，不计入过程中价值，也不算业绩">
            <Typography.Text type="secondary">¥{fenToWan(r.lost_value_fen)} 万</Typography.Text>
          </Tooltip>
        ) : (
          <Typography.Text type="secondary">-</Typography.Text>
        ),
    },
    {
      title: '待处理风险',
      dataIndex: 'pending_risk_count',
      width: 118,
      align: 'right',
      render: (v: number, r) =>
        v > 0 ? (
          <Tooltip title="该客户名下项目的「待处理」预警条数；点进客户详情看风险明细，或去风险与卡单中心统一处置">
            <Tag
              color="red"
              style={{ cursor: 'pointer' }}
              onClick={(e) => {
                e.stopPropagation();
                navigate(`/customers/${r.id}`);
              }}
            >
              <AlertOutlined /> {v} 条
            </Tag>
          </Tooltip>
        ) : (
          <Typography.Text type="secondary">无</Typography.Text>
        ),
    },
    {
      title: '按阶段金额',
      key: 'stages',
      width: 190,
      render: (_, r) =>
        r.stages?.length ? (
          <Space size={4} wrap>
            {r.stages.slice(0, 3).map((s) => (
              <Tooltip key={s.stage} title={`${s.stage_label}：${s.project_count} 个项目`}>
                <Tag color={s.signed ? 'green' : 'blue'}>
                  {s.stage_label} ¥{fenToWan(s.amount_fen)} 万
                </Tag>
              </Tooltip>
            ))}
            {r.stages.length > 3 && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                等 {r.stages.length} 个环节
              </Typography.Text>
            )}
          </Space>
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            暂无项目
          </Typography.Text>
        ),
    },
    {
      title: '建档时间',
      dataIndex: 'created_at',
      width: 108,
      render: (v: string | null) => fmtDate(v),
    },
  ];

  const filtered = Boolean(keyword) || scope === 'mine';

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title="客户"
        subtitle="一个客户名下可以有多个项目商机——先看客户整体价值，再点进客户看每个项目走到哪一步。"
        demo
        tags={
          <Space size={6} wrap>
            <PanelFlag>客户-项目两层</PanelFlag>
          </Space>
        }
        actions={
          <Space wrap>
            <Segmented
              value={scope}
              options={[
                { value: 'all', label: '全部客户' },
                { value: 'mine', label: '只看我负责' },
              ]}
              onChange={(v) => {
                setScope(v as 'all' | 'mine');
                setPage(1);
              }}
            />
            <Input.Search
              allowClear
              placeholder="搜索客户名称"
              style={{ width: 220 }}
              onSearch={(v) => {
                setKeyword(v.trim());
                setPage(1);
              }}
            />
          </Space>
        }
      />

      {/* 一行 KPI 大数字：客户数 / 项目数 / 过程中价值 / 已签约额 */}
      <Row gutter={[12, 12]}>
        <Col xs={24} sm={12} xl={6}>
          <KpiCard
            label="客户数"
            value={summary?.customer_count ?? 0}
            suffix="家"
            icon={<ShopOutlined />}
            tone="brand"
            hint="当前筛选条件下的客户总数（客户 = 学校 / 教育局 / 机构，同名视为同一客户）"
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <KpiCard
            label="项目数"
            value={summary?.project_count ?? 0}
            suffix="个"
            icon={<FolderOpenOutlined />}
            tone="purple"
            hint="这些客户名下全部项目商机数——每个项目各自走一遍 7 环节、各有金额"
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <KpiCard
            label="过程中价值"
            value={`¥${fenToWan(summary?.in_progress_value_fen ?? 0)}`}
            suffix="万"
            icon={<TeamOutlined />}
            tone="amber"
            hint="过程中价值 = 未签约项目金额合计；已丢单不计入。这是「还在跑的机会」，不等于已到手收入"
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <KpiCard
            label="已签约额"
            value={`¥${fenToWan(summary?.signed_value_fen ?? 0)}`}
            suffix="万"
            icon={<WalletOutlined />}
            tone="green"
            hint="已签约额 = 已签约项目的合同额合计（合同额为空时回落到预算额）"
          />
        </Col>
      </Row>

      {/*  口径说明：后端下发的原文，原样展示（判据 C3，不自己编口径） */}
      <Footnote>
        {valueNote || '过程中价值=未签约项目金额合计；已签约按合同额计；已丢单项目金额已剔除'}
        。金额单位：万元（后端下发整数分，页面按分换算展示，精确到元见客户详情各项目）。
      </Footnote>

      {isError && (
        <NextStepGuide
          title="客户列表没能加载出来"
          description={`原因：接口 /customers 返回失败（${
            (error as { message?: string })?.message || '网络或服务异常'
          }）。可以稍后重试；若持续失败请把这条提示发给系统管理员。`}
          actions={
            <Space>
              <Button type="primary" onClick={() => refetch()}>
                重新加载
              </Button>
              <Button onClick={() => navigate('/opportunities')}>直接去商机列表</Button>
            </Space>
          }
        />
      )}

      <Card>
        {filtered && (
          <div style={{ marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <PanelFlag>筛选中</PanelFlag>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              关键词「{keyword || '—'}」{scope === 'mine' ? ' · 只看我负责' : ''}；
              上方 KPI 与合计<strong>只统计筛选后的客户</strong>。
            </Typography.Text>
            <Button
              size="small"
              onClick={() => {
                setKeyword('');
                setScope('all');
                setPage(1);
              }}
            >
              清掉筛选
            </Button>
          </div>
        )}
        <Table<Customer>
          rowKey="id"
          size="small"
          loading={isLoading}
          dataSource={data?.items ?? []}
          columns={columns}
          scroll={{ x: 1400 }}
          locale={{
            emptyText: (
              <NextStepGuide
                title={filtered ? '没有符合条件的客户' : '还没有客户档案'}
                description={
                  filtered
                    ? '换个关键词，或清掉「只看我负责」再试。客户档案由线索录入时自动建档（同名视为同一客户），不需要手工新建。'
                    : '客户是线索录入时自动建档的（录入线索填的客户名称会归集到这里），不需要单独维护。先去线索池录入或导入客户线索。'
                }
                actions={
                  <Space wrap>
                    {filtered && (
                      <Button
                        onClick={() => {
                          setKeyword('');
                          setScope('all');
                          setPage(1);
                        }}
                      >
                        清掉筛选
                      </Button>
                    )}
                    <Button type="primary" onClick={() => navigate('/leads/new')}>
                      新增线索
                    </Button>
                    <Button onClick={() => navigate('/leads')}>去线索池</Button>
                  </Space>
                }
              />
            ),
          }}
          onRow={(r) => ({
            onClick: () => navigate(`/customers/${r.id}`),
            style: { cursor: 'pointer' },
          })}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total: data?.total ?? 0,
            showSizeChanger: false,
            showTotal: (t) => `共 ${t} 家客户`,
            onChange: (p) => setPage(p),
          }}
        />
      </Card>

      <Footnote>
        「过程中价值」与「已签约额」是<strong>两个并列口径</strong>，不能相加当业绩：
        过程中价值只算未签约项目，已签约额按合同额另算；已丢单金额仅作展示与复盘。
        风险数为该客户名下项目的「待处理」预警条数，点进客户详情或去「风险与卡单中心」处置。
        客户档案由线索录入自动建档（客户名称 + 省市区归一），本页不提供手工新建客户
        {role === 'biz_support' ? '；方案商务部门按矩阵主要看合同相关字段。' : '。'}
      </Footnote>
    </Space>
  );
}
