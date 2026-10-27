import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Button,
  Card,
  Col,
  Progress,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  FundProjectionScreenOutlined,
  ReloadOutlined,
  RiseOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import { fetchConversion, fetchFunnel, fetchStaffOptions } from '@/api/dashboard';
import SimpleBar from '@/components/SimpleBar';
import PageHeader from '@/components/PageHeader';
import KpiCard from '@/components/KpiCard';
import { Footnote, NextStepGuide } from '@/components/Guide';
import { ROLE_LABEL } from '@/utils/constants';
import { fenToWan, fmtPercent, rateToPercent } from '@/utils/format';
import type { ConversionPair, FunnelItem, StaffOption } from '@/types';

/** 小数差 → 「N 个百分点」（判据 C3：转化率差值单位一律用百分点，不写 %） */
function pointDiff(v?: number | null, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '-';
  return `${v >= 0 ? '+' : ''}${(v * 100).toFixed(digits)} 个百分点`;
}

/** 按负责人对比时最多取前 8 个负责人（每个人一次请求，避免请求爆炸） */
const MAX_COMPARE = 8;

/** 负责人对比行（团队视角的补充区块） */
interface CompareRow {
  user_id: string;
  user_name: string;
  role: string;
  count: number;
  total_amount_fen: number;
  weighted_amount_fen: number;
}

/**
 * 4.3 商机漏斗与预测（管理者 / 团队视角，只读）。
 * 与 2.3「业绩漏斗与预测」同源（同一后端接口），差别是：
 * ① 默认看团队全部；② 增加各环节金额占比与合计行；③ 增加按负责人的横向对比。
 */
