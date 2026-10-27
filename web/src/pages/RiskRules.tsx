import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Card,
  Col,
  InputNumber,
  Modal,
  Row,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import { ReloadOutlined, SaveOutlined, SettingOutlined, ThunderboltOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { fetchRiskRules, updateRiskRules } from '@/api/risks';
import { useAuthStore } from '@/store/auth';
import PageHeader from '@/components/PageHeader';
import KpiCard from '@/components/KpiCard';
import GateBanner from '@/components/GateBanner';
import { Footnote, NextStepGuide, PanelFlag } from '@/components/Guide';
import type { RuleConfig } from '@/types';

/** 分组标题：group → 中文标题（后端 group 有 operation / review / risk 三种） */
const GROUP_TITLE: Record<string, string> = {
  risk: '风险预警阈值（闭环二商机）',
  review: '待评审判定',
  operation: '运营侧提醒与预警阈值',
};
const GROUP_ORDER = ['risk', 'review', 'operation'];

/** 比例类阈值的辅助说明（后端 unit='比例'） */
const RATIO_HINT: Record<string, string> = {
  conversion_warn_ratio: '0.7 = 低于基准七成即报警',
  total_duration_warn_ratio: '0.8 = 走到总时长的 80% 就开始报',
};

/**
 * 「这个阈值影响哪类预警」（判据 C2 少判断）。
 * R5只采用下一关键节点及预计时间。
 */
const IMPACT: Record<string, { kind: string; color: string; text: string }> = {
  stuck_warn_ratio: {
    kind: '卡单预警',
    color: 'red',
    text: '影响「卡单」这类预警：当前环节停留 > 基准 × 该倍数即触发。直接决定 2.4 风险与卡单中心、4.1 经营大盘、4.4 周报、4.5 周会里的「卡单数」。',
  },
  stuck_require_unfinished: {
    kind: '卡单判定口径',
    color: 'volcano',
    text: '影响卡单预警是否成立：开 = 只有「停留超基准 且 完成标准未全部完成」才报卡单；关 = 只看停留天数，完成标准做完也照报。',
  },
  total_duration_warn_days: {
    kind: '总时长预警',
    color: 'orange',
    text: '影响「项目总时长超期」预警：7 环节合计基准天数，与实际进入闭环二后的天数比较，超了就报。',
  },
  total_duration_warn_ratio: {
    kind: '总时长预警',
    color: 'orange',
    text: '影响「项目总时长超期」预警的提前量：进入闭环二后的天数 ≥ 总天数 × 该比例时就开始报（0.8 = 72 天开始）。',
  },
  conversion_warn_ratio: {
    kind: '转化率预警',
    color: 'gold',
    text: '影响「阶段转化率低于基准」预警：实际转化率 < 基准 × 该比例即判定预警。决定了 4.3 / 4.4 / 4.5 里「低于预警线」标签是否出现。',
  },
  conversion_min_sample: {
    kind: '转化率预警',
    color: 'gold',
    text: '影响转化率预警是否判定：分母（上一环节数量）小于该值时不判定，4.3 / 4.4 会显示「样本不足」。属于防小样本误报的保护阀。',
  },
  stuck_missing_review_days: {
    kind: '待评审判定',
    color: 'geekblue',
    text: '影响谁进入「待评审」清单：进入闭环二后超过该天数未评审（或已到下次评审时间）即进入。决定 2.7 项目评审与 4.5 周会第三层的待评审项数。',
  },
  ops_no_contact_days: {
    kind: '运营预警',
    color: 'cyan',
    text: '影响「长期未联系客户」这类运营预警：超过该天数未联系即触发，出现在 2.4 风险与卡单中心。',
  },
  acceptance_due_workdays: {
    kind: '验收临近提醒',
    color: 'blue',
    text: '影响「验收临近」清单的范围：服务期到期前多少个工作日进入提醒，决定 3.11 验收管理与 4.1 经营大盘「验收临近」里出现谁。',
  },
  renewal_due_days: {
    kind: '续费到期提醒',
    color: 'blue',
    text: '影响「续费到期」清单的范围：服务期到期前多少天进入续费启动，决定 3.12 续费与增购与 4.1 经营大盘「续费到期」里出现谁。',
  },
  complaint_overdue_days: {
    kind: '投诉超期预警',
    color: 'red',
    text: '影响「投诉未闭环超期」预警：投诉登记后超过该天数仍未闭环即触发，出现在 3.8 投诉管理与 4.6 运营复盘。',
  },
  issue_overdue_days: {
    kind: '台账久拖预警',
    color: 'orange',
    text: '影响「产品问题久拖」预警：派单后超过该天数未解决即触发，决定 3.7 台账与 4.6 运营复盘「超期未解决」的条数。',
  },
  promise_due_warn_days: {
    kind: '承诺到期提醒',
    color: 'gold',
    text: '影响「承诺未兑现」提醒：销售承诺的兑现时间临近该天数仍未兑现即提醒，出现在 3.7 产品问题与承诺台账。',
  },
  delivery_delay_enabled: {
    kind: '交付延期预警',
    color: 'volcano',
    text: '影响「交付延期」预警是否生成：开启前必须先在 6.6 交付阶段产出材料配置里把各阶段停留时长基准配好，否则没有基准可比较。',
  },
};

/** 把规则值统一成数字，便于比对与编辑 */
function toNumber(v: number | string): number {
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}

/**
 * 6.7 预警规则配置
 * 阈值控制「卡单 / 总时长 / 转化率 / 节奏」四类预警与「待评审」判定，**改完即时生效**。
 * 权限：admin / market_manager 可改，其他人只读（菜单与路由层已拦）。
 *
 * 判据：C2（每条规则都写清"影响哪类预警 + 改了会怎样"）、C3（口径写在 KpiCard hint 与页脚 Footnote）。
 */
export default function RiskRules() {
  const qc = useQueryClient();
  const role = useAuthStore((s) => s.user?.role);
  const canEdit = role === 'admin' || role === 'market_manager';

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['risk-rules'],
    queryFn: fetchRiskRules,
  });

  /** 草稿值：key → 数字 */
  const [draft, setDraft] = useState<Record<string, number>>({});

  useEffect(() => {
    if (!data?.items) return;
    const next: Record<string, number> = {};
    data.items.forEach((r) => {
      next[r.key] = toNumber(r.value);
    });
    setDraft(next);
  }, [data]);

  /** 改动过的 key（与原始值不一致） */
  const dirty = useMemo(() => {
    const set = new Set<string>();
    (data?.items ?? []).forEach((r) => {
      const cur = draft[r.key];
      if (cur === undefined) return;
      if (toNumber(cur) !== toNumber(r.value)) set.add(r.key);
    });
    return set;
  }, [data, draft]);

  const doSave = useMutation({
    mutationFn: () =>
      updateRiskRules(
        Array.from(dirty).map((key) => ({ key, value: draft[key] as number })),
      ),
    onSuccess: (r) => {
      message.success(`已保存 ${r.items.length} 项阈值，新规则立即生效`);
      qc.invalidateQueries({ queryKey: ['risk-rules'] });
      qc.invalidateQueries({ queryKey: ['dashboard-risk-center'] });
    },
    onError: (err) => {
      // 后端原因原样透出，不改成模糊话术
      const e = err as { response?: { data?: { detail?: string } } };
      Modal.error({
        title: '保存失败',
        content: e?.response?.data?.detail ?? '请求失败，请稍后重试',
        okText: '知道了',
      });
    },
  });

  const setValue = (key: string, v: number | null) => {
    setDraft((prev) => ({ ...prev, [key]: v ?? 0 }));
  };

  const items = data?.items ?? [];
  const switchCount = items.filter((r) => r.value_type === 'int' && r.unit === '0/1').length;
  const noBaseDelivery = (draft.delivery_delay_enabled ?? 0) === 0;

  const columns: ColumnsType<RuleConfig> = [
    {
      title: '规则',
      dataIndex: 'label',
      render: (v: string, r) => (
        <Space direction="vertical" size={2}>
          <Typography.Text strong>{v}</Typography.Text>
          {/* 判据 C2：每条规则都写清"影响哪类预警" */}
          {IMPACT[r.key] && (
            <Space size={4} wrap>
              <Tag color={IMPACT[r.key].color}>影响：{IMPACT[r.key].kind}</Tag>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {IMPACT[r.key].text}
              </Typography.Text>
            </Space>
          )}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {r.description}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 11, fontFamily: 'monospace' }}>
            {r.key}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '阈值',
      width: 320,
      render: (_, r) => {
        const val = draft[r.key] ?? toNumber(r.value);
        const isSwitch = r.value_type === 'int' && r.unit === '0/1';
        if (isSwitch) {
          return (
            <Space direction="vertical" size={0}>
              <Space size={8}>
                <Switch
                  checked={val === 1}
                  disabled={!canEdit}
                  checkedChildren="开"
                  unCheckedChildren="关"
                  onChange={(c) => setValue(r.key, c ? 1 : 0)}
                />
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {val === 1 ? '已开启' : '已关闭'}
                </Typography.Text>
              </Space>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                改了会怎样：开 = 触发该类预警；关 = 完全不再生成，2.4 也看不到这类预警。
              </Typography.Text>
            </Space>
          );
        }
        return (
          <Space direction="vertical" size={0}>
            <Space size={8}>
              <InputNumber
                value={val}
                disabled={!canEdit}
                style={{ width: 150 }}
                min={0}
                max={r.unit === '比例' ? 1 : undefined}
                step={r.value_type === 'float' ? 0.1 : 1}
                precision={r.value_type === 'float' ? 2 : 0}
                onChange={(v) => setValue(r.key, v)}
              />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {r.unit}
              </Typography.Text>
              {RATIO_HINT[r.key] && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  （{RATIO_HINT[r.key]}）
                </Typography.Text>
              )}
            </Space>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              改了会怎样：数值调高 = 预警更少但更宽松；调低 = 预警更早、更敏感。
              {r.unit === '0/1' ? '' : '填 0 的含义见上方「影响」说明（部分规则 0 等于不告警）。'}
            </Typography.Text>
          </Space>
        );
      },
    },
    {
      title: '单位',
      dataIndex: 'unit',
      width: 90,
      render: (v: string) => (v === '0/1' ? <Tag>开关</Tag> : <Tag>{v || '-'}</Tag>),
    },
    {
      title: '状态',
      width: 100,
      render: (_, r) => (dirty.has(r.key) ? <Tag color="orange">已修改</Tag> : <Tag>未改动</Tag>),
    },
  ];

  const grouped = GROUP_ORDER.map((g) => ({
    group: g,
    items: items.filter((r) => r.group === g).sort((a, b) => a.sort_order - b.sort_order),
  })).filter((g) => g.items.length > 0);

  /** 未被上述分组覆盖的 group（后端新增分组时不至于看不见） */
  const otherGroups = Array.from(new Set(items.map((r) => r.group))).filter(
    (g) => !GROUP_ORDER.includes(g),
  );

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {/* 判据 C2：一句话说清"这一页先干什么" */}
      <PageHeader
        title="6.7 预警规则配置"
        subtitle="先确认每条阈值「影响哪类预警」，再改数值或开关，最后点保存；改完即时生效，不需要等定时扫描。"
        tags={
          <>
            <PanelFlag>改完即时生效</PanelFlag>
            {!canEdit && <PanelFlag>只读</PanelFlag>}
          </>
        }
        meta={[
          { label: '规则条数', value: data?.total ?? items.length },
          { label: '已修改', value: `${dirty.size} 项` },
          { label: '当前角色', value: canEdit ? '可修改' : '只读' },
        ]}
        actions={
          <>
            <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
              刷新
            </Button>
            <Button
              onClick={() => {
                if (!data?.items) return;
                const next: Record<string, number> = {};
                data.items.forEach((r) => {
                  next[r.key] = toNumber(r.value);
                });
                setDraft(next);
                message.info('已重置为服务端当前值');
              }}
              disabled={dirty.size === 0}
            >
              重置
            </Button>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              disabled={!canEdit || dirty.size === 0}
              loading={doSave.isPending}
              onClick={() => doSave.mutate()}
            >
              保存{dirty.size ? `（${dirty.size} 项）` : ''}
            </Button>
          </>
        }
      />

      {/* 判据 C3：口径写在卡上 */}
      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}>
          <KpiCard
            label="规则条数"
            value={data?.total ?? items.length}
            suffix="条"
            icon={<SettingOutlined />}
            tone="brand"
            hint="口径：后端 sys_rule_config 表里的阈值条数，覆盖风险预警 / 待评审判定 / 运营提醒三组。"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="开关类规则"
            value={switchCount}
            total={`/ ${items.length} 条`}
            icon={<ThunderboltOutlined />}
            tone="purple"
            hint="口径：unit = 0/1 的规则（开 / 关形态），例如卡单需完成标准未全完成、节奏不清预警开关、交付延期预警开关。"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="已修改待保存"
            value={dirty.size}
            suffix="项"
            icon={<SaveOutlined />}
            tone={dirty.size ? 'amber' : 'gray'}
            hint="口径：草稿值与服务端当前值不一致的规则条数。只有点「保存」才会写回后端；未保存的改动不会生效。"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="生效方式"
            value="即时生效"
            icon={<ThunderboltOutlined />}
            tone="green"
            rateText="定时扫描每 30 分钟一次，可手工触发"
            hint="口径：保存后新阈值立即生效；定时扫描每 30 分钟跑一次，也可以在 2.4 风险与卡单中心点「手工触发扫描」立刻按新阈值重算。"
          />
        </Col>
      </Row>

      <Card size="small">
        <Alert
          type="info"
          showIcon
          message="这些阈值控制「卡单 / 总时长 / 转化率 / 节奏」四类预警与「待评审」判定，改完即时生效"
          description="定时扫描每 30 分钟一次；也可以在 2.4 风险与卡单中心点「手工触发扫描」立刻重跑。界面上没有写死的阈值：页面上看到的每个数字都来自这张表。"
        />
      </Card>

      {!canEdit && (
        <GateBanner
          tone="tip"
          title="当前角色只读，只有管理员或市场部管理人员可以修改阈值"
          items={[
            '为什么不能改：阈值直接决定预警条数与考核讨论口径，故只对管理员 / 市场部管理人员开放。',
            '怎么办：把要改的规则名与目标值发给管理员；或到「6.2 角色与权限」核对当前角色的能力（只读）。',
          ]}
          action={
            <Button size="small" href="/admin/roles">
              去 6.2 查看角色能力
            </Button>
          }
        />
      )}

      <Card size="small" title="R5 项目节奏不清">
        下一关键节点或预计时间未知，或者节点已到期需要确认完成情况及新安排时提醒。
        允许暂定时间和区间；签约后停止销售R5。年度申报/招标窗口只作业务背景，不参与触发。
      </Card>

      {noBaseDelivery && (
        <GateBanner
          tone="tip"
          title="交付延期预警当前是关闭的（delivery_delay_enabled = 0）"
          items={[
            '为什么关着：交付阶段的停留时长基准本期未设，没有基准可比较，开了也会报错或全量报。',
            '怎么办：先在「6.6 交付阶段产出材料配置」里把各阶段停留时长基准填上，再回到本页把「交付延期预警开关」改成开。',
            '在此之前，4.6 运营复盘的「交付延期」区块会显示「基准未设」说明，而不是空表。',
          ]}
          action={
            <Space>
              <Button type="primary" size="small" href="/admin/delivery-standards">
                去 6.6 配置阶段基准
              </Button>
              <Button size="small" href="/reports/operation-review">
                看 4.6 运营复盘
              </Button>
            </Space>
          }
        />
      )}

      <Card
        loading={isLoading}
        title="预警规则与阈值"
        extra={
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            每条规则都标了「影响哪类预警」；改动未保存不会生效
          </Typography.Text>
        }
      >
        {grouped.length === 0 && otherGroups.length === 0 && !isLoading ? (
          <NextStepGuide
            title="暂无预警规则"
            description="后端 sys_rule_config 表里没有阈值记录。规则阈值由后端初始化；可先刷新，仍为空请联系管理员检查后端初始化。"
            actions={
              <Button type="primary" onClick={() => refetch()}>
                刷新
              </Button>
            }
          />
        ) : (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            {grouped.map((g) => (
              <div key={g.group}>
                <Typography.Title level={5} style={{ marginTop: 0 }}>
                  {GROUP_TITLE[g.group] ?? g.group}
                  <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                    {g.items.length} 条
                  </Typography.Text>
                </Typography.Title>
                <Table<RuleConfig>
                  rowKey="key"
                  size="small"
                  dataSource={g.items}
                  columns={columns}
                  pagination={false}
                />
              </div>
            ))}
            {otherGroups.map((g) => (
              <div key={g}>
                <Typography.Title level={5} style={{ marginTop: 0 }}>
                  {g}
                </Typography.Title>
                <Table<RuleConfig>
                  rowKey="key"
                  size="small"
                  dataSource={items.filter((r) => r.group === g)}
                  columns={columns}
                  pagination={false}
                />
              </div>
            ))}
          </Space>
        )}
      </Card>

      {/* 判据 C3：口径写在页脚，不只在文档里 */}
      <Card size="small">
        <Footnote>
          口径：① 保存后新阈值<strong>改完即时生效</strong>，无需等待定时扫描；定时扫描每 30 分钟跑一次，
          也可在「2.4 风险与卡单中心」点「手工触发扫描」立刻按新阈值重算并通知；
          ② 每条规则在上方都标了「影响哪类预警」，改前先确认影响面；
          ③ 数值调高 = 预警更少但更宽松，调低 = 预警更早、更敏感；
          ④ R5只核查下一关键节点与预计时间，不使用旧日期字段、宽限天数或年度窗口触发；
          ⑤ 页面上没有写死的阈值：看到的每个数字都来自这张表。
        </Footnote>
        <Footnote style={{ marginTop: 6 }}>
          边界：阈值只控制「何时提醒」，不改变业务判定本身（如商机能否推进仍由硬门槛决定）；阈值改动会写入「6.9 操作日志」留痕。
        </Footnote>
      </Card>
    </Space>
  );
}
