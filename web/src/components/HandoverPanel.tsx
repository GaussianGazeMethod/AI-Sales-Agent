import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import type { Dayjs } from 'dayjs';
import {
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
  Steps,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  CheckCircleFilled,
  FileTextOutlined,
  InfoCircleOutlined,
  WarningFilled,
} from '@ant-design/icons';
import {
  confirmHandover,
  createHandover,
  getHandover,
  listDeliveryItems,
  listUserOptions,
  updateDeliveryItem,
} from '@/api/delivery';
import { useAuthStore } from '@/store/auth';
import { fmtDate, fmtDateTime } from '@/utils/format';
import { BRAND, SUCCESS, TEXT_SUB, WARNING } from '@/theme';
import GateBanner from '@/components/GateBanner';
import { Footnote, NextStepGuide } from '@/components/Guide';
import type { DeliveryItem, HandoverCreateBody, HandoverOut, UserOption } from '@/types';

interface Props {
  contractId: string;
  /** 交接单数据（父页面已取则直接传入，避免重复请求） */
  handover?: HandoverOut | null;
  /** 只读（管理层） */
  readOnly?: boolean;
  /** 变更后通知父页面刷新交付总览 */
  onChanged?: () => void;
  /** 合同是否含硬件（来自合同；false 时明确显示「本项目不适用」） */
  hasHardware?: boolean;
}

interface HandoverFormValues {
  handover_user_id: string;
  receiver_id: string;
  delivery_owner_id: string;
  handover_at?: Dayjs;
  standard_note?: string;
  nonstandard_note?: string;
  remark?: string;
}

interface ItemFormValues {
  name: string;
  /** 后端 quantity 为字符串，故用文本输入 */
  quantity?: string;
  unit?: string;
  due_date?: Dayjs;
  acceptance_standard?: string;
  remark?: string;
}

/** 销售（移交人）候选角色 */
const SALES_ROLES = ['market_staff', 'market_manager', 'biz_support'];
/** 运营交付部角色 */
const DELIVERY_ROLES = ['delivery_staff', 'delivery_manager'];

/** 卡片标题旁的状态胶囊（设计稿 07：已完成=绿底、进行中=橙底） */
function SoftFlag({ tone, children }: { tone: 'success' | 'process'; children: React.ReactNode }) {
  const map = {
    success: { bg: '#E7F8F0', fg: '#027A48', border: '#C7EEDC' },
    process: { bg: '#FEF4E6', fg: '#B54708', border: '#FBE2BF' },
  }[tone];
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        background: map.bg,
        color: map.fg,
        border: `1px solid ${map.border}`,
        borderRadius: 999,
        padding: '0 10px',
        fontSize: 12,
        lineHeight: '20px',
      }}
    >
      {children}
    </span>
  );
}

