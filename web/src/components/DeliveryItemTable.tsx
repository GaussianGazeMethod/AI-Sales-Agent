import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import dayjs from 'dayjs';
import type { Dayjs } from 'dayjs';
import {
  Button,
  Card,
  Col,
  Collapse,
  DatePicker,
  Form,
  Input,
  Modal,
  Popconfirm,
  Radio,
  Row,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { AimOutlined, AppstoreOutlined, FileDoneOutlined } from '@ant-design/icons';
import {
  createDeliveryItem,
  deleteDeliveryItem,
  generateStandardItems,
  listDeliveryItems,
  listStandardItems,
  updateDeliveryItem,
} from '@/api/delivery';
import KpiCard from '@/components/KpiCard';
import { Footnote, NextStepGuide } from '@/components/Guide';
import { fmtDate } from '@/utils/format';
import type { DeliveryItem, DeliveryStandardItem } from '@/types';

interface Props {
  contractId: string;
  /** 只读（管理层） */
  readOnly?: boolean;
  onChanged?: () => void;
}

interface ItemFormValues {
  name: string;
  /** 后端 quantity 为字符串，故用文本输入 */
  quantity?: string;
  unit?: string;
  due_date?: Dayjs;
  acceptance_standard?: string;
  is_standard: boolean;
  remark?: string;
}

/**
 * 3.4 交付物清单：标准《项目服务清单》项 + 合同非标项，逐条核对与验收对齐。
 * 支持：一键生成标准项（幂等）、新增、行内编辑、删除、标准/非标筛选。
 */
export default function DeliveryItemTable({ contractId, readOnly = false, onChanged }: Props) {
  const qc = useQueryClient();
  const [keyword, setKeyword] = useState('');
  const [typeFilter, setTypeFilter] = useState<'all' | 'standard' | 'nonstandard'>('all');
  const [editing, setEditing] = useState<DeliveryItem | null>(null);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm<ItemFormValues>();

  const itemsQuery = useQuery({
    queryKey: ['delivery-items', contractId],
    queryFn: () => listDeliveryItems(contractId),
    enabled: !!contractId,
  });

  /** 标准《项目服务清单》模板（只读参考） */
  const templateQuery = useQuery({
    queryKey: ['delivery-standard-items'],
    queryFn: () => listStandardItems(),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['delivery-items', contractId] });
    qc.invalidateQueries({ queryKey: ['delivery-overview', contractId] });
    qc.invalidateQueries({ queryKey: ['handover', contractId] });
    onChanged?.();
  };

  const generate = useMutation({
    mutationFn: () => generateStandardItems(contractId),
    onSuccess: (r) => {
      message.success(`已生成标准项 ${r.created} 条（幂等：已存在的不重复生成）`);
      refresh();
    },
  });

  const save = useMutation({
    mutationFn: (v: ItemFormValues) => {
      const body = {
        name: v.name.trim(),
        quantity: v.quantity?.trim(),
        unit: v.unit?.trim(),
        due_date: v.due_date ? v.due_date.format('YYYY-MM-DD') : undefined,
        acceptance_standard: v.acceptance_standard?.trim(),
        is_standard: v.is_standard,
        remark: v.remark?.trim(),
      };
      return editing ? updateDeliveryItem(editing.id, body) : createDeliveryItem(contractId, body);
    },
    onSuccess: () => {
      message.success(editing ? '交付物已更新' : '交付物已新增');
      setEditing(null);
      setCreating(false);
      form.resetFields();
      refresh();
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteDeliveryItem(id),
    onSuccess: () => {
      message.success('交付物已删除');
      refresh();
    },
  });

  const dataSource = useMemo(() => {
    const list = itemsQuery.data?.items ?? [];
    const kw = keyword.trim();
    return list.filter((i) => {
      if (typeFilter === 'standard' && !i.is_standard) return false;
      if (typeFilter === 'nonstandard' && i.is_standard) return false;
      if (!kw) return true;
      return [i.name, i.acceptance_standard, i.remark, i.unit]
        .filter(Boolean)
        .some((t) => String(t).includes(kw));
    });
  }, [itemsQuery.data, keyword, typeFilter]);

  /** 清单口径统计（C3：比率就地可见；不虚构，缺值按 0 处理） */
  const allItems = itemsQuery.data?.items ?? [];
  const standardCount = allItems.filter((i) => i.is_standard).length;
  const nonstandardCount = allItems.length - standardCount;
  const withStandardCount = allItems.filter((i) => !!i.acceptance_standard).length;
  const nonstandardRate = allItems.length ? Math.round((nonstandardCount / allItems.length) * 100) : 0;
  const standardReadyRate = allItems.length ? Math.round((withStandardCount / allItems.length) * 100) : 0;

  const openCreate = () => {
    setEditing(null);
    form.setFieldsValue({
      name: '',
      quantity: '1',
      unit: '',
      due_date: undefined,
      acceptance_standard: '',
      is_standard: false,
      remark: '',
    });
    setCreating(true);
  };

  const columns = [
    {
      title: '交付物名称',
      dataIndex: 'name',
      ellipsis: true,
      render: (v: string, r: DeliveryItem) => (
        <Space size={6}>
          <span>{v}</span>
          <Tag color={r.is_standard ? 'blue' : 'orange'}>
            {r.is_standard_label || (r.is_standard ? '标准项' : '非标项')}
          </Tag>
        </Space>
      ),
    },
    {
      title: '数量 / 单位',
      key: 'quantity',
      width: 110,
      render: (_: unknown, r: DeliveryItem) =>
        r.quantity ? `${r.quantity}${r.unit ? ` ${r.unit}` : ''}` : r.unit || '-',
    },
    {
      title: '交付期限',
      dataIndex: 'due_date',
      width: 120,
      render: (v: string | null) => fmtDate(v),
    },
    {
      title: '验收标准',
      dataIndex: 'acceptance_standard',
      ellipsis: true,
      render: (v: string | null) =>
        v ? (
          v
        ) : (
          <Tooltip title="缺验收标准会影响 3.11 验收判定，请补齐后再进入验收。">
            <Tag color="warning" style={{ marginInlineEnd: 0 }}>
              未设验收标准
            </Tag>
          </Tooltip>
        ),
    },
    { title: '备注', dataIndex: 'remark', width: 180, ellipsis: true, render: (v: string | null) => v || '-' },
    {
      title: '操作',
      width: 130,
      render: (_: unknown, r: DeliveryItem) => (
        <Space size={4}>
          <Button
            type="link"
            size="small"
            disabled={readOnly}
            onClick={() => {
              setEditing(r);
              form.setFieldsValue({
                name: r.name,
                quantity: r.quantity ?? undefined,
                unit: r.unit ?? undefined,
                due_date: r.due_date ? dayjs(r.due_date) : undefined,
                acceptance_standard: r.acceptance_standard ?? undefined,
                is_standard: r.is_standard,
                remark: r.remark ?? undefined,
              });
            }}
          >
            编辑
          </Button>
          <Popconfirm
            title="确认删除该交付物？"
            okText="删除"
            cancelText="取消"
            disabled={readOnly}
            onConfirm={() => remove.mutate(r.id)}
          >
            <Button type="link" size="small" danger disabled={readOnly}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const isModalOpen = creating || !!editing;

  return (
    <Card
      size="small"
      title="交付物清单（标准《项目服务清单》+ 合同非标项）"
      extra={
        <Space wrap align="center">
          <Button
            type="primary"
            disabled={readOnly}
            loading={generate.isPending}
            onClick={() => generate.mutate()}
          >
            一键生成标准项
          </Button>
          <Footnote style={{ margin: 0 }}>
            按标准《项目服务清单》生成，幂等：已存在的不重复生成。
          </Footnote>
          <Button disabled={readOnly} onClick={openCreate}>
            新增交付物
          </Button>
        </Space>
      }
    >
      <Row gutter={12} style={{ marginBottom: 12 }}>
        <Col xs={12} md={8}>
          <KpiCard
            label="清单总条数"
            value={allItems.length}
            icon={<AppstoreOutlined />}
            tone="brand"
            hint="清单总条数 = 标准《项目服务清单》项 + 合同非标项"
          />
        </Col>
        <Col xs={12} md={8}>
          <KpiCard
            label="非标项占比"
            value={`${nonstandardRate}%`}
            total={` = ${nonstandardCount} / ${allItems.length}`}
            icon={<AimOutlined />}
            tone="amber"
            progress={nonstandardRate}
            hint="非标项占比 = 合同非标项 ÷ 清单总条数（非标项必须逐条澄清交付范围）"
          />
        </Col>
        <Col xs={12} md={8}>
          <KpiCard
            label="已设验收标准"
            value={`${standardReadyRate}%`}
            total={` = ${withStandardCount} / ${allItems.length}`}
            icon={<FileDoneOutlined />}
            tone="green"
            progress={standardReadyRate}
            hint="已设验收标准占比 = 已登记验收标准的交付物 ÷ 清单总条数；缺验收标准会影响 3.11 验收判定"
          />
        </Col>
      </Row>

      <Space wrap style={{ marginBottom: 12 }}>
        <Radio.Group
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value as 'all' | 'standard' | 'nonstandard')}
          optionType="button"
          buttonStyle="solid"
          options={[
            { value: 'all', label: '全部' },
            { value: 'standard', label: '标准项' },
            { value: 'nonstandard', label: '非标项' },
          ]}
        />
        <Input.Search
          allowClear
          placeholder="搜索交付物名称 / 验收标准"
          style={{ width: 240 }}
          onChange={(e) => setKeyword(e.target.value)}
        />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          共 {dataSource.length} 条；标准项来自运营维护的《项目服务清单》，非标项为合同特约内容。
        </Typography.Text>
      </Space>

      <Table<DeliveryItem>
        rowKey="id"
        size="small"
        loading={itemsQuery.isLoading}
        dataSource={dataSource}
        pagination={{ pageSize: 10, showTotal: (t) => `共 ${t} 条` }}
        locale={{
          emptyText: (
            <NextStepGuide
              title="暂无交付物清单"
              description="点「一键生成标准项」按标准《项目服务清单》生成（幂等，不重复）；合同特约内容用「新增交付物」登记为非标项。"
              actions={
                readOnly ? undefined : (
                  <Space>
                    <Button type="primary" loading={generate.isPending} onClick={() => generate.mutate()}>
                      一键生成标准项
                    </Button>
                    <Button onClick={openCreate}>新增非标项</Button>
                  </Space>
                )
              }
            />
          ),
        }}
        columns={columns}
      />

      <Collapse
        size="small"
        style={{ marginTop: 12 }}
        items={[
          {
            key: 'template',
            label: '标准《项目服务清单》模板（只读参考）',
            children: (
              <Table<DeliveryStandardItem>
                rowKey={(r) => r.id ?? r.name}
                size="small"
                loading={templateQuery.isLoading}
                pagination={false}
                dataSource={templateQuery.data?.items ?? []}
                locale={{
                  emptyText: (
                    <NextStepGuide
                      compact
                      title="标准清单模板暂未配置"
                      description="标准《项目服务清单》由运营在「6.3 交付标准清单配置」维护；维护后这里会显示模板项。"
                    />
                  ),
                }}
                columns={[
                  { title: '序号', dataIndex: 'sort_order', width: 70 },
                  { title: '服务项名称', dataIndex: 'name' },
                  { title: '单位', dataIndex: 'unit', width: 90, render: (v: string | null) => v || '-' },
                  {
                    title: '验收标准',
                    dataIndex: 'acceptance_standard',
                    ellipsis: true,
                    render: (v: string | null) => v || '-',
                  },
                ]}
              />
            ),
          },
        ]}
      />

      <Modal
        open={isModalOpen}
        title={editing ? `编辑交付物 · ${editing.name}` : '新增交付物'}
        okText="保存"
        cancelText="取消"
        confirmLoading={save.isPending}
        onCancel={() => {
          setCreating(false);
          setEditing(null);
          form.resetFields();
        }}
        onOk={async () => {
          const v = await form.validateFields();
          save.mutate(v);
        }}
      >
        <Form form={form} layout="vertical" initialValues={{ is_standard: false, quantity: '1' }}>
          <Form.Item label="交付物名称" name="name" rules={[{ required: true, message: '请填写名称' }]}>
            <Input placeholder="如：平板电脑交付与培训" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item label="数量" name="quantity" tooltip="后端为文本，可填数字或如「1200 人」">
                <Input placeholder="如 3" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="单位" name="unit">
                <Input placeholder="台 / 套 / 次" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="交付期限" name="due_date">
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="验收标准" name="acceptance_standard">
            <Input.TextArea rows={2} placeholder="如：平板全部到货并完成 2 场教师培训，客户签字确认" />
          </Form.Item>
          <Form.Item
            label="类型"
            name="is_standard"
            tooltip="标准项来自运营维护的《项目服务清单》；合同特约内容选「非标项」"
          >
            <Radio.Group
              options={[
                { value: false, label: '非标项（合同特约）' },
                { value: true, label: '标准项' },
              ]}
            />
          </Form.Item>
          <Form.Item label="备注" name="remark">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
