import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
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
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  AlertOutlined,
  CheckCircleOutlined,
  FileTextOutlined,
  ReloadOutlined,
  RiseOutlined,
} from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';
import { fetchLeadBoard } from '@/api/dashboard';
import { fmtHours, fmtPercent } from '@/utils/format';
import { LeadStatusTag } from '@/components/tags';
import SimpleBar from '@/components/SimpleBar';
import PageHeader from '@/components/PageHeader';
import KpiCard from '@/components/KpiCard';
import { Footnote, NextStepGuide } from '@/components/Guide';

const { RangePicker } = DatePicker;

/**
 * 4.2 线索看板：来源分布与有效率、状态漏斗、人均产出、跟进时效、趋势。
 *
 * 判据：C2（空态给"下一步 + 去哪做"）、C3（口径写在 KpiCard hint 与页脚 Footnote）。
 */
export default function LeadBoard() {
  const navigate = useNavigate();
  const [range, setRange] = useState<[Dayjs, Dayjs]>([dayjs().subtract(29, 'day'), dayjs()]);

  const start = range[0].format('YYYY-MM-DD');
  const end = range[1].format('YYYY-MM-DD');

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['lead-board', start, end],
    queryFn: () => fetchLeadBoard({ start, end }),
  });

  const timelinessColumns: ColumnsType<{ label: string; value: string }> = [
    { title: '指标', dataIndex: 'label' },
    { title: '数值', dataIndex: 'value', align: 'right' },
  ];

  const timeliness = data?.follow_timeliness;

  /** 总体有效率：所有来源的有效数 ÷ 线索数（与逐行口径一致） */
  const totalLeads = (data?.source_distribution ?? []).reduce((a, s) => a + (s.count || 0), 0);
  const totalValid = (data?.source_distribution ?? []).reduce((a, s) => a + (s.valid_count || 0), 0);
  const overallValidRate = totalLeads ? totalValid / totalLeads : 0;

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {/* 判据 C2：一句话说清"这一页先干什么" */}
      <PageHeader
        title="4.2 线索看板"
        subtitle="先看有效率和跟进时效：来源哪个划算、谁没在 1 个工作日内跟进；点击下方表格可回线索列表处理具体线索。"
        demo
        meta={[
          { label: '统计区间', value: `${start} ~ ${end}` },
          { label: '线索数', value: totalLeads },
          { label: '有效商机', value: totalValid },
        ]}
        actions={
          <>
            <RangePicker
              value={range}
              allowClear={false}
              onChange={(v) => v && v[0] && v[1] && setRange([v[0], v[1]])}
            />
            <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
              刷新
            </Button>
            <Button onClick={() => navigate('/leads')}>去 1.1 线索列表</Button>
          </>
        }
      />

      {/* 判据 C3：指标卡就地给出口径 */}
      <Card size="small" title="跟进时效与总体有效率">
        <Row gutter={[12, 12]}>
          <Col xs={12} md={6}>
            <KpiCard
              label="平均首次跟进时长"
              value={fmtHours(timeliness?.avg_first_follow_hours)}
              icon={<RiseOutlined />}
              tone="brand"
              hint="口径：已发生首次跟进的线索，从「指派时间 → 首次跟进时间」的自然小时数平均值；未跟进的线索不计入。"
            />
          </Col>
          <Col xs={12} md={6}>
            <KpiCard
              label="按时跟进率（1 个工作日内）"
              value={timeliness ? fmtPercent(timeliness.on_time_rate) : '-'}
              icon={<CheckCircleOutlined />}
              tone="green"
              progress={timeliness ? Math.round((timeliness.on_time_rate || 0) * 100) : undefined}
              hint="口径：首次跟进按工作日计时 —— 截止时刻 = 指派时间 + 1 个工作日（按节假日 / 调休日历推算）；按时率 = 在截止时刻前完成首次跟进的线索数 ÷ 已完成首次跟进的线索数。"
            />
          </Col>
          <Col xs={12} md={6}>
            <KpiCard
              label="超时未跟进数"
              value={timeliness?.overdue_count ?? 0}
              icon={<AlertOutlined />}
              tone={(timeliness?.overdue_count ?? 0) > 0 ? 'red' : 'gray'}
              danger={(timeliness?.overdue_count ?? 0) > 0}
              hint="口径：已过截止时刻（指派 + 1 个工作日）却还没有首次跟进记录的线索数；这是需要立刻认领处理的清单。"
            />
          </Col>
          <Col xs={12} md={6}>
            <KpiCard
              label="总体有效率"
              value={fmtPercent(overallValidRate)}
              icon={<FileTextOutlined />}
              tone="purple"
              progress={Math.round(overallValidRate * 100)}
              rateText={`${totalValid} / ${totalLeads} 条`}
              hint="口径：有效率 = 有效商机 ÷ 线索总数（区间内创建 / 归属该区间的线索），与「1.1 线索列表」的有效判定一致。"
            />
          </Col>
        </Row>
      </Card>

      <Row gutter={12}>
        <Col xs={24} lg={12}>
          <Card
            size="small"
            title="来源分布与有效率"
            extra={
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                按来源看「线索数 → 有效商机数」的转化
              </Typography.Text>
            }
          >
            {!data?.source_distribution?.length ? (
              <NextStepGuide
                title="暂无来源分布数据"
                description="该区间内还没有线索，或线索未填写来源。先到「1.1 线索列表」录入 / 「1.4 Excel 导入」批量导入，来源字段会决定这里的分布。"
                actions={
                  <Space>
                    <Button type="primary" onClick={() => navigate('/leads/new')}>
                      去 1.3 新增线索
                    </Button>
                    <Button onClick={() => navigate('/imports')}>去 1.4 Excel 导入</Button>
                  </Space>
                }
              />
            ) : (
              <Table
                rowKey="source"
                size="small"
                pagination={false}
                loading={isLoading}
                dataSource={data.source_distribution}
                columns={[
                  { title: '来源', dataIndex: 'label' },
                  { title: '线索数', dataIndex: 'count', width: 90, align: 'right' },
                  { title: '有效数', dataIndex: 'valid_count', width: 90, align: 'right' },
                  {
                    title: '有效率',
                    dataIndex: 'valid_rate',
                    width: 150,
                    render: (v: number) => (
                      <Progress percent={Math.round((v || 0) * 100)} size="small" format={() => fmtPercent(v)} />
                    ),
                  },
                ]}
              />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="状态漏斗">
            <SimpleBar
              items={(data?.status_funnel ?? []).map((s) => ({
                label: s.label,
                value: s.count,
              }))}
              color="#52c41a"
              emptyText="暂无状态分布：线索录入后按状态归集（未指派 / 已指派 / 跟进中 / 有效 / 无效 / 丢单 / 签约）"
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={12}>
        <Col xs={24} lg={12}>
          <Card size="small" title="人均产出">
            <Table
              rowKey="user_id"
              size="small"
              pagination={false}
              loading={isLoading}
              dataSource={data?.per_person ?? []}
              locale={{
                emptyText: (
                  <NextStepGuide
                    compact
                    title="暂无人均产出数据"
                    description="该区间内没有人有新增线索。可能是线索尚未指派，或当前账号的数据范围不包含团队成员。"
                    actions={
                      <Space>
                        <Button size="small" onClick={() => navigate('/assign')}>
                          去 1.6 分配管理
                        </Button>
                        <Button size="small" onClick={() => navigate('/leads')}>
                          去 1.1 线索列表
                        </Button>
                      </Space>
                    }
                  />
                ),
              }}
              columns={[
                { title: '成员', dataIndex: 'user_name', width: 130 },
                { title: '新增线索', dataIndex: 'new_lead_count', width: 100, align: 'right' },
                { title: '有效商机', dataIndex: 'valid_count', width: 100, align: 'right' },
                {
                  title: '有效率',
                  dataIndex: 'valid_rate',
                  render: (v: number) => (
                    <Progress percent={Math.round((v || 0) * 100)} size="small" format={() => fmtPercent(v)} />
                  ),
                },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="跟进时效">
            <Table
              rowKey="label"
              size="small"
              pagination={false}
              columns={timelinessColumns}
              dataSource={[
                { label: '平均首次跟进时长', value: fmtHours(timeliness?.avg_first_follow_hours) },
                { label: '按时跟进率（1 个工作日内）', value: fmtPercent(timeliness?.on_time_rate) },
                { label: '超时未跟进数量', value: String(timeliness?.overdue_count ?? 0) },
              ]}
            />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              首次跟进按工作日计时：截止时刻 = 指派时间 + 1 个工作日（按节假日 / 调休日历推算），
              不是自然 24 小时。
            </Typography.Text>
          </Card>
        </Col>
      </Row>

      <Card size="small" title="新增 / 有效趋势">
        {!data?.trend?.length ? (
          <NextStepGuide
            title="暂无趋势数据"
            description="该区间内没有线索新增或转有效记录；换个时间范围，或先去「1.1 线索列表」录入线索。"
            actions={
              <Space>
                <Button type="primary" onClick={() => navigate('/leads/new')}>
                  去 1.3 新增线索
                </Button>
                <Button
                  onClick={() => setRange([dayjs().subtract(89, 'day'), dayjs()])}
                >
                  看近 90 天
                </Button>
              </Space>
            }
          />
        ) : (
          <>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              条形长度代表当日新增线索数；「有效」为当日转有效商机数。
            </Typography.Text>
            <Row gutter={12} style={{ marginTop: 8 }}>
              <Col xs={24} lg={12}>
                <SimpleBar
                  items={data.trend.map((t) => ({ label: t.date, value: t.new_count }))}
                  color="#1677ff"
                  emptyText="暂无新增线索"
                />
              </Col>
              <Col xs={24} lg={12}>
                <SimpleBar
                  items={data.trend.map((t) => ({ label: t.date, value: t.valid_count }))}
                  color="#52c41a"
                  emptyText="暂无转有效记录"
                />
              </Col>
            </Row>
          </>
        )}
      </Card>

      <Card size="small" title="状态说明">
        <Space wrap>
          {['unassigned', 'assigned', 'following', 'valid', 'invalid', 'lost', 'signed'].map((s) => (
            <LeadStatusTag key={s} status={s} />
          ))}
          <Tag color="default">状态口径与线索列表一致</Tag>
        </Space>
      </Card>

      {/* 判据 C3：口径写在页脚，不只在文档里 */}
      <Card size="small">
        <Footnote>
          口径：① 有效率 = 有效商机 ÷ 线索总数（区间内线索，与「1.1 线索列表」的有效判定一致）；
          ② 首次跟进按工作日计时 —— 截止时刻 = 指派时间 + 1 个工作日（按节假日 / 调休日历推算）；
          ③ 按时跟进率 = 截止时刻前完成首次跟进的线索数 ÷ 已完成首次跟进的线索数；
          ④ 平均首次跟进时长只统计已发生首次跟进的线索（自然小时）；⑤ 超时未跟进数是需立刻处理的清单。
          统计区间 {start} ~ {end}。
        </Footnote>
        <Footnote style={{ marginTop: 6 }}>
          边界：本页用于线索过程管理，指标只用于发现堵点与辅导，不作为个人考核依据。
        </Footnote>
      </Card>
    </Space>
  );
}
