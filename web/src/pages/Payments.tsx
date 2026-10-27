import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  BankOutlined,
  CheckCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  WalletOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import dayjs, { type Dayjs } from 'dayjs';
import { fetchStaffOptions } from '@/api/dashboard';
import {
  createPayment,
  deletePayment,
  fetchPaymentSummary,
  listPayments,
  updatePayment,
} from '@/api/reports';
import { LeadStatusTag } from '@/components/tags';
import ContractSelect from '@/components/ContractSelect';
import KpiCard from '@/components/KpiCard';
import PageHeader from '@/components/PageHeader';
import { Footnote, NextStepGuide, NotForAssessment } from '@/components/Guide';
import { useDicts } from '@/hooks/useDicts';
import { useAuthStore } from '@/store/auth';
import {
  fenToWan,
  fenToYuanNumber,
  fmtDate,
  fmtDateTime,
  fmtPercent,
  formatYuan,
  yuanToFen,
} from '@/utils/format';
import type { ContractOut, PaymentRecord, PaymentUnpaidItem } from '@/types';

const PAGE_SIZE = 10;

interface PaymentFormValues {
  contract_id?: string;
  amount_yuan?: number;
  paid_date?: Dayjs;
  method?: string;
  period_no?: string;
  remark?: string;
}

/**
 * 2.6 回款管理 —— 对齐设计稿 06 的 KPI 骨架：
 * 4 个 KPI（已签约 / 已回款 / 回款率（带进度条）/ 未回款）· 回款记录（可改可删）· 未回款清单。
 * 登记表单可选合同（带出商机）、填金额（元，提交时换算为分）与到账日期。
 *
 * 判据：C1（合同带出客户与合同额，不用手敲）、C3（回款率口径写在卡片 hint 与页脚）。
 */