export default function OpportunityFunnel() {
  const navigate = useNavigate();
  const [ownerId, setOwnerId] = useState<string | undefined>();

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['reports-opportunity-funnel', { owner_id: ownerId }],
    queryFn: () => fetchFunnel({ owner_id: ownerId }),
  });

  const { data: staff } = useQuery({
    queryKey: ['dashboard-staff-options'],
    queryFn: fetchStaffOptions,
    retry: false,
  });

  /** 阶段转化率：实际 vs 基准（后端会标出是否跌破预警阈值） */
  const { data: conversion, isLoading: conversionLoading } = useQuery({
    queryKey: ['reports-opportunity-conversion', { owner_id: ownerId }],
    queryFn: () => fetchConversion({ owner_id: ownerId }),
  });

  const funnel = useMemo(() => data?.funnel ?? [], [data]);

  /* ---------------- 合计 ---------------- */
  const totals = funnel.reduce(
    (acc, f) => ({
      count: acc.count + (f.count || 0),
      amount: acc.amount + (f.amount_fen || 0),
      weighted: acc.weighted + (f.weighted_amount_fen || 0),
    }),
    { count: 0, amount: 0, weighted: 0 },
  );

  /* ---------------- 阶段中文名（用于把基准 key 翻译成中文） ---------------- */
  const stageLabel = useMemo(() => {
    const map: Record<string, string> = {};
    funnel.forEach((f) => {
      map[f.stage] = f.label;
    });
    return map;
  }, [funnel]);

  /** 基准 key 形如 s1_valid_opportunity->s2_customer_reached */
  const baselineRows = useMemo(
    () =>
      Object.entries(data?.baseline ?? {}).map(([path, v]) => {
        const [from, to] = path.split('->');
        return {
          path,
          label: `${stageLabel[from] ?? from} → ${stageLabel[to] ?? to}`,
          value: v,
        };
      }),
    [data, stageLabel],
  );

  /* ---------------- 统计口径 ---------------- */
  const scopeLabel = (() => {
    if (!data) return '-';
    if (data.owner_scope === 'team') return '团队（全部市场人员）';
    const hit = (staff?.items ?? []).find((s) => s.user_id === data.owner_scope);
    return hit?.name ?? '指定负责人';
  })();

  /* ---------------- 按负责人对比（最多前 8 个） ---------------- */
  const owners = useMemo(() => (staff?.items ?? []).slice(0, MAX_COMPARE), [staff]);
  const ownerKey = owners.map((o) => o.user_id).join(',');

  const { data: compareRows, isLoading: compareLoading } = useQuery({
    queryKey: ['reports-funnel-compare', ownerKey],
    enabled: owners.length > 0,
    queryFn: async () => {
      const settled = await Promise.all(
        owners.map(async (o: StaffOption): Promise<CompareRow | null> => {
          try {
            const r = await fetchFunnel({ owner_id: o.user_id });
            return {
              user_id: o.user_id,
              user_name: o.name,
              role: o.role,
              count: r.funnel.reduce((a, f) => a + (f.count || 0), 0),
              total_amount_fen: r.total_amount_fen,
              weighted_amount_fen: r.weighted_amount_fen,
            };
          } catch {
            // 单个负责人取数失败不影响整块对比
            return null;
          }
        }),
      );
      return settled
        .filter((r): r is CompareRow => r !== null)
        .sort((a, b) => b.weighted_amount_fen - a.weighted_amount_fen);
    },
  });

  const compareColumns: ColumnsType<CompareRow> = [
    { title: '负责人', dataIndex: 'user_name', width: 130 },
    {
      title: '角色',
      dataIndex: 'role',
      width: 130,
      render: (v: string) => ROLE_LABEL[v] ?? v,
    },
    { title: '在推进商机数', dataIndex: 'count', width: 130, align: 'right' },
    {
      title: '总金额（万元）',
      dataIndex: 'total_amount_fen',
      width: 150,
      align: 'right',
      render: (v: number) => fenToWan(v),
    },
    {
      title: '加权金额（万元）',
      dataIndex: 'weighted_amount_fen',
      width: 160,
      align: 'right',
      render: (v: number) => (
        <Typography.Text strong style={{ color: '#1677ff' }}>
          {fenToWan(v)}
        </Typography.Text>
      ),
    },
  ];

  /* ---------------- 漏斗表（含金额占比与隐含成交概率） ---------------- */
  const funnelColumns: ColumnsType<FunnelItem> = [
    { title: '环节', dataIndex: 'label' },
    { title: '商机数', dataIndex: 'count', width: 90, align: 'right' },
    {
      title: '在谈金额（万元）',
      dataIndex: 'amount_fen',
      width: 150,
      align: 'right',
      render: (v: number) => fenToWan(v),
    },
    {
      title: '金额占比',
      key: 'share',
      width: 190,
      render: (_, r) => {
        const share = totals.amount > 0 ? r.amount_fen / totals.amount : 0;
        return (
          <Progress
            percent={rateToPercent(share)}
            size="small"
            strokeColor="#1677ff"
            format={() => fmtPercent(share)}
          />
        );
      },
    },
    {
      title: '加权预测（万元）',
      dataIndex: 'weighted_amount_fen',
      width: 160,
      align: 'right',
      render: (v: number) => fenToWan(v),
    },
    {
      title: '隐含成交概率',
      key: 'prob',
      width: 130,
      align: 'right',
      render: (_, r) =>
        r.amount_fen > 0 ? (
          <Tag color="geekblue">{fmtPercent(r.weighted_amount_fen / r.amount_fen)}</Tag>
        ) : (
          '-'
        ),
    },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {/* 判据 C2：一句话说清"这一页先干什么"；筛选与动作收进 PageHeader actions */}
      <PageHeader
        title="4.3 商机漏斗与预测"
        subtitle="先看各环节在谈金额与加权预测总额，再看哪一段转化率跌破基准；需要下钻就按负责人筛选，再回「2.1 商机列表」推进具体商机。"
        demo
        meta={[
          { label: '统计口径', value: scopeLabel },
          { label: '默认范围', value: '团队全部，可按负责人下钻' },
          { label: '环节数', value: `${funnel.length} 个` },
        ]}
        actions={
          <>
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              style={{ width: 220 }}
              placeholder="负责人：团队全部"
              value={ownerId}
              onChange={(v) => setOwnerId(v ?? undefined)}
              options={owners.map((s) => ({
                value: s.user_id,
                label: `${s.name}（${ROLE_LABEL[s.role] ?? s.role}）`,
              }))}
            />
            <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
              刷新
            </Button>
            <Button onClick={() => navigate('/opportunities')}>前往 2.1 商机列表</Button>
          </>
        }
      />

      {/* 判据 C3：每个数字都能就地看到口径（KpiCard hint） */}
      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}>
          <KpiCard
            label="在推进商机数"
            value={totals.count}
            icon={<TeamOutlined />}
            tone="brand"
            hint="口径：当前处于 7 环节内的商机条数（各环节数量之和），不区分金额大小。"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="在谈总金额"
            value={fenToWan(data?.total_amount_fen)}
            suffix="万"
            icon={<FundProjectionScreenOutlined />}
            tone="purple"
            hint="口径：在谈总金额 = 各环节商机金额之和，不乘成交概率。"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="加权预测签约额"
            value={fenToWan(data?.weighted_amount_fen)}
            suffix="万"
            icon={<RiseOutlined />}
            tone="green"
            rateText={`加权 / 在谈 ≈ ${fmtPercent(
              data?.total_amount_fen ? (data?.weighted_amount_fen ?? 0) / data.total_amount_fen : null,
              0,
            )}`}
            hint="口径：加权金额 = 环节金额 × 该环节成交概率。概率由后端按「从该环节走到签约」的基准转化率连乘得到，逐条汇总；只用于预测，不代表已签约。"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="跌破基准的转化段"
            value={conversion?.breached?.length ?? 0}
            total={`/ ${conversion?.pairs?.length ?? 0} 段`}
            icon={<FundProjectionScreenOutlined />}
            tone={(conversion?.breached?.length ?? 0) > 0 ? 'red' : 'green'}
            danger={(conversion?.breached?.length ?? 0) > 0}
            hint={`口径：实际转化率低于「基准 × ${fmtPercent(conversion?.warn_ratio ?? 0.9, 0)}」即判定为跌破；样本少于 ${conversion?.min_sample ?? 5} 条的转化段不做判定。`}
          />
        </Col>
      </Row>

      <Card size="small" title="各环节商机数与金额（含占比 / 合计）">
        <Table<FunnelItem>
          rowKey="stage"
          size="small"
          loading={isLoading}
          columns={funnelColumns}
          dataSource={funnel}
          pagination={false}
          scroll={{ x: 900 }}
          summary={() =>
            funnel.length ? (
              <Table.Summary.Row>
                <Table.Summary.Cell index={0}>
                  <Typography.Text strong>合计</Typography.Text>
                </Table.Summary.Cell>
                <Table.Summary.Cell index={1} align="right">
                  <Typography.Text strong>{totals.count}</Typography.Text>
                </Table.Summary.Cell>
                <Table.Summary.Cell index={2} align="right">
                  <Typography.Text strong>{fenToWan(totals.amount)}</Typography.Text>
                </Table.Summary.Cell>
                <Table.Summary.Cell index={3}>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    100%（各环节占在谈总额比例）
                  </Typography.Text>
                </Table.Summary.Cell>
                <Table.Summary.Cell index={4} align="right">
                  <Typography.Text strong>{fenToWan(totals.weighted)}</Typography.Text>
                </Table.Summary.Cell>
                <Table.Summary.Cell index={5} align="right">
                  <Typography.Text strong>
                    {totals.amount > 0 ? fmtPercent(totals.weighted / totals.amount) : '-'}
                  </Typography.Text>
                </Table.Summary.Cell>
              </Table.Summary.Row>
            ) : null
          }
          locale={{
            emptyText: (
              <NextStepGuide
                title="暂无漏斗数据"
                description="漏斗只统计已进入 7 环节的商机；先在「2.1 商机列表」把有效线索推进到环节一，这里立刻就有数据。"
                actions={
                  <Button size="small" type="primary" onClick={() => navigate('/opportunities')}>
                    去 2.1 商机列表
                  </Button>
                }
              />
            ),
          }}
        />
        <div style={{ marginTop: 12 }}>
          <SimpleBar
            items={funnel.map((f) => ({
              label: f.label,
              value: f.count,
              extra: `金额 ${fenToWan(f.amount_fen)} 万 · 加权 ${fenToWan(f.weighted_amount_fen)} 万`,
            }))}
            emptyText="暂无漏斗数据：先推进商机到环节一"
          />
        </div>
      </Card>

      <Row gutter={12}>
        <Col xs={24} lg={12}>
          <Card size="small" title="加权预测算法说明">
            <Space direction="vertical" size={6} style={{ width: '100%' }}>
              <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
                <Typography.Text strong>加权金额 = 该环节在谈金额 × 该环节成交概率</Typography.Text>
                。概率取「环节成交概率字典 / 阶段转化率基准链」：后端按从当前环节一路走到「七 合同签约」的
                基准转化率连乘得到每个环节的成交概率，再乘以商机金额，逐条汇总成上表的「加权预测」。
              </Typography.Paragraph>
              <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
                表中「隐含成交概率」= 该环节加权金额 ÷ 该环节在谈金额，是后端按上面口径算出的反推值，
                可直接用来核对权重是否合理（例如「三 方案报价」约 25%、「二 客户触达」约 12.5%）。
              </Typography.Paragraph>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                注意：基准是「相邻环节」的转化率，跨环节（如三 → 七）在基准表里已按整段给出，
                因此不要再自行连乘，避免重复打折。
              </Typography.Text>
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card
            size="small"
            title="阶段转化率：实际 vs 基准"
            extra={
              conversion && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  样本要求 ≥{conversion.min_sample}；跌破基准 {fmtPercent(conversion.warn_ratio)} 即预警
                  {conversion.breached?.length ? `　 ${conversion.breached.length} 组预警` : ''}
                </Typography.Text>
              )
            }
          >
            {conversionLoading ? (
              <Typography.Text type="secondary">加载中…</Typography.Text>
            ) : conversion?.pairs?.length ? (
              <Table<ConversionPair>
                rowKey="path"
                size="small"
                columns={[
                  {
                    title: '转化路径',
                    key: 'path',
                    render: (_, r) => `${r.from_stage_label} → ${r.to_stage_label}`,
                  },
                  {
                    title: '实际',
                    dataIndex: 'rate',
                    width: 90,
                    align: 'right',
                    render: (v: number, r: ConversionPair) =>
                      r.from_count < conversion.min_sample ? (
                        <Tooltip title={`样本不足（${r.from_count} < ${conversion.min_sample}），不做判定`}>
                          <Typography.Text type="secondary">{fmtPercent(v)}</Typography.Text>
                        </Tooltip>
                      ) : (
                        <Tag color={r.alert ? 'red' : 'green'}>{fmtPercent(v)}</Tag>
                      ),
                  },
                  {
                    title: '基准',
                    dataIndex: 'baseline',
                    width: 90,
                    align: 'right',
                    render: (v: number) => fmtPercent(v),
                  },
                  {
                    title: '差值（百分点）',
                    key: 'gap',
                    width: 140,
                    align: 'right',
                    render: (_, r) => (
                      <Tooltip title="转化率差值一律用「百分点」表示，例如 30% → 26% 记作 -4.0 个百分点">
                        <Typography.Text type={r.gap < 0 ? 'danger' : 'success'}>
                          {pointDiff(r.gap)}
                        </Typography.Text>
                      </Tooltip>
                    ),
                  },
                  {
                    title: '样本',
                    key: 'sample',
                    width: 110,
                    align: 'right',
                    render: (_, r) => `${r.from_count} → ${r.to_count}`,
                  },
                  {
                    title: '判定',
                    dataIndex: 'alert',
                    width: 90,
                    render: (v: boolean) => (v ? <Tag color="red">预警</Tag> : <Tag>正常</Tag>),
                  },
                ]}
                dataSource={conversion.pairs}
                pagination={false}
              />
            ) : baselineRows.length ? (
              <Table<{ path: string; label: string; value: number }>
                rowKey="path"
                size="small"
                columns={[
                  { title: '转化路径', dataIndex: 'label' },
                  {
                    title: '基准转化率',
                    dataIndex: 'value',
                    width: 130,
                    align: 'right',
                    render: (v: number) => <Tag color="blue">{fmtPercent(v)}</Tag>,
                  },
                ]}
                dataSource={baselineRows}
                pagination={false}
                locale={{
                  emptyText: (
                    <NextStepGuide
                      compact
                      title="暂无基准数据"
                      description="转化率基准（相邻环节成交概率）在「6.5 环节标准卡配置」里维护；设好后这里会自动出现对照。"
                      actions={
                        <Button size="small" onClick={() => navigate('/admin/stage-standards')}>
                          去 6.5 环节标准卡配置
                        </Button>
                      }
                    />
                  ),
                }}
              />
            ) : (
              <NextStepGuide
                title="暂无基准数据，无法判定转化率是否跌破基线"
                description="转化率基准 = 相邻环节的目标成交概率，在「6.5 环节标准卡配置」里按环节设定；未设基准时本页只显示实际转化率、不做预警判定。"
                actions={
                  <Button size="small" type="primary" onClick={() => navigate('/admin/stage-standards')}>
                    去 6.5 环节标准卡配置
                  </Button>
                }
              />
            )}
          </Card>
        </Col>
      </Row>

      <Card
        size="small"
        title={`按负责人对比（前 ${MAX_COMPARE} 位，按加权金额排序）`}
        extra={
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            团队视角的补充：逐人各取一次漏斗，用于横向比较投入与产出
          </Typography.Text>
        }
      >
        <Table<CompareRow>
          rowKey="user_id"
          size="small"
          loading={compareLoading}
          columns={compareColumns}
          dataSource={compareRows ?? []}
          pagination={false}
          locale={{
            emptyText: (
              <NextStepGuide
                compact
                title="暂无可对比的市场人员"
                description="接口未返回人员列表（可能是角色权限或人员未配置）；可先用上方「负责人」筛选下钻到单人口径。"
                actions={
                  <Button size="small" onClick={() => navigate('/admin/users')}>
                    去 6.1 用户管理核对人员
                  </Button>
                }
              />
            ),
          }}
        />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          说明：负责人较多时逐人请求会变慢，这里只对比前 {MAX_COMPARE} 位；如需看某人明细，请用上方「负责人」筛选下钻到单人口径。
        </Typography.Text>
      </Card>

      {/* 判据 C3：关键口径写在页脚，不只在文档里 */}
      <Card size="small">
        <Footnote>
          口径：① 加权金额 = 环节金额 × 该环节成交概率（概率由「从该环节走到签约」的基准转化率连乘得到，逐条汇总）；
          ② 表中「隐含成交概率」= 该环节加权金额 ÷ 该环节在谈金额，可用来反查权重是否合理；
          ③ 转化率差值单位一律为「百分点」（如 30% → 26% 记作 -4.0 个百分点），不写成百分比；
          ④ 样本少于 {conversion?.min_sample ?? 5} 条的转化段不做预警判定；
          ⑤ 基准是相邻环节口径，跨环节基准已在基准表里整段给出，不要再自行连乘；
          ⑥ 加权预测只用于预测，不代表已签约。
        </Footnote>
      </Card>
    </Space>
  );
}
