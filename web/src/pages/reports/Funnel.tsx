import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, Col, Row, Select, Space, Table, Tooltip, Typography } from 'antd';
import {
  FundProjectionScreenOutlined,
  ReloadOutlined,
  RiseOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import { fetchConversion, fetchFunnel, fetchStaffOptions } from '@/api/dashboard';
import PageHeader from '@/components/PageHeader';
import KpiCard from '@/components/KpiCard';
import { Footnote, NextStepGuide } from '@/components/Guide';
import { useAuthStore } from '@/store/auth';
import { fenToWan, fmtPercent } from '@/utils/format';
import type { ConversionPair, FunnelItem } from '@/types';

/**
 * 商机漏斗阶段的完整顺序（后端 STAGE_ORDER）：用于把「三 → 七」这类跨环节基准
 * 落到它跨越的每一行上参考——这不改口径，只是让基准在界面上可见。
 */
const STAGE_ORDER = [
  's1_valid_opportunity',
  's2_customer_reached',
  's3_proposal_quote',
  's4_budget_confirmed',
  's5_project_reviewed',
  's6_bidding',
  's7_contract_signed',
];

const stageIdx = (stage: string | null | undefined) => STAGE_ORDER.indexOf(stage ?? '');

/** 一行转化率对照（实际 + 基准 + 差值百分点） */
interface ConversionRow {
  key: string;
  pathLabel: string;
  fromLabel: string;
  toLabel: string;
  fromCount: number;
  toCount: number;
  rate: number;
  /** 同段基准（后端 pairs 里已给），没有则用跨段基准兜底 */
  baseline?: number;
  warnThreshold?: number;
  alert?: boolean;
}

/** 百分点差值展示：负值红色（设计稿 06「对比（百分点）」列） */
function GapText({ gap, digits = 1 }: { gap: number; digits?: number }) {
  return (
    <Typography.Text type={gap < 0 ? 'danger' : 'success'} strong>
      {gap >= 0 ? '+' : ''}
      {(gap * 100).toFixed(digits)}
    </Typography.Text>
  );
}

/**
 * 2.3 业绩漏斗与预测 —— 对齐设计稿 06 左栏：
 * 标题与筛选条件同一行 · 4 个 KPI 卡（图标 + 标签 + 大号数字 + 单位 + 灰色「演示数据」）
 * · 漏斗用横向条形表示各环节金额与条数（宽度按占比） · 右侧列出转化率与基准差值（百分点，负值红色）。
 *
 * 判据：C2（空态给"下一步 + 去哪做"）、C3（加权口径就地可见：hint + 页脚）。
 */
export default function Funnel() {
  const navigate = useNavigate();
  const role = useAuthStore((s) => s.user?.role);
  const isManager = role === 'admin' || role === 'market_manager';
  const [ownerId, setOwnerId] = useState<string | undefined>();

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['dashboard-funnel', { owner_id: ownerId }],
    queryFn: () => fetchFunnel({ owner_id: ownerId }),
  });

  /** 实际转化率 vs 基准（同一后端口径，用于右侧「转化率与基准差值」） */
  const convQuery = useQuery({
    queryKey: ['dashboard-conversion', { owner_id: ownerId }],
    queryFn: () => fetchConversion({ owner_id: ownerId }),
  });

  const { data: staff } = useQuery({
    queryKey: ['dashboard-staff-options'],
    queryFn: fetchStaffOptions,
    enabled: isManager,
    retry: false,
  });

  const funnel = useMemo(() => data?.funnel ?? [], [data]);
  const byStage = useMemo(() => new Map(funnel.map((f) => [f.stage, f])), [funnel]);

  const scopeLabel = (() => {
    if (!data) return '-';
    if (data.owner_scope === 'team') return '团队（全部市场人员）';
    const hit = (staff?.items ?? []).find((s) => s.user_id === data.owner_scope);
    return `${hit?.name ?? '本人'}`;
  })();

  const totalCount = funnel.reduce((a, f) => a + (f.count || 0), 0);
  const totalAmount = data?.total_amount_fen ?? 0;
  const maxAmount = Math.max(...funnel.map((f) => f.amount_fen), 1);
  const weightedRatio = totalAmount ? (data?.weighted_amount_fen ?? 0) / totalAmount : null;

  /**
   * 转化率对照行：以后端 pairs（相邻环节口径）为准，并按 STAGE_ORDER 补齐
   * 每一行在本段上的跨段基准提示（如「三 → 七 25%」），便于就地看到"差在哪"。
   */
  const convRows = useMemo<ConversionRow[]>(() => {
    const pairs: ConversionPair[] = convQuery.data?.pairs ?? [];
    if (pairs.length) {
      return pairs.map((p) => {
        return {
          key: p.path,
          pathLabel: `${p.from_stage_label} → ${p.to_stage_label}`,
          fromLabel: p.from_stage_label,
          toLabel: p.to_stage_label,
          fromCount: p.from_count,
          toCount: p.to_count,
          rate: p.rate,
          baseline: p.baseline,
          warnThreshold: p.warn_threshold,
          alert: p.alert,
        };
      });
    }
    // pairs 为空（样本不足 / 无数据）时退回按漏斗条数自算相邻环节转化率
    const rows: ConversionRow[] = [];
    for (let i = 1; i < STAGE_ORDER.length; i += 1) {
      const from = byStage.get(STAGE_ORDER[i - 1]);
      const to = byStage.get(STAGE_ORDER[i]);
      if (!from || !to) continue;
      if (from.count === 0 && to.count === 0) continue;
      rows.push({
        key: `${STAGE_ORDER[i - 1]}-${STAGE_ORDER[i]}`,
        pathLabel: `${from.label} → ${to.label}`,
        fromLabel: from.label,
        toLabel: to.label,
        fromCount: from.count,
        toCount: to.count,
        rate: from.count ? to.count / from.count : 0,
        baseline: undefined,
      });
    }
    return rows;
  }, [convQuery.data, byStage]);

  /** 跨段基准按环节区间落到对应行：如「三 → 七 25%」对 三→四 / 四→五 / 五→六 / 六→七 都有参考意义 */
  const spanBaseline = useMemo(() => {
    const map = new Map<string, { value: number; label: string }>();
    Object.entries(data?.baseline ?? {}).forEach(([path, value]) => {
      const [from, to] = path.split('->');
      const a = stageIdx(from);
      const b = stageIdx(to);
      if (a < 0 || b < 0 || b - a <= 1) return;
      const fromLabel = byStage.get(from)?.label ?? from;
      const toLabel = byStage.get(to)?.label ?? to;
      for (let i = a; i < b; i += 1) {
        map.set(`${STAGE_ORDER[i]}-${STAGE_ORDER[i + 1]}`, {
          value,
          label: `${fromLabel} → ${toLabel} ${fmtPercent(value, 0)}`,
        });
      }
    });
    return map;
  }, [data?.baseline, byStage]);

  const convColumns = [
    {
      title: '环节转化',
      width: 150,
      render: (_: unknown, r: ConversionRow) => (
        <Tooltip title={`样本：上一环节 ${r.fromCount} 条 → 本环节 ${r.toCount} 条`}>
          <span style={{ fontSize: 13 }}>{r.pathLabel}</span>
        </Tooltip>
      ),
    },
    {
      title: '实际转化率',
      dataIndex: 'rate',
      width: 120,
      render: (v: number, r: ConversionRow) => (
        <Space direction="vertical" size={2} style={{ width: '100%' }}>
          <Typography.Text strong style={{ fontSize: 13 }}>
            {fmtPercent(v)}
          </Typography.Text>
          <div
            style={{
              height: 6,
              borderRadius: 3,
              background: '#EEF1FF',
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                width: `${Math.min(Math.round(v * 100), 100)}%`,
                height: '100%',
                background: r.alert ? '#F04438' : '#465FFF',
              }}
            />
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            样本 {r.fromCount} / {r.toCount}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '基准转化率',
      width: 110,
      render: (_: unknown, r: ConversionRow) => {
        if (r.baseline !== undefined) {
          return <span style={{ fontSize: 13 }}>{fmtPercent(r.baseline)}</span>;
        }
        const span = spanBaseline.get(r.key);
        return span ? (
          <Tooltip title={`跨环节基准：${span.label}（按环节区间参考）`}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {fmtPercent(span.value, 0)} 区间
            </Typography.Text>
          </Tooltip>
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            未设基准
          </Typography.Text>
        );
      },
    },
    {
      title: '对比（百分点）',
      width: 120,
      render: (_: unknown, r: ConversionRow) => {
        const base = r.baseline ?? spanBaseline.get(r.key)?.value;
        if (base === undefined) {
          return (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              无法对比
            </Typography.Text>
          );
        }
        return (
          <Space direction="vertical" size={0}>
            <GapText gap={r.rate - base} />
            {r.alert && (
              <Typography.Text type="danger" style={{ fontSize: 11 }}>
                低于预警线 {fmtPercent(r.warnThreshold ?? 0, 0)}
              </Typography.Text>
            )}
          </Space>
        );
      },
    },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {/* 设计稿 06：标题与筛选条件同一行 */}
      <PageHeader
        title="业绩漏斗与预测"
        demo
        subtitle="先看加权预测总额，再看哪一环节积压的金额最多；推进具体商机去「2.1 商机列表」。"
        actions={
          <Space wrap size={8}>
            <Space size={6}>
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                负责人：
              </Typography.Text>
              {isManager ? (
                <Select
                  allowClear
                  showSearch
                  optionFilterProp="label"
                  style={{ width: 200 }}
                  placeholder="团队全部"
                  value={ownerId}
                  onChange={(v) => setOwnerId(v ?? undefined)}
                  options={(staff?.items ?? []).map((s) => ({
                    value: s.user_id,
                    label: `${s.name}（${s.role}）`,
                  }))}
                />
              ) : (
                <Typography.Text strong style={{ fontSize: 13 }}>
                  本人
                </Typography.Text>
              )}
            </Space>
            <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
              刷新
            </Button>
          </Space>
        }
      />

      {/* 判据 C3：每个数字都能就地看到口径（Tooltip） */}
      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}>
          <KpiCard
            label="在推进商机数"
            value={totalCount}
            suffix="条"
            icon={<TeamOutlined />}
            tone="brand"
            hint="口径：当前处于 7 环节内的商机条数（各环节数量之和，不含丢单 / 已签约）"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="在谈总金额"
            value={fenToWan(totalAmount)}
            suffix="万"
            icon={<FundProjectionScreenOutlined />}
            tone="purple"
            hint="口径：在谈总金额 = 各环节商机预算金额之和，不乘成交概率"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="加权预测签约额"
            value={fenToWan(data?.weighted_amount_fen)}
            suffix="万"
            icon={<RiseOutlined />}
            tone="green"
            rateText={`加权 / 在谈 ≈ ${fmtPercent(weightedRatio, 0)}`}
            hint="口径：加权金额 = 环节金额 × 该环节成交概率；概率由「从该环节走到签约」的基准转化率连乘得到，逐条汇总。只用于预测，不代表已签约"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="统计口径"
            value={scopeLabel}
            icon={<TeamOutlined />}
            tone="gray"
            hint="口径：专员默认只看本人；管理层可选择具体负责人或看团队全部。本页与 4.3 商机漏斗与预测同源（同一后端接口）"
          />
        </Col>
      </Row>

      <Row gutter={[12, 12]}>
        {/* ── 漏斗：横向条形（宽度按金额占比），条上标金额与条数 ── */}
        <Col xs={24} xl={14}>
          <Card
            size="small"
            title="商机漏斗（按环节）"
            extra={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                条形宽度 = 该环节在谈金额 ÷ 最大值
              </Typography.Text>
            }
          >
            {funnel.length ? (
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                {funnel.map((f: FunnelItem) => {
                  const pct = maxAmount ? Math.round((f.amount_fen / maxAmount) * 100) : 0;
                  const share = totalAmount ? f.amount_fen / totalAmount : 0;
                  return (
                    <div key={f.stage}>
                      <div
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'baseline',
                          gap: 8,
                        }}
                      >
                        <Typography.Text style={{ fontSize: 13 }}>{f.label}</Typography.Text>
                        <Space size={10}>
                          <Typography.Text strong style={{ fontSize: 13 }}>
                            {fenToWan(f.amount_fen)} 万
                          </Typography.Text>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {f.count} 条 · 占比 {fmtPercent(share, 1)}
                          </Typography.Text>
                        </Space>
                      </div>
                      <div
                        style={{
                          marginTop: 4,
                          height: 16,
                          borderRadius: 4,
                          background: '#F2F4F7',
                          overflow: 'hidden',
                        }}
                      >
                        <Tooltip
                          title={`${f.label}：金额 ${fenToWan(f.amount_fen)} 万（占在谈总额 ${fmtPercent(share, 1)}）· ${f.count} 条 · 加权预测 ${fenToWan(f.weighted_amount_fen)} 万`}
                        >
                          <div
                            style={{
                              width: `${Math.max(pct, f.amount_fen > 0 ? 4 : 0)}%`,
                              height: '100%',
                              background: f.amount_fen > 0 ? '#465FFF' : 'transparent',
                              borderRadius: 4,
                              transition: 'width .2s',
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
                title="暂无漏斗数据"
                description="漏斗只统计已进入 7 环节的在途商机。先在「2.1 商机列表」把有效线索推进到环节一，这里立刻就有数据。"
                actions={
                  <Space>
                    <Button type="primary" onClick={() => navigate('/opportunities')}>
                      去 2.1 商机列表
                    </Button>
                    <Button onClick={() => navigate('/leads')}>去线索池</Button>
                  </Space>
                }
              />
            )}
          </Card>
        </Col>

        {/* ── 转化率与基准差值（百分点，负值红） ── */}
        <Col xs={24} xl={10}>
          <Card
            size="small"
            title="转化率与基准差值"
            extra={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                差值单位：百分点
              </Typography.Text>
            }
          >
            <Table<ConversionRow>
              rowKey="key"
              size="small"
              pagination={false}
              loading={convQuery.isLoading}
              dataSource={convRows}
              columns={convColumns}
              locale={{
                emptyText: (
                  <NextStepGuide
                    compact
                    title="暂无转化率数据"
                    description={`分母（上一环节商机数）小于最小样本量 ${convQuery.data?.min_sample ?? 5} 条时不计算，避免小样本误报；先把商机推进到下一环节即可看到。`}
                    actions={
                      <Button size="small" type="primary" onClick={() => navigate('/opportunities')}>
                        去 2.1 商机列表
                      </Button>
                    }
                  />
                ),
              }}
            />
            <div style={{ marginTop: 8 }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                基准 = 相邻环节的目标成交概率（在「环节标准卡配置」里设定）；跨环节基准（如「三 → 七」）会在对应
                行上以「区间」标注，仅作参考，不参与加权预测计算。
              </Typography.Text>
            </div>
          </Card>
        </Col>
      </Row>

      <Card size="small" title="环节明细">
        <Table<FunnelItem>
          rowKey="stage"
          size="small"
          loading={isLoading}
          dataSource={funnel}
          pagination={false}
          locale={{
            emptyText: (
              <NextStepGuide
                compact
                title="暂无数据"
                description="还没有商机进入环节；先在「2.1 商机列表」推进线索到环节一。"
                actions={
                  <Button size="small" onClick={() => navigate('/opportunities')}>
                    去 2.1 商机列表
                  </Button>
                }
              />
            ),
          }}
          columns={[
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
              title: '加权预测（万元）',
              dataIndex: 'weighted_amount_fen',
              width: 160,
              align: 'right',
              render: (v: number) => fenToWan(v),
            },
            {
              title: '隐含成交概率',
              key: 'prob',
              width: 150,
              align: 'right',
              render: (_, r) =>
                r.amount_fen > 0 ? (
                  <Tooltip title="隐含成交概率 = 该环节加权金额 ÷ 该环节在谈金额，用来反查权重是否合理">
                    <Typography.Text type="secondary">
                      {fmtPercent(r.weighted_amount_fen / r.amount_fen)}
                    </Typography.Text>
                  </Tooltip>
                ) : (
                  '-'
                ),
            },
          ]}
        />
      </Card>

      {/* 判据 C3：口径写在页脚，不只在文档里 */}
      <Card size="small">
        <Footnote>
          口径：① 加权金额 = 环节金额 × 该环节成交概率（概率由「从该环节走到签约」的基准转化率连乘得到，逐条汇总）；
          ② 「隐含成交概率」= 该环节加权金额 ÷ 该环节在谈金额，用来反查权重是否合理；
          ③ 转化率差值一律用「百分点」表示（如 30% → 26% 记作 -4.0 个百分点），不写成百分比变化；
          ④ 基准是相邻环节口径，跨环节基准（如「三 → 七」）只在界面上以「区间」标注作参考，不自行连乘、不参与加权计算；
          ⑤ 加权预测只用于预测，不代表已签约。
        </Footnote>
        <Footnote style={{ marginTop: 6 }}>
          边界：本页为只读预测视图，数字用于排优先级与资源安排，不作为个人考核依据。
        </Footnote>
      </Card>
    </Space>
  );
}