export default function Payments() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { dicts } = useDicts();
  const role = useAuthStore((s) => s.user?.role);
  const isManager = role === 'admin' || role === 'market_manager';

  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [ownerId, setOwnerId] = useState<string | undefined>(undefined);

  const params = {
    start: range?.[0]?.format('YYYY-MM-DD'),
    end: range?.[1]?.format('YYYY-MM-DD'),
    owner_id: isManager ? ownerId : undefined,
  };

  const summaryQuery = useQuery({
    queryKey: ['payments-summary', params],
    queryFn: () => fetchPaymentSummary(params),
  });
  const summary = summaryQuery.data;

  const [page, setPage] = useState(1);
  const recordsQuery = useQuery({
    queryKey: ['payments', { ...params, page }],
    queryFn: () => listPayments({ ...params, page, page_size: PAGE_SIZE }),
  });

  const { data: staff } = useQuery({
    queryKey: ['dashboard-staff-options'],
    queryFn: fetchStaffOptions,
    enabled: isManager,
    retry: false,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['payments'] });
    qc.invalidateQueries({ queryKey: ['payments-summary'] });
    qc.invalidateQueries({ queryKey: ['opportunities'] });
    qc.invalidateQueries({ queryKey: ['leads'] });
  };

  /* ---------------- 登记 / 编辑 ---------------- */
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<PaymentRecord | null>(null);
  const [pickedContract, setPickedContract] = useState<ContractOut | undefined>(undefined);
  const [form] = Form.useForm<PaymentFormValues>();
  const [amountYuan, setAmountYuan] = useState<number | undefined>(undefined);
  /** 从「未回款清单」点「登记回款」时的引导文案（少切换：直接进入登记） */
  const [prefillHint, setPrefillHint] = useState<string>('');

  const openCreate = () => {
    setEditing(null);
    setPickedContract(undefined);
    setAmountYuan(undefined);
    setPrefillHint('');
    form.resetFields();
    form.setFieldsValue({ paid_date: dayjs(), method: 'bank' });
    setFormOpen(true);
  };

  const openEdit = (r: PaymentRecord) => {
    setEditing(r);
    setPickedContract(undefined);
    setAmountYuan(fenToYuanNumber(r.amount_fen));
    setPrefillHint('');
    form.setFieldsValue({
      amount_yuan: fenToYuanNumber(r.amount_fen),
      paid_date: r.paid_date ? dayjs(r.paid_date) : dayjs(),
      method: r.method ?? 'bank',
      period_no: r.period_no ?? undefined,
      remark: r.remark ?? '',
    });
    setFormOpen(true);
  };

  const doSave = useMutation({
    mutationFn: (v: PaymentFormValues) => {
      const fen = yuanToFen(v.amount_yuan);
      const body = {
        amount_fen: fen as number,
        paid_date: v.paid_date?.format('YYYY-MM-DD'),
        method: v.method,
        period_no: v.period_no,
        remark: v.remark ?? '',
      };
      if (editing) return updatePayment(editing.id, body);
      return createPayment({ ...body, lead_id: pickedContract?.lead_id as string });
    },
    onSuccess: () => {
      message.success(editing ? '回款记录已修改' : '回款已登记，商机已回款金额同步重算');
      setFormOpen(false);
      setEditing(null);
      setPickedContract(undefined);
      setAmountYuan(undefined);
      setPrefillHint('');
      form.resetFields();
      invalidate();
    },
  });

  const doDelete = useMutation({
    mutationFn: (id: string) => deletePayment(id),
    onSuccess: () => {
      message.success('回款记录已删除，商机已回款金额已同步重算');
      invalidate();
    },
  });

  const submit = async () => {
    const v = await form.validateFields();
    const fen = yuanToFen(v.amount_yuan);
    if (fen === undefined || fen <= 0) {
      message.error('回款金额必须大于 0（单位：元）');
      return;
    }
    doSave.mutate(v);
  };

  /* ---------------- 表格列 ---------------- */
  const recordColumns: ColumnsType<PaymentRecord> = [
    {
      title: '客户',
      dataIndex: 'lead_name',
      width: 190,
      render: (v: string, r) => <a onClick={() => navigate(`/opportunities/${r.lead_id}`)}>{v}</a>,
    },
    {
      title: '回款金额(元)',
      dataIndex: 'amount_fen',
      width: 150,
      align: 'right',
      render: (v: number) => <Typography.Text strong>{formatYuan(v)}</Typography.Text>,
    },
    {
      title: '到账日期',
      dataIndex: 'paid_date',
      width: 120,
      render: (v: string | null) => fmtDate(v),
    },
    {
      title: '方式',
      dataIndex: 'method_label',
      width: 110,
      render: (v: string, r) => <Tag color="blue">{v || r.method || '-'}</Tag>,
    },
    {
      title: '期次',
      dataIndex: 'period_label',
      width: 100,
      render: (v: string, r) => <Tag>{v || r.period_no || '-'}</Tag>,
    },
    { title: '备注', dataIndex: 'remark', ellipsis: true, render: (v: string | null) => v || '-' },
    { title: '操作人', dataIndex: 'operator_name', width: 100, render: (v: string | null) => v || '-' },
    {
      title: '登记时间',
      dataIndex: 'created_at',
      width: 150,
      render: (v: string | null) => fmtDateTime(v),
    },
    {
      title: '操作',
      width: 120,
      fixed: 'right',
      render: (_, r) => (
        <Space size={4}>
          <Button type="link" size="small" onClick={() => openEdit(r)}>
            编辑
          </Button>
          <Popconfirm
            title="确认删除这笔回款记录？"
            description="删除后该商机的已回款金额会同步重算，操作不可撤销。"
            okText="删除"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            onConfirm={() => doDelete.mutate(r.id)}
          >
            <Button type="link" size="small" danger loading={doDelete.isPending}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const unpaidColumns: ColumnsType<PaymentUnpaidItem> = [
    {
      title: '客户',
      dataIndex: 'lead_name',
      width: 200,
      render: (v: string, r) => <a onClick={() => navigate(`/opportunities/${r.lead_id}`)}>{v}</a>,
    },
    { title: '负责人', dataIndex: 'owner_name', width: 100, render: (v: string | null) => v || '-' },
    {
      title: '合同额(万)',
      dataIndex: 'contract_amount_fen',
      width: 130,
      align: 'right',
      render: (v: number) => fenToWan(v),
    },
    {
      title: '已回款(万)',
      dataIndex: 'paid_amount_fen',
      width: 130,
      align: 'right',
      render: (v: number) => fenToWan(v),
    },
    {
      title: '未回款(万)',
      dataIndex: 'unpaid_amount_fen',
      width: 140,
      align: 'right',
      render: (v: number) => (
        <Typography.Text type="danger" strong>
          {fenToWan(v)}
        </Typography.Text>
      ),
    },
    {
      title: '签约日期',
      dataIndex: 'sign_date',
      width: 120,
      render: (v: string | null) => fmtDate(v),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (v: string) => <LeadStatusTag status={v} />,
    },
    {
      title: '操作',
      width: 110,
      fixed: 'right',
      render: (_, r) => (
        <Button
          type="link"
          size="small"
          onClick={() => {
            openCreate();
            setPrefillHint(`已按「${r.lead_name}」打开登记回款：请在「合同 / 客户」里搜索该客户并选中合同（合同带出商机）。`);
          }}
        >
          登记回款
        </Button>
      ),
    },
  ];

  const rate = summary?.collection_rate ?? 0;
  const ratePercent = Math.min(Math.max(Math.round(rate * 1000) / 10, 0), 100);

  const tabs = [
    {
      key: 'records',
      label: `回款记录（${recordsQuery.data?.total ?? 0}）`,
      children: (
        <Table<PaymentRecord>
          rowKey="id"
          size="small"
          loading={recordsQuery.isLoading}
          dataSource={recordsQuery.data?.items ?? []}
          columns={recordColumns}
          scroll={{ x: 1250 }}
          locale={{
            emptyText: (
              <NextStepGuide
                title="暂无回款记录"
                description="回款在这里登记：选合同（自动带出客户）→ 填金额（元）与到账日期 → 方式即可；登记后该商机的已回款金额会自动同步。"
                actions={
                  <Space wrap>
                    <Button type="primary" onClick={openCreate}>
                      登记回款
                    </Button>
                    <Button onClick={() => navigate('/opportunities')}>去 2.1 商机列表</Button>
                  </Space>
                }
              />
            ),
          }}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total: recordsQuery.data?.total ?? 0,
            showSizeChanger: false,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p) => setPage(p),
          }}
        />
      ),
    },
    {
      key: 'unpaid',
      label: `未回款清单（${summary?.unpaid_list?.length ?? 0}）`,
      children: (
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            口径：合同额 &gt; 0 且未回款 &gt; 0 的客户（按签约日期倒序）。点客户名称进入商机详情跟进；点「登记回款」直接开单。
          </Typography.Text>
          <Table<PaymentUnpaidItem>
            rowKey="lead_id"
            size="small"
            loading={summaryQuery.isLoading}
            dataSource={summary?.unpaid_list ?? []}
            columns={unpaidColumns}
            locale={{
              emptyText: (
                <NextStepGuide
                  compact
                  title="没有未回款客户"
                  description="口径：合同额 > 0 且未回款 > 0 的客户才会出现在这里；当前都已回清或还没签约。"
                />
              ),
            }}
            pagination={{ pageSize: PAGE_SIZE, showSizeChanger: false, showTotal: (t) => `共 ${t} 条` }}
          />
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <PageHeader
        title="回款管理"
        subtitle="先看未回款的，再对账——回款率口径见卡片与页脚；登记回款选合同即可带出客户，金额按元填、系统按分存。"
        demo
        actions={
          <Space wrap>
            <DatePicker.RangePicker
              value={range}
              onChange={(v) => {
                setPage(1);
                setRange(v as [Dayjs, Dayjs] | null);
              }}
              allowClear
              placeholder={['回款开始日期', '回款结束日期']}
            />
            {isManager && (
              <Select
                allowClear
                style={{ width: 160 }}
                placeholder="团队全部"
                value={ownerId}
                onChange={(v) => {
                  setPage(1);
                  setOwnerId(v ?? undefined);
                }}
                options={(staff?.items ?? []).map((s) => ({ value: s.user_id, label: s.name }))}
              />
            )}
            <Button
              icon={<ReloadOutlined />}
              loading={summaryQuery.isFetching || recordsQuery.isFetching}
              onClick={() => {
                summaryQuery.refetch();
                recordsQuery.refetch();
              }}
            >
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              登记回款
            </Button>
          </Space>
        }
      />

      {/* 设计稿 06：图标圆角块 + 标签 + 大号数字 + 单位（+ 回款率带进度条） */}
      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}>
          <KpiCard
            label="已签约"
            value={fenToWan(summary?.contracted_fen)}
            suffix="万元"
            icon={<BankOutlined />}
            tone="brand"
            hint={`已签约 ${summary?.signed_count ?? 0} 个客户的合同金额合计（状态为「已签约」的商机合同额）；是回款率的分母`}
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="已回款"
            value={fenToWan(summary?.paid_fen)}
            suffix="万元"
            icon={<CheckCircleOutlined />}
            tone="green"
            hint={`已回款金额合计（${formatYuan(summary?.paid_fen)} 元）；是回款率的分子。修改 / 删除回款记录后立即重算`}
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="回款率"
            value={fmtPercent(rate)}
            icon={<WalletOutlined />}
            tone="purple"
            progress={ratePercent}
            rateText={`本期回款 ${fenToWan(summary?.period_paid_fen)} 万 / ${summary?.period_count ?? 0} 笔`}
            hint="回款率 = 已回款 ÷ 已签约合同额（不是回款 ÷ 预算金额）；进度条按百分比画，上限 100%"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            label="未回款"
            value={fenToWan(summary?.unpaid_fen)}
            suffix="万元"
            icon={<WarningOutlined />}
            tone={(summary?.unpaid_fen ?? 0) > 0 ? 'red' : 'gray'}
            danger={(summary?.unpaid_fen ?? 0) > 0}
            hint={`未回款 = 已签约合同额 − 已回款；涉及 ${summary?.unpaid_list?.length ?? 0} 个客户（见「未回款清单」页签）`}
          />
        </Col>
      </Row>

      {!isManager && (
        <Alert
          type="info"
          showIcon
          message="你当前只能查看与登记本人名下商机的回款；管理人员可切换负责人查看团队数据。"
        />
      )}

      <Card>
        <Tabs items={tabs} />
      </Card>

      {/* 登记 / 编辑回款：选合同 → 金额（元）→ 到账日期 */}
      <Modal
        open={formOpen}
        width={600}
        title={editing ? `修改回款记录 · ${editing.lead_name}` : '登记回款'}
        okText={editing ? '保存修改' : '提交'}
        cancelText="取消"
        confirmLoading={doSave.isPending}
        onCancel={() => {
          setFormOpen(false);
          setEditing(null);
          setPickedContract(undefined);
          setAmountYuan(undefined);
          setPrefillHint('');
          form.resetFields();
        }}
        onOk={submit}
      >
        {prefillHint ? (
          <Alert type="info" showIcon style={{ marginBottom: 12 }} message={prefillHint} />
        ) : null}
        <Form form={form} layout="vertical">
          <Form.Item
            label="合同 / 客户"
            name="contract_id"
            rules={
              editing
                ? []
                : [{ required: true, message: '请选择合同（客户由合同带出，不用手敲）' }]
            }
            extra={
              editing
                ? '修改记录时不允许换客户：如需换客户请删除这条后重新登记。'
                : '按客户名称 / 合同编号搜索；选中后自动带出商机与合同额。查不到合同说明该客户还没签约，先去 2.1 商机列表推进到「合同签约」。'
            }
          >
            <ContractSelect
              disabled={!!editing}
              style={{ width: '100%' }}
              onPick={(c) => {
                setPickedContract(c);
                // 少填：金额留空，让用户按实际到账填；这里只提示合同额
              }}
            />
          </Form.Item>

          {pickedContract && (
            <Descriptions
              size="small"
              column={2}
              style={{ marginBottom: 12 }}
              items={[
                { key: 'no', label: '合同编号', children: pickedContract.contract_no },
                {
                  key: 'amount',
                  label: '合同金额',
                  children: `${fenToWan(pickedContract.amount_fen)} 万元`,
                },
                {
                  key: 'unpaid',
                  label: '该客户未回款',
                  children: `${fenToWan(
                    (summary?.unpaid_list ?? []).find((u) => u.lead_id === pickedContract.lead_id)
                      ?.unpaid_amount_fen ?? 0,
                  )} 万元`,
                },
                {
                  key: 'sign',
                  label: '签约日期',
                  children: fmtDate(pickedContract.sign_date),
                },
                {
                  key: 'paid',
                  label: '该客户已回款',
                  children: `${fenToWan(
                    (summary?.unpaid_list ?? []).find((u) => u.lead_id === pickedContract.lead_id)
                      ?.paid_amount_fen ?? 0,
                  )} 万元`,
                },
              ]}
            />
          )}

          <Form.Item
            label="回款金额（元）"
            name="amount_yuan"
            rules={[
              { required: true, message: '请输入回款金额' },
              {
                validator: (_, v) =>
                  v === undefined || v === null || Number(v) > 0
                    ? Promise.resolve()
                    : Promise.reject(new Error('回款金额必须大于 0')),
              },
            ]}
            extra={`按元填写，提交时换算为分（1 元 = 100 分）${
              amountYuan ? `；系统将按 ${formatYuan(yuanToFen(amountYuan))} 元入账` : ''
            }`}
          >
            <InputNumber
              min={0.01}
              precision={2}
              style={{ width: '100%' }}
              placeholder="如 200000（即 20 万元）"
              onChange={(v) => setAmountYuan(typeof v === 'number' ? v : undefined)}
            />
          </Form.Item>

          <Form.Item
            label="到账日期"
            name="paid_date"
            rules={[{ required: true, message: '请选择到账日期' }]}
            extra="到账日期用于统计「本期回款」，与合同签约日期无关。"
          >
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item
            label="回款方式"
            name="method"
            rules={[{ required: true, message: '请选择回款方式' }]}
          >
            <Select
              placeholder="选择回款方式"
              options={(dicts.payment_methods ?? []).map((d) => ({ value: d.value, label: d.label }))}
            />
          </Form.Item>

          <Form.Item label="期次" name="period_no" extra="首款 / 二期 / 尾款…，用于对账时分辨是哪一笔。">
            <Select
              allowClear
              placeholder="选择期次"
              options={(dicts.payment_periods ?? []).map((d) => ({ value: d.value, label: d.label }))}
            />
          </Form.Item>

          <Form.Item label="备注" name="remark">
            <Input.TextArea rows={2} maxLength={200} placeholder="如：首款 30%，到账日为合同约定日" />
          </Form.Item>
        </Form>
        {editing && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            当前记录：{formatYuan(editing.amount_fen)} 元 · {fmtDate(editing.paid_date)} ·{' '}
            {editing.method_label}
          </Typography.Text>
        )}
      </Modal>

      <Footnote>
        回款率 = 已回款 ÷ 已签约合同额（不是回款 ÷ 预算金额）；未回款 = 已签约合同额 − 已回款。
        金额单位：KPI 卡用万元、明细与登记表单用元（提交时换算为分，1 元 = 100 分）。
        合同额只统计状态为「已签约」的客户；「未回款清单」口径 = 合同额 &gt; 0 且未回款 &gt; 0 的客户。
        修改或删除回款记录后，对应商机的「已回款金额」会立即同步重算；记录带操作人与登记时间，可追溯。
      </Footnote>
      <NotForAssessment />
    </Space>
  );
}
