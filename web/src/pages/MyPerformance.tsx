import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  Button,
  Card,
  Col,
  DatePicker,
  Progress,
  Row,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  AimOutlined,
  FundOutlined,
  ProjectOutlined,
  RiseOutlined,
  WalletOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { listOpportunities } from '@/api/opportunities';
import { fetchAchievement } from '@/api/targets';
import { useAuthStore } from '@/store/auth';
import { METRIC_LABEL } from '@/utils/constants';
import { fenToWan, fmtPercent } from '@/utils/format';
import KpiCard from '@/components/KpiCard';
import PageHeader from '@/components/PageHeader';
import { Footnote, NextStepGuide, NotForAssessment } from '@/components/Guide';

/**
 * 2.8 我的业绩 —— 对齐设计稿 06 左栏骨架：
 * KPI 卡（图标 + 标签 + 大号数字 + 单位）· 目标达成（达成率 = 实际 ÷ 目标）· 环节分布 · 页脚口径。
 *
 *  诚实底线：**未设目标时不许伪造达成率** —— 直接给空态并引导去「1.8 市场目标设定」。
 * 判据：C2（空态说清为什么没有达成率 + 去哪补）、C3（口径逐条写清）、C4（过程管理用，不作为考核依据）。
 */
export default function MyPerformance() {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const [period, setPeriod] = useState(dayjs().format('YYYY-MM'));

  const { data: achievement, isLoading: achLoading } = useQuery({
    queryKey: ['targets-achievement', period],
    queryFn: () => fetchAchievement(period),
  });

  // 复用商机列表接口汇总本人名下（后端已按数据权限自动收窄）
  const { data: opps, isLoading: oppLoading } = useQuery({
    queryKey: ['opportunities', 'mine-summary', 200],
    queryFn: () => listOpportunities({ page: 1, page_size: 200 }),
  });

  const mine = achievement?.items?.find((i) => i.user_id === user?.id);
  const metrics = mine?.metrics ?? [];

  const summary = useMemo(() => {
    const items = opps?.items ?? [];
    const validCount = items.filter((i) => i.status === 'valid').length;
    const weighted = items.reduce((s, i) => s + (i.weighted_amount_fen ?? 0), 0);
    const signed = items
      .filter((i) => i.status === 'signed')
      .reduce((s, i) => s + (i.contract_amount_fen ?? 0), 0);
    const paid = items.reduce((s, i) => s + (i.paid_amount_fen ?? 0), 0);

    // 各环节分布
    const stageMap = new Map<
      string,
      { stage: string; label: string; count: number; amount: number; weighted: number }
    >();
    items.forEach((i) => {
      const key = i.current_stage || 'unknown';
      const row = stageMap.get(key) ?? {
        stage: key,
        label: i.current_stage_label || i.current_stage || '未进入环节',
        count: 0,
        amount: 0,
        weighted: 0,
      };
      row.count += 1;
      row.amount += i.budget_amount_fen ?? 0;
      row.weighted += i.weighted_amount_fen ?? 0;
      stageMap.set(key, row);
    });

    return {
      total: opps?.total ?? items.length,
      validCount,
      weighted,
      signed,
      paid,
      stages: Array.from(stageMap.values()).sort((a, b) => b.count - a.count),
    };
  }, [opps]);

  const renderMetric = (metric: string, value: number) =>
    metric === 'valid_rate' ? fmtPercent(value) : value;

  /**
   * 目标达成：达成率 = 实际 ÷ 目标。
   *  目标为 0（未设目标）时不算达成率 —— 返回 null，界面给空态引导，绝不编一个数出来。
   */
  const targetRows = useMemo(
    () =>
      metrics.map((m) => {
        const target = m.target_value ?? 0;
        const actual = m.actual_value ?? 0;
        const hasTarget = target > 0;
        const rate = hasTarget ? actual / target : null;
        return { ...m, target, actual, hasTarget, rate };
      }),
    [metrics],
  );

  const overallRate = useMemo(() => {
    const valid = targetRows.filter((r) => r.hasTarget);
    if (!valid.length) return null;
    // 多指标不做加权平均（口径会被误读）；只在只有一个指标时给"总达成率"
    if (valid.length > 1) return null;
    return valid[0].rate;
  }, [targetRows]);

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <PageHeader
        title="我的业绩"
        subtitle={`看清楚自己 ${period} 的目标与实际——达成率 = 实际 ÷ 目标；每个数字的口径都写在卡片提示与页脚。`}
        demo
        meta={[
          { label: '统计期间', value: period },
          { label: '口径', value: '本人名下（后端按数据权限收窄）' },
        ]}
        actions={
          <Space wrap>
            <DatePicker
              picker="month"
              allowClear={false}
              value={dayjs(period, 'YYYY-MM')}
              onChange={(d) => d && setPeriod(d.format('YYYY-MM'))}
            />
            <Button onClick={() => navigate('/targets')}>市场目标设定</Button>
            <Button type="primary" onClick={() => navigate('/opportunities')}>
              去 2.1 商机列表
            </Button>
          </Space>
        }
      />

      <Row gutter={[12, 12]}>
        <Col xs={12} md={8} xl={5}>
          <KpiCard
            label="本人名下商机"
            value={summary.total}
            suffix="条"
            icon={<ProjectOutlined />}
            tone="brand"
            hint="本人名下的商机条数（后端已按数据权限自动收窄；口径与 2.1 商机列表一致）"
          />
        </Col>
        <Col xs={12} md={8} xl={5}>
          <KpiCard
            label="有效商机数"
            value={summary.validCount}
            suffix="条"
            icon={<RiseOutlined />}
            tone="green"
            hint="本人名下状态为「有效商机」的商机数（口径见 1.2 有效性判定：信息正确 + 有意愿）"
          />
        </Col>
        <Col xs={12} md={8} xl={5}>
          <KpiCard
            label="加权预测签约额"
            value={fenToWan(summary.weighted)}
            suffix="万元"
            icon={<FundOutlined />}
            tone="purple"
            hint="加权预测 = Σ（当前金额 × 该环节成交概率），按本人名下商机逐条相加"
          />
        </Col>
        <Col xs={12} md={8} xl={5}>
          <KpiCard
            label="已签约金额"
            value={fenToWan(summary.signed)}
            suffix="万元"
            icon={<RiseOutlined />}
            tone="green"
            hint="状态为「已签约」的商机合同金额之和（不是预算金额）"
          />
        </Col>
        <Col xs={12} md={8} xl={4}>
          <KpiCard
            label="已回款金额"
            value={fenToWan(summary.paid)}
            suffix="万元"
            icon={<WalletOutlined />}
            tone="brand"
            hint="本人名下商机的已回款金额之和；回款率口径见 2.6 回款管理"
          />
        </Col>
      </Row>

      <Card
        size="small"
        title={
          <Space>
            <AimOutlined />
            <span>我的目标达成（{period}）</span>
          </Space>
        }
        extra={
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            达成率 = 实际 ÷ 目标
          </Typography.Text>
        }
      >
        {!targetRows.length ? (
          achLoading ? (
            <NextStepGuide compact title="加载中…" description="正在读取本期目标与达成数据。" />
          ) : (
            <NextStepGuide
              title={`${period} 尚未设定目标，因此没有达成率`}
              description={`达成率 = 实际 ÷ 目标；没有目标就没有分母，系统不会凭空生成一个百分比。目标由管理者在「市场目标设定」里按「人 × 月 × 指标」设定；也可以在上方切换月份查看往期目标。`}
              actions={
                <Space wrap>
                  <Button type="primary" onClick={() => navigate('/targets')}>
                    去市场目标设定
                  </Button>
                  <Button onClick={() => navigate('/me/achievement')}>查看达成明细</Button>
                </Space>
              }
            />
          )
        ) : (
          <Row gutter={[12, 12]}>
            {targetRows.map((m) => (
              <Col xs={24} md={12} xl={8} key={m.metric}>
                {m.hasTarget ? (
                  <KpiCard
                    label={
                      <Tooltip title="达成率 = 实际值 ÷ 目标值；有效率按 0–1 小数比较">
                        <span>{m.label ?? METRIC_LABEL[m.metric] ?? m.metric}</span>
                      </Tooltip>
                    }
                    value={renderMetric(m.metric, m.actual)}
                    total={`/ 目标 ${renderMetric(m.metric, m.target)}`}
                    tone={(m.rate ?? 0) >= 1 ? 'green' : 'brand'}
                    progress={Math.min((m.rate ?? 0) * 100, 100)}
                    rateText={`达成 ${fmtPercent(m.rate)}`}
                    hint="实际值来自系统实时统计（不在本页手填）；达成率 = 实际 ÷ 目标。目标在「市场目标设定」里按人 × 月设定，改目标后本页即时重算"
                  />
                ) : (
                  <div
                    style={{
                      border: '1px dashed #D0D5DD',
                      borderRadius: 8,
                      padding: '12px 14px',
                      background: '#FCFCFD',
                      height: '100%',
                    }}
                  >
                    <Typography.Text strong>
                      {m.label ?? METRIC_LABEL[m.metric] ?? m.metric}
                    </Typography.Text>
                    <div style={{ fontSize: 13, color: '#667085', marginTop: 6, lineHeight: 1.7 }}>
                      本期该项<b>未设目标</b>（目标 0），所以没有达成率可算；实际值当前为{' '}
                      {renderMetric(m.metric, m.actual)}。
                      <br />
                      要看到达成率，请让管理者在「市场目标设定」里为本月设定该指标目标。
                    </div>
                    <Button size="small" style={{ marginTop: 8 }} onClick={() => navigate('/targets')}>
                      去设定目标
                    </Button>
                  </div>
                )}
              </Col>
            ))}
          </Row>
        )}
        {overallRate !== null && (
          <div style={{ marginTop: 10 }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              ⓘ 本期只有 1 个目标指标，总达成率即该指标达成率：{fmtPercent(overallRate)}（
              实际 ÷ 目标）。多个指标时不做加权平均，避免口径误读。
            </Typography.Text>
          </div>
        )}
      </Card>

      <Card size="small" title="我的商机环节分布">
        <Table
          rowKey="stage"
          size="small"
          loading={oppLoading}
          dataSource={summary.stages}
          pagination={false}
          locale={{
            emptyText: (
              <NextStepGuide
                compact
                title="暂无商机数据"
                description="本人名下还没有进入闭环二的商机。先去线索池把线索验证为有效商机，或在商机列表里查看。"
                actions={
                  <Button size="small" type="primary" onClick={() => navigate('/leads')}>
                    去线索池
                  </Button>
                }
              />
            ),
          }}
          columns={[
            {
              title: '环节',
              dataIndex: 'label',
              render: (v: string, r) =>
                r.stage === 'unknown' ? v : <a onClick={() => navigate('/opportunities')}>{v}</a>,
            },
            { title: '商机数', dataIndex: 'count', width: 100, align: 'right' },
            {
              title: '预算金额（万元）',
              dataIndex: 'amount',
              width: 150,
              align: 'right',
              render: (v: number) => fenToWan(v),
            },
            {
              title: '加权预测（万元）',
              dataIndex: 'weighted',
              width: 150,
              align: 'right',
              render: (v: number) => fenToWan(v),
            },
            {
              title: '占比',
              width: 160,
              render: (_, r) => (
                <Progress
                  percent={summary.total ? Math.round((r.count / summary.total) * 100) : 0}
                  size="small"
                />
              ),
            },
          ]}
        />
        <div style={{ marginTop: 8 }}>
          <Tag color="blue">默认按「环节停留最久优先」在 2.1 商机列表查看明细</Tag>
        </div>
      </Card>

      <Footnote>
        数据口径：有效商机数 = 本人名下状态为「有效商机」的商机数；加权预测 = Σ（当前金额 × 该环节成交概率）；
        已签约金额 = 状态为「已签约」的合同金额之和；已回款金额 = Σ 已回款金额。
        <b>达成率 = 实际值 ÷ 目标值</b>（有效率按 0–1 小数比较）；目标由管理者在「1.8 市场目标设定」里按
        「人 × 月 × 指标」设定，<b>没有目标就没有达成率</b>（本页不会伪造）。
        多个目标指标时不做加权平均，逐个指标各看各的达成率。若需比较两个期间的转化率差异，请用百分点表述
        （如「较上期 +3.2 个百分点」）。
      </Footnote>
      <NotForAssessment />
    </Space>
  );
}