/** 双方确认的「身份块」（设计稿 07）：圆头像 + 角色 + 姓名 + 演示数据 + 右侧状态胶囊 + 时间 */
function IdentityBlock({
  initial,
  role,
  name,
  confirmed,
  confirmedAt,
  action,
}: {
  initial: string;
  role: string;
  name: string;
  confirmed: boolean;
  confirmedAt?: string | null;
  action?: React.ReactNode;
}) {
  return (
    <div
      style={{
        border: `1px solid ${confirmed ? '#C7EEDC' : '#EAECF0'}`,
        borderRadius: 8,
        padding: '10px 12px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 36,
            height: 36,
            borderRadius: '50%',
            background: BRAND,
            color: '#FFFFFF',
            fontWeight: 600,
            flexShrink: 0,
          }}
        >
          {initial}
        </span>
        <div style={{ minWidth: 0, flex: 1 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {role}
          </Typography.Text>
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            {name}
            <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
              {' '}
              （演示数据）
            </Typography.Text>
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {confirmed ? fmtDateTime(confirmedAt) : '-'}
          </Typography.Text>
        </div>
        <SoftFlag tone={confirmed ? 'success' : 'process'}>
          {confirmed ? '已确认' : '待确认'}
        </SoftFlag>
      </div>
      {action && <div style={{ marginTop: 8 }}>{action}</div>}
    </div>
  );
}

/**
 * 3.3 交付交接单（对应设计稿 07，全系统唯一一次真正的交接：销售 → 运营交付部）
 *
 * 三段式：① 标准《项目服务清单》核对 → ② 非标内容澄清（内部交接会）→ ③ 双方确认。
 *
 * 判据落点：
 * - C2 少判断：未澄清的非标内容用 GateBanner 说清"还差几项、差哪项"，
 *   「确认接收」按钮**置灰 + Tooltip 说明原因**，而不是等点了才报错。
 * - C3 口径可见：「核对结果」的口径（是否已登记验收标准）写在列头 Tooltip 与页脚脚注里。
 */
export default function HandoverPanel({
  contractId,
  handover,
  readOnly = false,
  onChanged,
  hasHardware,
}: Props) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const [createForm] = Form.useForm<HandoverFormValues>();
  const [noteForm] = Form.useForm<{ nonstandard_note: string }>();
  const [itemForm] = Form.useForm<ItemFormValues>();
  const [editingItem, setEditingItem] = useState<DeliveryItem | null>(null);
  const [viewingItem, setViewingItem] = useState<DeliveryItem | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  /** 交接单（父页面传值时不重复请求；后端返回体为 {handover: ...}） */
  const handoverQuery = useQuery({
    queryKey: ['handover', contractId],
    queryFn: () => getHandover(contractId),
    enabled: handover === undefined,
  });
  const data = handover !== undefined ? handover : (handoverQuery.data?.handover ?? null);

  /** 合同下的交付物（交接单未创建时也能核对标准项） */
  const itemsQuery = useQuery({
    queryKey: ['delivery-items', contractId],
    queryFn: () => listDeliveryItems(contractId),
    enabled: !!contractId,
  });

  /** 人员下拉：移交人（销售）/ 接收人 / 运营交付专员 */
  const usersQuery = useQuery({
    queryKey: ['users', 'options', 'all'],
    queryFn: () => listUserOptions(),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  const users: UserOption[] = usersQuery.data?.items ?? [];
  const optionsOf = (roles: string[]) =>
    users.filter((u) => roles.includes(u.role)).map((u) => ({ value: u.id, label: `${u.name}（${u.role_label}）` }));

  const items: DeliveryItem[] = data?.items?.length ? data.items : (itemsQuery.data?.items ?? []);
  const standardItems = items.filter((i) => i.is_standard);
  const nonstandardItems = items.filter((i) => !i.is_standard);

  /** 标准清单核对：标准项已生成，且每条都已登记验收标准（口径见页脚脚注） */
  const standardMissing = standardItems.filter((i) => !i.acceptance_standard);
  const step1Done = standardItems.length > 0 && standardMissing.length === 0;
  const step1Process = !step1Done && standardItems.length > 0;

  /** 非标内容澄清：无非标项，或已登记澄清记录；否则视为待澄清 */
  const pendingNonstandard = data?.nonstandard_note ? [] : nonstandardItems;
  const step2Done = nonstandardItems.length === 0 || !!data?.nonstandard_note;
  const step2Process = pendingNonstandard.length > 0;

  const step3Done = !!data?.is_confirmed;

  const currentStep = step1Done ? (step2Done ? (step3Done ? 3 : 2) : 1) : 0;

  const steps = useMemo(
    () => [
      {
        title: '标准清单核对',
        status: (step1Done ? 'finish' : step1Process ? 'process' : 'wait') as 'finish' | 'process' | 'wait',
        description: (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {step1Done
              ? `已完成 ${fmtDateTime(data?.handover_at)}`
              : standardItems.length
                ? '进行中'
                : '待处理'}
          </Typography.Text>
        ),
      },
      {
        title: '非标内容澄清',
        status: (step2Done ? 'finish' : step2Process ? 'process' : 'wait') as 'finish' | 'process' | 'wait',
        description: (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {step2Done ? (nonstandardItems.length ? '已完成' : '不适用') : '进行中'}
          </Typography.Text>
        ),
      },
      {
        title: '双方确认',
        status: (step3Done ? 'finish' : data ? 'process' : 'wait') as 'finish' | 'process' | 'wait',
        description: (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {step3Done ? '已确认' : '待确认'}
          </Typography.Text>
        ),
      },
    ],
    [step1Done, step1Process, step2Done, step2Process, step3Done, data, standardItems.length, nonstandardItems.length],
  );

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['handover', contractId] });
    qc.invalidateQueries({ queryKey: ['delivery-overview', contractId] });
    qc.invalidateQueries({ queryKey: ['delivery-items', contractId] });
    onChanged?.();
  };

  /** 新建 / 更新交接单（后端 POST 为唯一入口，重复提交视为更新） */
  const saveHandover = useMutation({
    mutationFn: (body: HandoverCreateBody) => createHandover(contractId, body),
    onSuccess: () => {
      message.success('交付交接单已保存');
      setShowCreate(false);
      refresh();
    },
  });

  /** 双方确认 */
  const doConfirm = useMutation({
    mutationFn: (v: { role: 'sales' | 'delivery' }) => confirmHandover(data!.id, { role: v.role }),
    onSuccess: (_r, v) => {
      message.success(v.role === 'sales' ? '销售方已确认' : '交付方已确认');
      refresh();
    },
  });

  /** 非标项逐条编辑 */
  const saveItem = useMutation({
    mutationFn: (v: ItemFormValues) =>
      updateDeliveryItem(editingItem!.id, {
        name: v.name.trim(),
        quantity: v.quantity?.trim(),
        unit: v.unit?.trim(),
        due_date: v.due_date ? v.due_date.format('YYYY-MM-DD') : undefined,
        acceptance_standard: v.acceptance_standard?.trim(),
        remark: v.remark?.trim(),
      }),
    onSuccess: () => {
      message.success('非标项已更新');
      setEditingItem(null);
      itemForm.resetFields();
      refresh();
    },
  });

  const submitCreate = (values: HandoverFormValues) => {
    const body: HandoverCreateBody = {
      handover_user_id: values.handover_user_id,
      receiver_id: values.receiver_id,
      delivery_owner_id: values.delivery_owner_id,
      // 交接日期按本地提交，避免时区偏移
      handover_at: values.handover_at
        ? dayjs(values.handover_at).format('YYYY-MM-DD')
        : undefined,
      standard_note: values.standard_note?.trim(),
      nonstandard_note: values.nonstandard_note?.trim(),
      remark: values.remark?.trim(),
    };
    saveHandover.mutate(body);
  };

  /** 仅更新非标澄清说明（后端为部分更新，只传变更字段，避免重复指派/通知） */
  const submitNote = (values: { nonstandard_note: string }) => {
    if (!data) return;
    saveHandover.mutate({ nonstandard_note: values.nonstandard_note?.trim() });
  };

  const canSalesConfirm = user?.role === 'admin' || user?.id === data?.handover_user_id;
  const canDeliveryConfirm =
    user?.role === 'admin' ||
    user?.id === data?.delivery_owner_id ||
    user?.id === data?.receiver_id;

  /** 「确认接收」被拦下的原因（C2：置灰必须说清为什么 + 怎么办） */
  const acceptBlockedReason = readOnly
    ? '管理层为只读视图，确认操作需由接收人 / 运营交付专员本人完成。'
    : !canDeliveryConfirm
      ? '仅接收人、运营交付专员本人或管理员可确认接收。'
      : data?.delivery_confirmed
        ? '交付方已确认，无需重复操作。'
        : pendingNonstandard.length > 0
          ? `尚有 ${pendingNonstandard.length} 项非标边界未明确，建议先澄清后再确认接收（在下方「非标内容澄清」登记澄清记录）。`
          : '';
  const canAccept = !readOnly && !!data && !data.delivery_confirmed && canDeliveryConfirm && pendingNonstandard.length === 0;

  const itemColumns = [
    {
      title: '项目',
      dataIndex: 'name',
      ellipsis: true,
      render: (v: string, r: DeliveryItem) => (
        <Space size={6} wrap>
          <span>{v}</span>
          <Tag color={r.is_standard ? 'blue' : 'orange'}>
            {r.is_standard_label || (r.is_standard ? '标准项' : '非标项')}
          </Tag>
        </Space>
      ),
    },
    {
      title: '内容',
      key: 'content',
      ellipsis: true,
      render: (_: unknown, r: DeliveryItem) => {
        const qty = r.quantity ? `${r.quantity}${r.unit ? ` ${r.unit}` : ''}` : r.unit || '';
        return qty ? `${qty}${r.due_date ? ` · 期限 ${fmtDate(r.due_date)}` : ''}` : r.due_date ? `期限 ${fmtDate(r.due_date)}` : '-';
      },
    },
    {
      title: (
        <Tooltip title="核对结果 = 该交付物是否已登记验收标准；标准清单由启动会与客户逐条核对，系统只记录结果，不代替核对。">
          <span>核对结果</span>
        </Tooltip>
      ),
      key: 'checked',
      width: 120,
      render: (_: unknown, r: DeliveryItem) =>
        r.acceptance_standard ? (
          <Space size={4}>
            <CheckCircleFilled style={{ color: SUCCESS }} />
            <span style={{ color: SUCCESS }}>已核对</span>
          </Space>
        ) : (
          <Tooltip title="缺验收标准会影响 3.11 验收判定；请到交付物清单逐条补齐。">
            <Space size={4}>
              <WarningFilled style={{ color: WARNING }} />
              <span style={{ color: WARNING }}>待核对</span>
            </Space>
          </Tooltip>
        ),
    },
    {
      title: '证据',
      key: 'evidence',
      width: 180,
      render: (_: unknown, r: DeliveryItem) =>
        r.acceptance_standard ? (
          <Space size={6}>
            <FileTextOutlined style={{ color: BRAND }} />
            <a onClick={() => setViewingItem(r)}>查看验收标准</a>
          </Space>
        ) : (
          <a onClick={() => navigate(`/contracts/${contractId}/delivery-items`)}>去补充</a>
        ),
    },
    {
      title: '澄清状态',
      key: 'clarity',
      width: 110,
      render: (_: unknown, r: DeliveryItem) =>
        r.acceptance_standard ? (
          <Space size={4}>
            <CheckCircleFilled style={{ color: SUCCESS }} />
            <span style={{ color: SUCCESS }}>已澄清</span>
          </Space>
        ) : (
          <Space size={4}>
            <WarningFilled style={{ color: WARNING }} />
            <span style={{ color: WARNING }}>未澄清</span>
          </Space>
        ),
    },
    {
      title: '操作',
      width: 100,
      render: (_: unknown, r: DeliveryItem) =>
        r.is_standard ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            标准模板
          </Typography.Text>
        ) : (
          <Button
            size="small"
            style={{ background: '#FFFFFF', borderColor: BRAND, color: BRAND }}
            disabled={readOnly}
            onClick={() => {
              setEditingItem(r);
              itemForm.setFieldsValue({
                name: r.name,
                quantity: r.quantity ?? undefined,
                unit: r.unit ?? undefined,
                due_date: r.due_date ? dayjs(r.due_date) : undefined,
                acceptance_standard: r.acceptance_standard ?? undefined,
                remark: r.remark ?? undefined,
              });
            }}
          >
            记录澄清
          </Button>
        ),
    },
  ];

  /** 非标内容澄清表（设计稿 07）：非标事项 / 需要澄清的内容 / 澄清负责人 / 澄清状态 / 操作 */
  const nonstandardColumns = [
    itemColumns[0],
    itemColumns[1],
    {
      title: '澄清负责人',
      key: 'owner',
      width: 110,
      render: (_: unknown, r: DeliveryItem) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {r.remark ? '见澄清记录' : '待指派'}
        </Typography.Text>
      ),
    },
    {
      title: '澄清状态',
      key: 'clarity',
      width: 110,
      render: (_: unknown, r: DeliveryItem) =>
        r.acceptance_standard ? (
          <Space size={4}>
            <CheckCircleFilled style={{ color: SUCCESS }} />
            <span style={{ color: SUCCESS }}>已澄清</span>
          </Space>
        ) : (
          <Space size={4}>
            <WarningFilled style={{ color: WARNING }} />
            <span style={{ color: WARNING }}>未澄清</span>
          </Space>
        ),
    },
    itemColumns[4],
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {/* 顶部三段式进度（设计稿 07 通栏）：① 标准清单核对 → ② 非标内容澄清 → ③ 双方确认 */}
      <Card size="small" styles={{ body: { padding: '18px 40px 14px' } }}>
        <Steps size="small" current={currentStep} items={steps} labelPlacement="vertical" />
      </Card>

      {!data && (
        <GateBanner
          title="尚未创建交付交接单，交接流程未开始"
          items={[
            '签约（闭环二环节七）后由销售发起、运营交付部接收；双方各自确认后才算完成交接。',
            '先登记交接单（移交人 / 接收人 / 运营交付专员），再逐条核对标准服务清单与非标内容。',
          ]}
          action={
            <Button type="primary" size="small" disabled={readOnly} onClick={() => setShowCreate(true)}>
              登记交接单
            </Button>
          }
        />
      )}

      <Row gutter={12}>
        {/* 左栏：① 标准清单核对 + ② 非标内容澄清 */}
        <Col xs={24} lg={16}>
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Card
              size="small"
              title={
                <Space size={8}>
                  <span>标准《项目服务清单》</span>
                  {step1Done ? (
                    <SoftFlag tone="success">已完成</SoftFlag>
                  ) : (
                    <SoftFlag tone="process">{standardItems.length ? '进行中' : '待处理'}</SoftFlag>
                  )}
                </Space>
              }
              extra={
                <Button size="small" onClick={() => navigate(`/contracts/${contractId}/delivery-items`)}>
                  去交付物清单生成 / 逐条核对
                </Button>
              }
            >
              <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
                标准服务清单由运营维护，在启动会（客户 + 销售 + 交付）与客户逐条核对；下表为当前合同下的标准项。
              </Typography.Paragraph>
              <Table<DeliveryItem>
                rowKey="id"
                size="small"
                pagination={false}
                dataSource={standardItems}
                loading={itemsQuery.isLoading}
                locale={{
                  emptyText: (
                    <NextStepGuide
                      compact
                      title="暂无标准《项目服务清单》项"
                      description="到交付物清单点「一键生成标准项」按标准清单生成（幂等，不重复），再回到本页核对。"
                      actions={
                        <Button size="small" type="primary" onClick={() => navigate(`/contracts/${contractId}/delivery-items`)}>
                          去生成标准项
                        </Button>
                      }
                    />
                  ),
                }}
                columns={itemColumns.filter((_, i) => [0, 1, 2, 4].includes(i))}
              />
            </Card>

            <Card
              size="small"
              title={
                <Space size={8}>
                  <span>非标内容澄清</span>
                  {step2Done ? (
                    <SoftFlag tone="success">已完成</SoftFlag>
                  ) : (
                    <SoftFlag tone="process">进行中</SoftFlag>
                  )}
                </Space>
              }
            >
              {pendingNonstandard.length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <GateBanner
                    title={`尚有 ${pendingNonstandard.length} 项非标边界未明确，建议澄清后确认`}
                    items={pendingNonstandard.map((i) => `${i.name}（待澄清交付范围 / 验收标准）`)}
                    action={
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        在下方表格点「记录澄清」，或直接在「非标内容澄清记录」里写明后保存。
                      </Typography.Text>
                    }
                  />
                </div>
              )}

              <Table<DeliveryItem>
                rowKey="id"
                size="small"
                pagination={false}
                dataSource={nonstandardItems}
                loading={itemsQuery.isLoading}
                locale={{
                  emptyText: (
                    <NextStepGuide
                      compact
                      title="暂无非标项"
                      description="说明合同内容与标准《项目服务清单》一致；若合同有特约内容，请到交付物清单手工新增非标项。"
                      actions={
                        <Button size="small" onClick={() => navigate(`/contracts/${contractId}/delivery-items`)}>
                          去新增非标项
                        </Button>
                      }
                    />
                  ),
                }}
                columns={nonstandardColumns}
              />

              {hasHardware === false && (
                <div style={{ marginTop: 10 }}>
                  <Space size={6}>
                    <InfoCircleOutlined style={{ color: TEXT_SUB }} />
                    <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                      无硬件交付（本项目不适用）
                    </Typography.Text>
                  </Space>
                </div>
              )}

              <Form form={noteForm} layout="vertical" initialValues={{ nonstandard_note: data?.nonstandard_note ?? '' }} style={{ marginTop: 12 }}>
                <Form.Item
                  label="非标内容澄清记录"
                  name="nonstandard_note"
                  tooltip="逐条写明非标内容、责任人与边界；保存后会作为交付范围与验收依据。"
                >
                  <Input.TextArea
                    rows={3}
                    disabled={readOnly}
                    placeholder={'如：\n1. 需额外交付 20 台平板并完成教师培训（合同第 3 条）；\n2. 需定制阅卷规则 3 套，5 月底前上线。'}
                  />
                </Form.Item>
                <Space wrap>
                  <Tooltip title={data ? '' : '需先登记交接单后才能保存澄清记录。'}>
                    <Button
                      type="primary"
                      disabled={readOnly || !data}
                      loading={saveHandover.isPending}
                      onClick={async () => {
                        const v = await noteForm.validateFields();
                        submitNote(v);
                      }}
                    >
                      保存澄清记录
                    </Button>
                  </Tooltip>
                  {nonstandardItems.length === 0 && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      当前合同无非标项，本段可留空（说明合同内容与标准清单一致）。
                    </Typography.Text>
                  )}
                </Space>
              </Form>
            </Card>
          </Space>
        </Col>

        {/* 右栏：③ 双方确认（两个身份块）+ 琥珀提示 + 「退回澄清 / 确认接收」 */}
        <Col xs={24} lg={8}>
          <Card size="small" title="双方确认">
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              {/* 销售侧身份块 */}
              <IdentityBlock
                initial={(data?.handover_user_name || '?').slice(0, 1)}
                role="市场专员（移交方 · 销售侧）"
                name={data?.handover_user_name || '-'}
                confirmed={!!data?.sales_confirmed}
                confirmedAt={data?.sales_confirmed_at}
                action={
                  <Tooltip title={canSalesConfirm ? '' : '仅移交人本人或管理员可确认；请由移交人本人操作。'}>
                    <Button
                      size="small"
                      disabled={readOnly || !data || data.sales_confirmed || !canSalesConfirm}
                      loading={doConfirm.isPending}
                      onClick={() => doConfirm.mutate({ role: 'sales' })}
                    >
                      销售方确认移交
                    </Button>
                  </Tooltip>
                }
              />

              {/* 交付侧身份块 */}
              <IdentityBlock
                initial={(data?.delivery_owner_name || data?.receiver_name || '?').slice(0, 1)}
                role={`运营交付专员（接收方 · 交付侧）${
                  data?.receiver_name && data.receiver_name !== data.delivery_owner_name
                    ? ` · 接收人 ${data.receiver_name}`
                    : ''
                }`}
                name={data?.delivery_owner_name || '-'}
                confirmed={!!data?.delivery_confirmed}
                confirmedAt={data?.delivery_confirmed_at}
                action={null}
              />

              {data?.standard_note && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  标准清单核对说明：{data.standard_note}
                </Typography.Text>
              )}
              {data?.remark && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  备注：{data.remark}
                </Typography.Text>
              )}

              {pendingNonstandard.length > 0 ? (
                <GateBanner
                  title={`尚有 ${pendingNonstandard.length} 项非标边界未明确，建议澄清后确认。`}
                  tone="gate"
                />
              ) : !step1Done && standardItems.length > 0 ? (
                <GateBanner
                  title={`标准清单还有 ${standardMissing.length} 项未登记验收标准，建议补齐后确认。`}
                  tone="gate"
                />
              ) : null}

              <Space size={8} wrap>
                <Tooltip
                  title={
                    pendingNonstandard.length > 0
                      ? `尚有 ${pendingNonstandard.length} 项非标边界未明确，请先在上方「非标内容澄清」逐条澄清并保存。`
                      : '如需退回修改，请在「非标内容澄清」补记边界或联系销售侧修改标准清单。'
                  }
                >
                  <Button
                    disabled={readOnly || !data}
                    style={{ background: '#EEF1FF', borderColor: '#EEF1FF', color: BRAND }}
                  >
                    退回澄清
                  </Button>
                </Tooltip>
                <Tooltip title={canAccept ? '确认接收后交接完成。' : acceptBlockedReason}>
                  <Button
                    disabled={!canAccept}
                    loading={doConfirm.isPending}
                    onClick={() => doConfirm.mutate({ role: 'delivery' })}
                  >
                    确认接收
                  </Button>
                </Tooltip>
              </Space>

              {/* C2：置灰时把原因写在按钮下方（不只 Tooltip） */}
              {!canAccept && !!acceptBlockedReason && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {acceptBlockedReason}
                </Typography.Text>
              )}

              <Space size={6} wrap>
                <Tag color={data?.is_confirmed ? 'success' : 'warning'}>
                  {data?.is_confirmed ? '双方已确认 · 交接完成' : '交接未完成'}
                </Tag>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  交接时间：{fmtDateTime(data?.handover_at)}
                </Typography.Text>
              </Space>

              <Space size={6} align="start">
                <InfoCircleOutlined style={{ color: TEXT_SUB, marginTop: 2 }} />
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  交接确认应核对服务边界与承诺，所有操作留痕。
                </Typography.Text>
              </Space>

              <Footnote>本次是唯一一次销售→交付交接；确认应核对服务边界与承诺，所有操作留痕。</Footnote>
            </Space>
          </Card>
        </Col>
      </Row>

      {/* 交接单登记（未创建时才展开） */}
      {!data && showCreate && (
        <Card size="small" title="登记交付交接单（销售 → 运营交付部）">
          <Form form={createForm} layout="vertical" onFinish={submitCreate} disabled={readOnly}>
            <Row gutter={12}>
              <Col xs={24} md={8}>
                <Form.Item
                  label="移交人（销售）"
                  name="handover_user_id"
                  rules={[{ required: true, message: '请选择移交人' }]}
                >
                  <Select options={optionsOf(SALES_ROLES)} placeholder="选择销售" showSearch optionFilterProp="label" />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}>
                <Form.Item
                  label="接收人"
                  name="receiver_id"
                  rules={[{ required: true, message: '请选择接收人' }]}
                >
                  <Select options={optionsOf(DELIVERY_ROLES)} placeholder="选择接收人" showSearch optionFilterProp="label" />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}>
                <Form.Item
                  label="运营交付专员（客户级归属）"
                  name="delivery_owner_id"
                  rules={[{ required: true, message: '请指派运营交付专员' }]}
                >
                  <Select
                    options={optionsOf(DELIVERY_ROLES)}
                    placeholder="一名客户配一名专员"
                    showSearch
                    optionFilterProp="label"
                  />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}>
                <Form.Item label="交接时间" name="handover_at">
                  <DatePicker showTime style={{ width: '100%' }} placeholder="默认当前时间" />
                </Form.Item>
              </Col>
              <Col xs={24} md={16}>
                <Form.Item label="标准服务清单核对说明" name="standard_note">
                  <Input placeholder="如：启动会已与客户逐条核对 12 项标准服务，无异议" />
                </Form.Item>
              </Col>
              <Col span={24}>
                <Form.Item label="备注" name="remark">
                  <Input.TextArea rows={2} placeholder="其他需要交付部知晓的信息" />
                </Form.Item>
              </Col>
            </Row>
            <Space>
              <Button type="primary" htmlType="submit" loading={saveHandover.isPending} disabled={readOnly}>
                提交交接单
              </Button>
              <Button onClick={() => setShowCreate(false)}>取消</Button>
            </Space>
          </Form>
        </Card>
      )}

      {/* 查看验收标准（标准项的「证据」入口） */}
      <Modal
        open={!!viewingItem}
        title="查看核对证据"
        footer={<Button onClick={() => setViewingItem(null)}>关闭</Button>}
        onCancel={() => setViewingItem(null)}
      >
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Typography.Text strong>{viewingItem?.name}</Typography.Text>
          <Space size={6} wrap>
            <Tag color={viewingItem?.is_standard ? 'blue' : 'orange'}>
              {viewingItem?.is_standard_label || (viewingItem?.is_standard ? '标准项' : '非标项')}
            </Tag>
            {viewingItem?.quantity && (
              <Tag>
                {viewingItem.quantity}
                {viewingItem.unit ? ` ${viewingItem.unit}` : ''}
              </Tag>
            )}
            {viewingItem?.due_date && <Tag>交付期限 {fmtDate(viewingItem.due_date)}</Tag>}
          </Space>
          <Card size="small">
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              验收标准
            </Typography.Text>
            <Typography.Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>
              {viewingItem?.acceptance_standard || '（未登记）'}
            </Typography.Paragraph>
          </Card>
          {viewingItem?.remark && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              备注：{viewingItem.remark}
            </Typography.Text>
          )}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            验收标准是核对结果的判定依据；改动请到交付物清单，本页只做核对留痕。
          </Typography.Text>
        </Space>
      </Modal>

      {/* 非标项行内编辑（记录澄清） */}
      <Modal
        open={!!editingItem}
        title={`记录澄清 · ${editingItem?.name ?? ''}`}
        okText="保存"
        cancelText="取消"
        confirmLoading={saveItem.isPending}
        onCancel={() => {
          setEditingItem(null);
          itemForm.resetFields();
        }}
        onOk={async () => {
          const v = await itemForm.validateFields();
          saveItem.mutate(v);
        }}
      >
        <Form form={itemForm} layout="vertical">
          <Form.Item label="交付物名称" name="name" rules={[{ required: true, message: '请填写名称' }]}>
            <Input />
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
          <Form.Item
            label="验收标准（澄清后的交付边界）"
            name="acceptance_standard"
            tooltip="填了验收标准，「核对结果」才会显示已登记。"
          >
            <Input.TextArea rows={2} placeholder="如：平板全部到货并完成 2 场教师培训" />
          </Form.Item>
          <Form.Item label="备注" name="remark">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
