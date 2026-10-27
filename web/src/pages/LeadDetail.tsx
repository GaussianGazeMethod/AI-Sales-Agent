import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Divider,
  Form,
  Input,
  Modal,
  Popconfirm,
  Radio,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tabs,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  CheckCircleFilled,
  ClockCircleOutlined,
  EnvironmentOutlined,
  FilePdfFilled,
  FileTextOutlined,
  GlobalOutlined,
  PaperClipOutlined,
  PhoneFilled,
  PlusOutlined,
  TeamOutlined,
  WarningFilled,
  WechatFilled,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import {
  claimLead,
  createContact,
  createFollowup,
  deleteContact,
  getLeadDetail,
  reviveLead,
  updateContact,
} from '@/api/leads';
import { useDicts } from '@/hooks/useDicts';
import { useAuthStore } from '@/store/auth';
import { ASSIGN_ACTION_LABEL } from '@/utils/constants';
import { fenToYuan, fenToWan, fmtDate, fmtDateTime, fmtPercent, toLabelMap } from '@/utils/format';
import {
  FollowCountdown,
  LeadStatusTag,
  OrgTypeTag,
  ReminderTag,
  RiskTags,
  SourceTag,
} from '@/components/tags';
import GateBanner from '@/components/GateBanner';
import KpiCard from '@/components/KpiCard';
import PageHeader from '@/components/PageHeader';
import { Footnote, NextStepGuide, PanelFlag } from '@/components/Guide';
import { SUCCESS, WARNING } from '@/theme';
import type { Contact, FollowUp, OperationLog } from '@/types';

/**
 * 1.2 线索详情与有效性复核（对齐 UIdesign/03-线索详情与有效性复核.png）
 *
 * 骨架（照设计稿）：
 *   面包屑（MainLayout）→ 大标题 + 演示数据 + 新增跟进/… → 一行图标元信息
 *   → SLA 通栏（首次跟进已完成 ｜ 回收剩余 N 工作日）→ Tabs
 *   → 左 2/3：最新跟进记录 + 人工复核；右 1/3：自动初判 + 联系人信息 + 蓝色 info 块
 *
 * 判据：
 *   C2 少判断 —— 每个禁用/空态都有"为什么 + 怎么办"（GateBanner / NextStepGuide）
 *   C3 口径可见 —— 工作日 vs 自然日就地标注（SLA 条 / KPI hint / 页脚）
 *   C4 赋能边界 —— 自动初判只给建议，必须人工复核才生效（琥珀条 + 口径文案）
 *
 * 复核表单提交 method、followed_at、content、is_valid、next_action、info_verified、
 *    next_follow_at：`info_verified`（信息是否核实正确）与 `next_action`（是否有意愿）
 *    是 spec/02 §7 的两个判定依据，**都要显式传**；
 *    `is_valid` 仍由这两项推导后一并提交（供服务端交叉校验）。
 */

/** 「意愿依据」可选项 —— 与后端 INTENT_ACTIONS（汇报交流 / 微信交流看我方资料）同口径 */
const INTENT_OPTIONS = [
  { value: 'report_meeting', label: '同意下一步汇报交流' },
  { value: 'wechat_material', label: '微信交流看我方资料' },
] as const;

/** 复核「下一步动作」输入框上限（与设计稿计数 16/200 同口径） */
const NEXT_ACTION_MAX = 200;

/**
 * 工作日差值（只用于展示「回收剩余 N 个工作日」，实时口径以后端下发的
 * `recycle_deadline` / `first_follow_deadline` 为准）。
 */
function workdaysUntil(deadline?: string | null): number | null {
  if (!deadline) return null;
  let d = dayjs().startOf('day');
  const end = dayjs(deadline).startOf('day');
  if (end.isBefore(d)) return -d.diff(end, 'day');
  let days = 0;
  while (d.isBefore(end)) {
    d = d.add(1, 'day');
    const dow = d.day();
    if (dow !== 0 && dow !== 6) days += 1;
  }
  return days;
}

/** 判断联系人里是否已有「关键决策人」的直接联系方式（关键词匹配职位） */
const DECISION_KEYWORDS = ['校长', '主任', '局长', '院长', '决策', '负责人', '总经理', '处长', '科长'];
function hasDecisionMakerContact(contacts: Contact[] | undefined): boolean {
  if (!contacts?.length) return false;
  return contacts.some(
    (c) =>
      !!c.phone?.trim() &&
      DECISION_KEYWORDS.some((k) => (c.position ?? '').includes(k) || (c.name ?? '').includes(k)),
  );
}

/** 手机号脱敏：138****6208（照设计稿；号码不足 7 位时原样展示） */
function maskPhone(phone?: string | null): string {
  const raw = (phone ?? '').trim();
  if (!raw) return '';
  if (raw.length < 7) return raw;
  return `${raw.slice(0, 3)}****${raw.slice(-4)}`;
}

/** 跟进方式 → 圆形彩色图标（设计稿里是蓝色实心圆 + 白色方式图标） */
const METHOD_ICON: Record<string, { icon: React.ReactNode; bg: string }> = {
  phone: { icon: <PhoneFilled />, bg: '#465FFF' },
  wechat: { icon: <WechatFilled />, bg: '#12B76A' },
  visit: { icon: <TeamOutlined />, bg: '#F79009' },
  online_meeting: { icon: <GlobalOutlined />, bg: '#7A5AF8' },
};

function MethodAvatar({ method }: { method: string }) {
  const conf = METHOD_ICON[method] ?? { icon: <PhoneFilled />, bg: '#465FFF' };
  return (
    <span
      style={{
        width: 40,
        height: 40,
        borderRadius: '50%',
        background: conf.bg,
        color: '#fff',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: 17,
        flex: '0 0 auto',
      }}
    >
      {conf.icon}
    </span>
  );
}

/** 文件 chip（设计稿：浅底 + 文件图标 + 文件名 + 大小 KB） */
function FileChip({
  name,
  sizeKb,
  tone = 'pdf',
  hint,
  onClick,
}: {
  name: string;
  sizeKb?: number | null;
  tone?: 'pdf' | 'doc';
  hint?: string;
  onClick?: () => void;
}) {
  const brand = tone === 'pdf' ? '#F04438' : '#465FFF';
  const icon = tone === 'pdf' ? <FilePdfFilled /> : <FileTextOutlined />;
  return (
    <Tooltip title={hint}>
      <span
        onClick={onClick}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 8,
          padding: '6px 12px 6px 10px',
          border: '1px solid #EAECF0',
          background: '#FCFCFD',
          borderRadius: 8,
          cursor: onClick ? 'pointer' : 'default',
          maxWidth: '100%',
        }}
      >
        <span style={{ color: brand, fontSize: 18, lineHeight: 1 }}>{icon}</span>
        <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          <span style={{ fontSize: 13, color: '#182230', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {name}
          </span>
          {sizeKb !== null && sizeKb !== undefined && (
            <span style={{ fontSize: 11, color: '#98A2B3' }}>{sizeKb} KB</span>
          )}
        </span>
      </span>
    </Tooltip>
  );
}

/**
 * 必填标记：设计稿要求「下一步动作 *」「下一步时间 *」在标签右侧带**红色星号**。
 * 不用 `required` 属性是因为它默认渲染在 label 左侧（与设计稿方向相反）。
 */
function requiredLabel(text: string) {
  return (
    <span>
      {text}
      <span style={{ color: '#F04438', marginLeft: 4 }}>*</span>
    </span>
  );
}

/** 蓝色 info 块（设计稿右下角的口径说明；等价于 .gate-banner 的 tip 态但更紧凑） */
function InfoBlock({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 8,
        padding: '12px 14px',
        background: '#F5F8FF',
        border: '1px solid #E4E7FF',
        borderRadius: 8,
        fontSize: 13,
        color: '#3538CD',
        lineHeight: 1.6,
      }}
    >
      <span style={{ flex: '0 0 auto', marginTop: 1 }}>
        <span
          style={{
            display: 'inline-flex',
            width: 16,
            height: 16,
            borderRadius: '50%',
            background: '#465FFF',
            color: '#fff',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 11,
            fontWeight: 700,
          }}
        >
          i
        </span>
      </span>
      <span>{children}</span>
    </div>
  );
}

/** 卡片内一行「标签 — 值」 */
function InfoRow({ label, children }: { label: React.ReactNode; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', padding: '5px 0' }}>
      <span style={{ flex: '0 0 104px', color: '#667085', fontSize: 13 }}>{label}</span>
      <span style={{ flex: 1, minWidth: 0, color: '#182230', fontSize: 13 }}>{children}</span>
    </div>
  );
}

export default function LeadDetail() {
  const { id } = useParams();
  const leadId = id ?? '';
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { dicts } = useDicts();
  const user = useAuthStore((s) => s.user);
  const isManager = user?.role === 'admin' || user?.role === 'market_manager';

  const [contactOpen, setContactOpen] = useState(false);
  const [editingContact, setEditingContact] = useState<Contact | null>(null);
  const [reviveOpen, setReviveOpen] = useState(false);
  const [activeTab, setActiveTab] = useState('validity');
  const [moreOpen, setMoreOpen] = useState(false);
  const [contactForm] = Form.useForm<Partial<Contact>>();
  const [followForm] = Form.useForm();
  const [reviveForm] = Form.useForm<{ remark: string }>();

  /** 复核表单「暂存」：本地草稿（不提交、不改变业务状态），避免长文本白填 */
  const [draftSavedAt, setDraftSavedAt] = useState<string | null>(null);
  const nextActionValue: string = Form.useWatch('next_action_text', followForm) ?? '';

  const { data, isLoading } = useQuery({
    queryKey: ['lead-detail', leadId],
    queryFn: () => getLeadDetail(leadId),
    //  这里原来写的是 `Number.isFinite(leadId)`：leadId 是 uuid 字符串，
    // Number.isFinite('abc…') 恒为 false → 查询被禁用 → data 永远为空 →
    // 页面卡在 <Spin /> 打不开（从 MVP 起就坏，直到真机截图才被发现）。
    enabled: Boolean(leadId),
  });

  const lead = data?.lead;
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['lead-detail', leadId] });
    qc.invalidateQueries({ queryKey: ['leads'] });
    qc.invalidateQueries({ queryKey: ['workbench'] });
  };

  /* ---- 联系人增删改 ---- */
  const saveContact = useMutation({
    mutationFn: (v: Partial<Contact>) =>
      editingContact ? updateContact(editingContact.id, v) : createContact(leadId, v),
    onSuccess: () => {
      message.success(editingContact ? '联系人已更新' : '联系人已新增');
      setContactOpen(false);
      setEditingContact(null);
      contactForm.resetFields();
      refresh();
    },
  });

  const removeContact = useMutation({
    mutationFn: (cid: string) => deleteContact(cid),
    onSuccess: () => {
      message.success('联系人已删除');
      refresh();
    },
  });

  /* ---- 新增跟进：后端自动判定有效 / 无效 ---- */
  const addFollowup = useMutation({
    mutationFn: (v: {
      method: string;
      followed_at: dayjs.Dayjs;
      content: string;
      is_valid: 'valid' | 'invalid';
      next_action: string;
      info_verified: 'yes' | 'no';
      next_follow_at?: dayjs.Dayjs | null;
    }) =>
      createFollowup(leadId, {
        method: v.method,
        followed_at: dayjs(v.followed_at).format('YYYY-MM-DD'), // 只记日期（本地），避免时区偏移
        content: v.content,
        is_valid: v.is_valid,
        next_action: v.next_action,
        // ⭐「客户信息是否核实正确」是判有效的前提（spec/02 §7），必须显式提交；
        //    后端不再默认成"已核实"，不传会被判为无效
        info_verified: v.info_verified === 'yes',
        next_follow_at: v.next_follow_at ? dayjs(v.next_follow_at).format('YYYY-MM-DD') : null,
      }),
    onSuccess: (r) => {
      if (r.validity?.is_valid) {
        message.success(`系统判定：有效商机（${r.validity.reason || '客户信息正确且有意愿'}）`);
      } else {
        message.warning(`系统判定：${r.validity?.reason || '暂不构成有效商机'}`);
      }
      followForm.resetFields();
      setDraftSavedAt(null);
      refresh();
    },
  });

  /* ---- 无效商机复活 ---- */
  const revive = useMutation({
    mutationFn: (v: { remark: string }) => reviveLead(leadId, v.remark),
    onSuccess: () => {
      message.success('已复活为「跟进中」');
      setReviveOpen(false);
      reviveForm.resetFields();
      refresh();
    },
  });

  /* ---- 公海重新领取 ---- */
  const claim = useMutation({
    mutationFn: () => claimLead(leadId),
    onSuccess: () => {
      message.success('领取成功，已归入我的线索');
      refresh();
    },
  });

  /** 拿到线索的有效跟进（后端判定为有效的最近一条） */
  const validFollowups = useMemo(
    () => (data?.followups ?? []).filter((f) => f.is_valid === 'valid'),
    [data?.followups],
  );
  const latestFollowup: FollowUp | null = data?.followups?.[0] ?? null;
  const decisionMakerMissing = useMemo(
    () => !hasDecisionMakerContact(data?.contacts),
    [data?.contacts],
  );
  /** 关键决策人：优先取职位命中关键词且有电话的联系人 */
  const decisionMaker = useMemo(
    () =>
      (data?.contacts ?? []).find(
        (c) =>
          !!c.phone?.trim() &&
          DECISION_KEYWORDS.some((k) => (c.position ?? '').includes(k) || (c.name ?? '').includes(k)),
      ) ?? null,
    [data?.contacts],
  );
  /** 主联系人（右栏「联系人信息」卡优先展示） */
  const primaryContact = useMemo(
    () => (data?.contacts ?? []).find((c) => c.is_primary) ?? (data?.contacts ?? [])[0] ?? null,
    [data?.contacts],
  );

  if (isLoading || !lead) {
    return (
      <Card>
        <Spin />
      </Card>
    );
  }

  const methodMap = toLabelMap(dicts.follow_methods);
  const actionMap = toLabelMap(dicts.next_actions);

  /** 自动初判：后端已写入的有效判定（可核对、可通过人工复核修正） */
  const autoIsValid = validFollowups.length > 0;
  const autoVerdictText = autoIsValid ? '建议：有效商机' : '尚未构成有效商机';
  const autoReason = latestFollowup
    ? `最近一次跟进（${fmtDate(latestFollowup.followed_at)}）判定为${
        latestFollowup.is_valid === 'valid' ? '有效' : '无效'
      }`
    : '还没有跟进记录，无法初判';

  /** SLA：首次跟进 / 回收剩余工作日（实时以后端下发的 deadline 为准） */
  const recycleLeft = workdaysUntil(lead.recycle_deadline);
  const recycleUrgent = recycleLeft !== null && recycleLeft <= 0;
  const firstFollowDeadline = lead.first_follow_deadline ?? null;
  const firstFollowLate =
    !!firstFollowDeadline && !lead.first_follow_at && dayjs(firstFollowDeadline).isBefore(dayjs());
  const slaAlert = firstFollowLate || recycleUrgent;

  const otherContacts = (data.contacts ?? []).filter((c) => c.id !== primaryContact?.id);

  const contactColumns: ColumnsType<Contact> = [
    { title: '姓名', dataIndex: 'name', width: 120 },
    { title: '职位', dataIndex: 'position', width: 120, render: (v) => v || '-' },
    {
      title: '电话',
      dataIndex: 'phone',
      width: 150,
      render: (v: string) => (v ? <Tooltip title="已脱敏展示">{maskPhone(v)}</Tooltip> : '-'),
    },
    { title: '微信', dataIndex: 'wechat', width: 140, render: (v) => v || '-' },
    {
      title: '主联系人',
      dataIndex: 'is_primary',
      width: 100,
      render: (v: boolean) => (v ? <Tag color="blue">主联系人</Tag> : '-'),
    },
    { title: '备注', dataIndex: 'remark', ellipsis: true, render: (v) => v || '-' },
    {
      title: '操作',
      key: 'op',
      width: 130,
      render: (_, r) => (
        <Space size={4}>
          <a
            onClick={() => {
              setEditingContact(r);
              contactForm.setFieldsValue(r);
              setContactOpen(true);
            }}
          >
            编辑
          </a>
          <Popconfirm title="确认删除该联系人？" onConfirm={() => removeContact.mutate(r.id)}>
            <a style={{ color: '#ff4d4f' }}>删除</a>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const logColumns: ColumnsType<OperationLog> = [
    { title: '时间', dataIndex: 'created_at', width: 160, render: (v) => fmtDateTime(v) },
    { title: '操作人', dataIndex: 'operator_name', width: 110, render: (v) => v || '-' },
    { title: '操作类型', dataIndex: 'action', width: 130 },
    { title: '对象', dataIndex: 'target_name', width: 160, render: (v) => v || '-' },
    { title: '变动内容', dataIndex: 'detail', ellipsis: true, render: (v) => v || '-' },
    { title: 'IP', dataIndex: 'ip', width: 130, render: (v) => v || '-' },
  ];

  /**
   * 抓取复核表单 → 组装成后端既有字段。
   *
   * 提交字段：method、followed_at、content、is_valid、next_action、info_verified、
   * next_follow_at；
   * `is_valid` 由「信息是否核实正确 + 是否有意愿」两项推导，
   * `next_action` 取勾选的「意愿依据」（后端 INTENT_ACTIONS 只认汇报交流 / 微信交流看我方资料）。
   * 「下一步动作」文本框是复核必填的**人看的内容**，与既有 `content` 一并留痕。
   */
  const submitReview = (v: {
    method: string;
    followed_at: string;
    content: string;
    info_verified: 'yes' | 'no';
    has_intent: 'yes' | 'no';
    intent_basis?: string[];
    next_action_text: string;
    next_follow_at?: string | null;
  }) => {
    const is_valid: 'valid' | 'invalid' =
      v.info_verified === 'yes' && v.has_intent === 'yes' ? 'valid' : 'invalid';
    const basis = v.intent_basis?.[0] ?? 'report_meeting';
    const nextActionText = (v.next_action_text ?? '').trim();
    const content = nextActionText ? `${v.content}\n下一步动作：${nextActionText}` : v.content;
    addFollowup.mutate({
      method: v.method,
      followed_at: dayjs(v.followed_at),
      content,
      is_valid,
      next_action: basis,
      // 「客户信息是否核实正确」必须显式提交（后端不再默认 true）
      info_verified: v.info_verified,
      next_follow_at: v.next_follow_at ? dayjs(v.next_follow_at) : null,
    });
  };

  /**
   * 复核 / 跟进表单的字段块。**同一份字段定义供两处复用**（跟进记录 Tab 的「新增跟进」
   * 与验证与判定 Tab 的「人工复核」），避免两处表单漂移。
   */
  const sharedFieldItems = (
    <>
      {/* 跟进方式 / 跟进时刻：两处表单都必须带上（否则切 Tab 后回退值缺字段）。
          用 ISO 字符串存储，切换 Tab 时不会因 dayjs 对象失效而丢时间。 */}
      <Form.Item name="method" hidden>
        <Input />
      </Form.Item>
      <Form.Item name="followed_at" hidden>
        <Input />
      </Form.Item>
      <Form.Item label={requiredLabel('跟进内容')} style={{ marginBottom: 12 }}>
        <Form.Item
          name="content"
          noStyle
          rules={[{ required: true, message: '请填写跟进内容（判定依据要留痕）' }]}
        >
          <Input.TextArea
            rows={2}
            maxLength={NEXT_ACTION_MAX}
            showCount
            placeholder="沟通对象、沟通结果、客户关注点（如：已核实学校信息，刘主任同意微信接收产品资料）"
          />
        </Form.Item>
      </Form.Item>
      <Form.Item
        name="info_verified"
        label={requiredLabel('客户信息是否核实正确')}
        rules={[{ required: true, message: '请选择' }]}
      >
        <Radio.Group>
          <Radio value="yes">是</Radio>
          <Radio value="no">否</Radio>
        </Radio.Group>
      </Form.Item>
      <Form.Item
        name="has_intent"
        label={requiredLabel('是否有意愿')}
        rules={[{ required: true, message: '请选择' }]}
      >
        <Radio.Group>
          <Radio value="yes">是</Radio>
          <Radio value="no">否</Radio>
        </Radio.Group>
      </Form.Item>
      <Form.Item
        name="intent_basis"
        label="意愿依据"
        extra="勾选任一项即视为「有意愿」——与后端判定同口径（汇报交流 / 微信交流看我方资料）。"
      >
        <Select
          mode="multiple"
          allowClear
          style={{ maxWidth: 480 }}
          placeholder="可多选"
          options={INTENT_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
        />
      </Form.Item>
      <Row gutter={12}>
        <Col xs={24} md={12}>
          <Form.Item name="method_visible" label="跟进方式" rules={[{ required: true, message: '请选择跟进方式' }]}>
            <Select
              options={dicts.follow_methods ?? []}
              onChange={(val) => followForm.setFieldValue('method', val)}
            />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item
            name="followed_at_visible"
            label="跟进日期"
            rules={[{ required: true, message: '请选择跟进日期' }]}
            extra="只记到日期：销售是按天回忆与汇报的，要求填时分秒只会让人瞎填"
          >
            <DatePicker
              style={{ width: '100%' }}
              // 跟进时间按日期记录，不记录时分秒。
              //    统一按当天 00:00 存，避免同一天的记录在时间线上乱序。
              //  必须提交**本地日期字符串**，不能用 toISOString()：
              //    北京时间 3/31 00:00 → UTC 3/30 16:00，后端按原值存 → 显示差一天
              onChange={(val) =>
                followForm.setFieldValue('followed_at', (val ?? dayjs()).format('YYYY-MM-DD'))
              }
            />
          </Form.Item>
        </Col>
      </Row>
      <Form.Item
        name="next_action_text"
        label={requiredLabel('下一步动作')}
        rules={[{ required: true, message: '请写明下一步动作（复核必填）' }]}
      >
        <Input.TextArea
          rows={3}
          maxLength={NEXT_ACTION_MAX}
          showCount
          placeholder="例：添加微信发送资料，预约 10 月 9 日方案交流。"
        />
      </Form.Item>
      <Form.Item
        name="next_follow_at"
        label={requiredLabel('下一步时间')}
        rules={[{ required: true, message: '请选择下一步时间' }]}
        extra="必填：系统按它排提醒；不填只能靠 SLA 兜底，容易超时。"
      >
        <DatePicker style={{ width: '100%' }} placeholder="请选择日期" />
      </Form.Item>
    </>
  );

  /**
   * 复核 / 跟进共用的初始值。
   * `followed_at` 存 ISO 字符串（不存 dayjs 对象）：切 Tab 后表单被卸载重建时不会丢时间。
   * `*_visible` 是给用户看的控件名，变更时同步写回上面的真实字段。
   */
  const followInitialValues = {
    method: 'phone',
    method_visible: 'phone',
    followed_at: dayjs().format('YYYY-MM-DD'), // 默认今天（只到日期）
    followed_at_visible: dayjs(),
    content: latestFollowup?.content ?? '',
    info_verified: 'yes',
    has_intent: 'yes',
    intent_basis: ['report_meeting'],
    next_action_text: '',
  };

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title={lead.name}
        demo
        tags={
          <Space size={6} wrap>
            <LeadStatusTag status={lead.status} />
            {lead.is_suspect_duplicate && (
              <Tooltip title="与已有线索在「客户名称 + 省 / 市 / 区」上撞单，待人工裁决">
                <Tag color="orange">疑似重复</Tag>
              </Tooltip>
            )}
            {lead.is_deleted && <Tag color="red">已删除</Tag>}
          </Space>
        }
        meta={[
          { value: <><EnvironmentOutlined style={{ color: '#98A2B3', marginRight: 4 }} />{lead.org_type_label || lead.org_type || '-'}</> },
          { value: <><EnvironmentOutlined style={{ color: '#98A2B3', marginRight: 4 }} />{[lead.province, lead.city, lead.district].filter(Boolean).join(' ') || '-'}</> },
        ]}
        actions={
          <Space wrap>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => {
                setActiveTab('validity');
                setTimeout(() => {
                  document
                    .getElementById('lead-followup-form')
                    ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }, 0);
              }}
            >
              新增跟进
            </Button>
            <Button onClick={() => setMoreOpen(true)}>…</Button>
          </Space>
        }
      />

      {/*  SLA 通栏：首次跟进（工作日）｜ 回收剩余 N 工作日 */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 28,
          padding: '16px 22px',
          background: slaAlert ? '#FEF7EC' : '#F5F8FF',
          border: `1px solid ${slaAlert ? '#FBE2BF' : '#E4E7FF'}`,
          borderRadius: 8,
        }}
      >
        <Space size={10}>
          {lead.first_follow_at ? (
            <>
              <CheckCircleFilled style={{ color: SUCCESS, fontSize: 22 }} />
              <Typography.Text strong style={{ fontSize: 15 }}>
                首次跟进已完成
              </Typography.Text>
            </>
          ) : (
            <>
              <WarningFilled
                style={{ color: firstFollowLate ? '#F04438' : WARNING, fontSize: 22 }}
              />
              <Typography.Text strong style={{ fontSize: 15 }}>
                首次跟进{firstFollowLate ? '已超时' : '待完成'}
              </Typography.Text>
              <FollowCountdown
                assignAt={lead.assign_at}
                firstFollowAt={lead.first_follow_at}
                deadline={lead.first_follow_deadline}
              />
            </>
          )}
        </Space>
        <span style={{ width: 1, height: 22, background: '#D8DDF5' }} />
        <Space size={10}>
          <ClockCircleOutlined
            style={{ color: recycleUrgent ? '#F04438' : WARNING, fontSize: 20 }}
          />
          <Typography.Text
            strong
            style={{ fontSize: 15, color: recycleUrgent ? '#F04438' : WARNING }}
          >
            {recycleLeft === null
              ? '回收时限未下发'
              : recycleLeft <= 0
                ? '已到回收时限'
                : `回收剩余 ${recycleLeft} 工作日`}
          </Typography.Text>
        </Space>
      </div>

      <Card styles={{ body: { paddingTop: 12 } }}>
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: 'basic',
              label: '基本信息',
              children: (
                <Descriptions column={{ xs: 1, md: 2, xl: 3 }} size="small" bordered>
                  <Descriptions.Item label="来源">
                    <SourceTag source={lead.source} />
                  </Descriptions.Item>
                  <Descriptions.Item label="客户类型">
                    <OrgTypeTag orgType={lead.org_type} />
                  </Descriptions.Item>
                  <Descriptions.Item label="区域">
                    {[lead.province, lead.city, lead.district].filter(Boolean).join(' / ') || '-'}
                  </Descriptions.Item>
                  <Descriptions.Item label="归属人">{lead.owner_name || '未指派'}</Descriptions.Item>
                  <Descriptions.Item label="创建人">{lead.creator_name || '-'}</Descriptions.Item>
                  <Descriptions.Item label="创建时间">{fmtDateTime(lead.created_at)}</Descriptions.Item>
                  <Descriptions.Item label="指派时间">{fmtDateTime(lead.assign_at)}</Descriptions.Item>
                  <Descriptions.Item label="首次跟进">{fmtDate(lead.first_follow_at)}</Descriptions.Item>
                  <Descriptions.Item label="首次跟进时限">
                    <FollowCountdown
                      assignAt={lead.assign_at}
                      firstFollowAt={lead.first_follow_at}
                      deadline={lead.first_follow_deadline}
                    />
                  </Descriptions.Item>
                  <Descriptions.Item label="最近跟进">{fmtDate(lead.last_follow_at)}</Descriptions.Item>
                  <Descriptions.Item label="预算金额（元）">{fenToYuan(lead.budget_amount_fen)}</Descriptions.Item>
                  <Descriptions.Item label="加权预测（万）">{fenToWan(lead.weighted_amount_fen)}</Descriptions.Item>
                  <Descriptions.Item label="当前环节">
                    {lead.current_stage
                      ? toLabelMap(dicts.stages)[lead.current_stage] ??
                        lead.current_stage_label ??
                        lead.current_stage
                      : '-'}
                  </Descriptions.Item>
                  <Descriptions.Item label="环节停留">
                    {lead.stage_stay_days === null || lead.stage_stay_days === undefined
                      ? '-'
                      : `${lead.stage_stay_days} 天（自然日）`}
                    {lead.stage_overdue && (
                      <Tag color="red" style={{ marginLeft: 6 }}>
                        超期
                      </Tag>
                    )}
                  </Descriptions.Item>
                  <Descriptions.Item label="成交概率">{fmtPercent(lead.win_probability)}</Descriptions.Item>
                  <Descriptions.Item label="风险标记" span={2}>
                    <RiskTags types={lead.risk_types} />
                  </Descriptions.Item>
                  <Descriptions.Item label="报备单位/介绍人">
                    {[lead.channel_unit, lead.referrer].filter(Boolean).join(' / ') || '-'}
                  </Descriptions.Item>
                  <Descriptions.Item label="备注" span={2}>
                    {lead.remark || '-'}
                  </Descriptions.Item>
                </Descriptions>
              ),
            },
            {
              key: 'contacts',
              label: `联系人`,
              children: (
                <>
                  <Space style={{ marginBottom: 12 }} wrap>
                    <Button
                      type="primary"
                      size="small"
                      icon={<PlusOutlined />}
                      onClick={() => {
                        setEditingContact(null);
                        contactForm.resetFields();
                        setContactOpen(true);
                      }}
                    >
                      新增联系人
                    </Button>
                    {decisionMakerMissing && (
                      <Typography.Text style={{ fontSize: 12, color: WARNING }}>
                        还没有带电话/微信的关键决策人（校长 / 主任 / 局长等）——推进到环节二前必须补上。
                      </Typography.Text>
                    )}
                  </Space>
                  <Table
                    rowKey="id"
                    size="small"
                    columns={contactColumns}
                    dataSource={data.contacts ?? []}
                    pagination={false}
                    locale={{
                      emptyText: (
                        <NextStepGuide
                          compact
                          title="暂无联系人"
                          description="至少登记一个能拍板的人（姓名 + 直接电话/微信），否则环节一 → 环节二会被卡住。"
                          actions={
                            <Button
                              size="small"
                              type="primary"
                              onClick={() => {
                                setEditingContact(null);
                                contactForm.resetFields();
                                setContactOpen(true);
                              }}
                            >
                              新增联系人
                            </Button>
                          }
                        />
                      ),
                    }}
                  />
                </>
              ),
            },
            {
              key: 'followups',
              label: `跟进记录`,
              children: (
                <Row gutter={16}>
                  <Col xs={24} lg={10}>
                    <Card size="small" title="新增跟进" styles={{ body: { paddingTop: 12 } }}>
                      <Form
                        form={followForm}
                        layout="vertical"
                        initialValues={followInitialValues}
                        onFinish={submitReview}
                      >
                        {sharedFieldItems}
                        <Space wrap>
                          <Button type="primary" htmlType="submit" loading={addFollowup.isPending}>
                            确认有效商机
                          </Button>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            提交即留痕；有效性由后端按「信息正确 + 有意愿」判定，接口口径不变。
                          </Typography.Text>
                        </Space>
                      </Form>
                    </Card>
                  </Col>
                  <Col xs={24} lg={14}>
                    {!data.followups?.length ? (
                      <NextStepGuide
                        title="暂无跟进记录"
                        description="第一次跟进既是 SLA 要求，也是「有效商机」判定的依据。左侧填一条即可。"
                      />
                    ) : (
                      <Timeline
                        items={data.followups.map((f) => ({
                          color: f.is_valid === 'valid' ? 'green' : 'gray',
                          children: (
                            <Space direction="vertical" size={2}>
                              <Space wrap>
                                <Typography.Text strong>{f.user_name}</Typography.Text>
                                <Tag>{methodMap[f.method] ?? f.method}</Tag>
                                <Tag color={f.is_valid === 'valid' ? 'success' : 'default'}>
                                  {f.is_valid === 'valid' ? '有效' : '无效'}
                                </Tag>
                                {f.source === 'ai_confirmed' && <Tag color="purple">AI 生成·已确认</Tag>}
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                  {fmtDate(f.followed_at)}
                                </Typography.Text>
                              </Space>
                              <Typography.Text>{f.content}</Typography.Text>
                              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                下一步：{actionMap[f.next_action] ?? (f.next_action || '-')}
                                {f.next_follow_at ? ` · 下次跟进 ${fmtDate(f.next_follow_at)}` : ''}
                              </Typography.Text>
                            </Space>
                          ),
                        }))}
                      />
                    )}
                  </Col>
                </Row>
              ),
            },
            {
              key: 'validity',
              label: '验证与判定',
              children: (
                <Row gutter={[16, 16]}>
                  {/* ═════════ 左 2/3：最新跟进记录 + 人工复核 ═════════ */}
                  <Col xs={24} xl={16}>
                    <Space direction="vertical" size={16} style={{ width: '100%' }}>
                      <Card size="small" title="最新跟进记录" styles={{ body: { paddingTop: 12 } }}>
                        {latestFollowup ? (
                          <>
                            <div
                              style={{
                                display: 'flex',
                                gap: 14,
                                padding: '14px 16px',
                                background: '#F9FAFB',
                                borderRadius: 8,
                              }}
                            >
                              <MethodAvatar method={latestFollowup.method} />
                              <div style={{ flex: 1, minWidth: 0 }}>
                                <Space size={16} wrap style={{ marginBottom: 6 }}>
                                  <Typography.Text>{fmtDate(latestFollowup.followed_at)}</Typography.Text>
                                  <Typography.Text>
                                    {methodMap[latestFollowup.method] ?? latestFollowup.method}
                                  </Typography.Text>
                                  <Typography.Text>{latestFollowup.user_name}</Typography.Text>
                                </Space>
                                <Typography.Paragraph style={{ marginBottom: 10 }}>
                                  {latestFollowup.content}
                                </Typography.Paragraph>
                                <Space wrap size={8}>
                                  <FileChip
                                    name="跟进记录（可核对）"
                                    tone="pdf"
                                    hint="跟进记录即本轮有效性判定的证据：点开可看全部跟进"
                                    onClick={() => setActiveTab('followups')}
                                  />
                                  {latestFollowup.next_follow_at && (
                                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                      下次跟进 {fmtDate(latestFollowup.next_follow_at)}
                                    </Typography.Text>
                                  )}
                                </Space>
                              </div>
                            </div>
                            <div style={{ marginTop: 10 }}>
                              <a onClick={() => setActiveTab('followups')}>查看全部跟进记录 →</a>
                            </div>
                          </>
                        ) : (
                          <NextStepGuide
                            compact
                            title="还没有跟进记录"
                            description="没有跟进就没有有效性判定的依据 —— 右栏「自动初判」也无法给出建议。先新增一条跟进。"
                            actions={
                              <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => setActiveTab('followups')}>
                                去新增跟进
                              </Button>
                            }
                          />
                        )}
                      </Card>

                      <div id="lead-followup-form">
                        <Card
                          size="small"
                          title="人工复核"
                          extra={<PanelFlag>复核提交后才生效</PanelFlag>}
                          styles={{ body: { paddingTop: 12 } }}
                        >
                          <Form
                            form={followForm}
                            layout="horizontal"
                            labelCol={{ flex: '128px' }}
                            wrapperCol={{ flex: 1 }}
                            initialValues={followInitialValues}
                            onFinish={submitReview}
                          >
                            {sharedFieldItems}
                            <div
                              style={{
                                display: 'flex',
                                flexWrap: 'wrap',
                                alignItems: 'center',
                                justifyContent: 'flex-end',
                                gap: 12,
                                marginTop: 4,
                              }}
                            >
                              {draftSavedAt && (
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                  草稿已暂存在本页（{draftSavedAt}），刷新会清空
                                </Typography.Text>
                              )}
                              <Button
                                onClick={() => {
                                  setDraftSavedAt(dayjs().format('HH:mm:ss'));
                                  message.success('已暂存草稿（本地，未提交、不改变线索状态）');
                                }}
                              >
                                暂存
                              </Button>
                              <Tooltip
                                title={
                                  nextActionValue.trim()
                                    ? undefined
                                    : '「下一步动作」是复核必填项：写清下一步做什么，系统才能按它和 SLA 盯人'
                                }
                              >
                                <span>
                                  <Button type="primary" loading={addFollowup.isPending} onClick={() => followForm.submit()}>
                                    确认有效商机
                                  </Button>
                                </span>
                              </Tooltip>
                            </div>
                          </Form>
                        </Card>
                      </div>
                    </Space>
                  </Col>

                  {/* ═════════ 右 1/3：自动初判 + 联系人信息 + 口径 info ═════════ */}
                  <Col xs={24} xl={8}>
                    <Space direction="vertical" size={16} style={{ width: '100%' }}>
                      <Card size="small" title="自动初判" styles={{ body: { paddingTop: 12 } }}>
                        <Space direction="vertical" size={10} style={{ width: '100%' }}>
                          <div
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: 12,
                              padding: '16px 16px',
                              background: autoIsValid ? '#E7F8F0' : '#FEF4E6',
                              borderRadius: 8,
                            }}
                          >
                            {autoIsValid ? (
                              <CheckCircleFilled style={{ color: SUCCESS, fontSize: 26 }} />
                            ) : (
                              <WarningFilled style={{ color: WARNING, fontSize: 26 }} />
                            )}
                            <div style={{ minWidth: 0 }}>
                              <div
                                style={{
                                  fontSize: 19,
                                  fontWeight: 700,
                                  color: autoIsValid ? '#027A48' : '#B54708',
                                }}
                              >
                                {autoVerdictText}
                              </div>
                              <Typography.Text style={{ fontSize: 12, color: '#667085' }}>
                                {autoIsValid ? '信息正确 + 有意愿' : autoReason}
                              </Typography.Text>
                            </div>
                          </div>
                          <div
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: 8,
                              padding: '10px 14px',
                              background: '#FEF7EC',
                              border: '1px solid #FBE2BF',
                              borderRadius: 8,
                              fontSize: 13,
                              color: '#B54708',
                            }}
                          >
                            <WarningFilled style={{ color: WARNING }} />
                            待人工复核，尚未生效。
                          </div>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            判定口径：客户信息正确 + 有意愿（同意下一步汇报交流 / 微信交流看我方资料）
                            → 有效商机 = 环节一。自动初判只给建议，最终以人工复核提交为准，可核对、可修正。
                          </Typography.Text>
                        </Space>
                      </Card>

                      <Card size="small" title="联系人信息" styles={{ body: { paddingTop: 12 } }}>
                        {primaryContact ? (
                          <>
                            <InfoRow label="姓名">{primaryContact.name}</InfoRow>
                            <InfoRow label="职位">{primaryContact.position || '-'}</InfoRow>
                            <InfoRow label="手机">
                              {primaryContact.phone ? (
                                <Tooltip title="界面按脱敏展示，编辑联系人可看完整号码">
                                  <span>{maskPhone(primaryContact.phone)}</span>
                                </Tooltip>
                              ) : (
                                <Typography.Text style={{ color: WARNING, fontSize: 13 }}>
                                  未填
                                </Typography.Text>
                              )}
                            </InfoRow>
                            <InfoRow label="微信">
                              {primaryContact.wechat ? (
                                <Space size={6}>
                                  <CheckCircleFilled style={{ color: SUCCESS }} />
                                  <span>已添加</span>
                                </Space>
                              ) : (
                                <Typography.Text style={{ color: WARNING, fontSize: 13 }}>
                                  未获取
                                </Typography.Text>
                              )}
                            </InfoRow>
                            <Divider style={{ margin: '10px 0' }} />
                          </>
                        ) : (
                          <>
                            <NextStepGuide
                              compact
                              title="暂无联系人"
                              description="登记一个能拍板的人（姓名 + 直接电话/微信），否则环节一 → 环节二会被卡住。"
                              actions={
                                <Button
                                  size="small"
                                  type="primary"
                                  onClick={() => {
                                    setEditingContact(null);
                                    contactForm.resetFields();
                                    setContactOpen(true);
                                  }}
                                >
                                  新增联系人
                                </Button>
                              }
                            />
                            <Divider style={{ margin: '10px 0' }} />
                          </>
                        )}
                        <InfoRow label="关键决策人联系方式">
                          {decisionMaker ? (
                            <Space size={6} wrap>
                              <CheckCircleFilled style={{ color: SUCCESS }} />
                              <span>
                                {decisionMaker.name}（{decisionMaker.position || '职位未填'}）
                                {decisionMaker.phone ? ` · ${maskPhone(decisionMaker.phone)}` : ''}
                              </span>
                            </Space>
                          ) : (
                            <Space size={6}>
                              <WarningFilled style={{ color: WARNING }} />
                              <span style={{ color: WARNING }}>尚未获取</span>
                            </Space>
                          )}
                        </InfoRow>
                        {decisionMakerMissing && (
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            口径：带电话 / 微信、且职位命中（校长 / 主任 / 局长等）才算「关键决策人」。
                          </Typography.Text>
                        )}
                        {otherContacts.length > 0 && (
                          <div style={{ marginTop: 8 }}>
                            <Space wrap size={6}>
                              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                其他联系人 {otherContacts.length} 位：
                              </Typography.Text>
                              {otherContacts.map((c) => (
                                <Tag key={c.id} style={{ marginInlineEnd: 0 }}>
                                  {c.name}
                                  {c.position ? `·${c.position}` : ''}
                                </Tag>
                              ))}
                              <a onClick={() => setActiveTab('contacts')}>查看全部</a>
                            </Space>
                          </div>
                        )}
                      </Card>

                      {/*  设计稿右下角的蓝色 info 块：就地讲清"卡在哪、本次不受影响" */}
                      <InfoBlock>
                        有效商机进入环节一；推进至环节二前仍须补齐关键决策人直接联系方式。
                      </InfoBlock>
                    </Space>
                  </Col>

                  {/* 相关待办提醒 + 推进前置提示 */}
                  <Col span={24}>
                    {decisionMakerMissing && lead.status !== 'signed' && lead.status !== 'lost' && (
                      <GateBanner
                        tone="tip"
                        style={{ marginBottom: 12 }}
                        title="尚未获取关键决策人直接联系方式 —— 不阻止本次验证，但会阻止「环节一 → 环节二」推进"
                        items={[
                          '本次有效性复核可以照常提交；',
                          '推进到环节二（客户触达）前，必须补上能拍板的人（校长 / 主任 / 局长等）的姓名 + 直接电话或微信。',
                        ]}
                        action={
                          <Space wrap>
                            <Button
                              size="small"
                              type="primary"
                              onClick={() => {
                                setEditingContact(null);
                                contactForm.resetFields();
                                setContactOpen(true);
                              }}
                            >
                              去补联系方式
                            </Button>
                            <Button size="small" onClick={() => setActiveTab('contacts')}>
                              查看联系人
                            </Button>
                          </Space>
                        }
                      />
                    )}
                    <Divider orientation="left" plain>
                      相关待办提醒
                    </Divider>
                    {!data.reminders?.length ? (
                      <NextStepGuide
                        compact
                        title="暂无提醒"
                        description="首次跟进超时、即将回收、下次跟进到点时会自动出现在这里，也会进工作台待办。"
                      />
                    ) : (
                      <Table
                        rowKey="id"
                        size="small"
                        pagination={false}
                        dataSource={data.reminders}
                        columns={[
                          {
                            title: '类型',
                            dataIndex: 'type',
                            width: 140,
                            render: (v: string) => <ReminderTag type={v} />,
                          },
                          { title: '内容', dataIndex: 'content' },
                          {
                            title: '提醒时间',
                            dataIndex: 'remind_at',
                            width: 160,
                            render: (v: string) => fmtDateTime(v),
                          },
                          {
                            title: '状态',
                            dataIndex: 'status',
                            width: 90,
                            render: (v: string) =>
                              v === 'done' ? (
                                <Tag color="success">已处理</Tag>
                              ) : (
                                <Tag color="orange">待处理</Tag>
                              ),
                          },
                        ]}
                      />
                    )}
                  </Col>
                </Row>
              ),
            },
            {
              key: 'logs',
              label: '分配与操作日志',
              children: (
                <>
                  <Divider orientation="left" plain>
                    分配 / 改派 / 回收记录
                  </Divider>
                  <Table
                    rowKey="id"
                    size="small"
                    style={{ marginBottom: 16 }}
                    pagination={false}
                    dataSource={data.assignments ?? []}
                    locale={{
                      emptyText: (
                        <NextStepGuide
                          compact
                          title="暂无分配记录"
                          description="这条线索还没被分配过；分配后 1 个工作日内需首次跟进，7 个工作日未跟进自动回收（均按工作日）。"
                          actions={
                            <Button size="small" onClick={() => navigate('/assign')}>
                              去分配管理
                            </Button>
                          }
                        />
                      ),
                    }}
                    columns={[
                      {
                        title: '动作',
                        dataIndex: 'action',
                        width: 110,
                        render: (v: string) => <Tag>{ASSIGN_ACTION_LABEL[v] ?? v}</Tag>,
                      },
                      {
                        title: '原归属人',
                        dataIndex: 'from_owner_name',
                        width: 120,
                        render: (v: string | null) => v || '-',
                      },
                      {
                        title: '新归属人',
                        dataIndex: 'to_owner_name',
                        width: 120,
                        render: (v: string | null) => v || '-',
                      },
                      { title: '操作人', dataIndex: 'operator_name', width: 120, render: (v) => v || '-' },
                      { title: '备注', dataIndex: 'remark', ellipsis: true, render: (v) => v || '-' },
                      {
                        title: '时间',
                        dataIndex: 'created_at',
                        width: 160,
                        render: (v: string) => fmtDateTime(v),
                      },
                    ]}
                  />
                  <Divider orientation="left" plain>
                    操作日志
                  </Divider>
                  <Table
                    rowKey="id"
                    size="small"
                    pagination={false}
                    columns={logColumns}
                    dataSource={data.logs ?? []}
                    locale={{
                      emptyText: (
                        <NextStepGuide
                          compact
                          title="暂无操作日志"
                          description="线索被编辑、分配、回收后这里会逐条留痕。"
                        />
                      ),
                    }}
                  />
                </>
              ),
            },
          ]}
        />
      </Card>

      {/* KPI：本页三个关键数（口径见页脚） */}
      <Row gutter={[12, 12]}>
        <Col xs={24} sm={12} lg={6}>
          <KpiCard
            label="有效跟进记录"
            value={validFollowups.length}
            total={`/ ${data.followups?.length ?? 0} 条`}
            icon={<CheckCircleFilled />}
            tone={autoIsValid ? 'green' : 'gray'}
            hint="有效 = 后端按「客户信息正确 + 有意愿」判定为有效的跟进记录条数"
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <KpiCard
            label="回收剩余工作日"
            value={recycleLeft === null ? '未下发' : recycleLeft}
            suffix={recycleLeft === null ? undefined : '工作日'}
            icon={<ClockCircleOutlined />}
            tone={recycleUrgent ? 'red' : 'brand'}
            danger={recycleUrgent}
            hint="以最近一次跟进（无则按指派时间）起算的 7 个工作日回收时限；实时以后端下发截止时间为准"
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <KpiCard
            label="联系人"
            value={data.contacts?.length ?? 0}
            icon={<PaperClipOutlined />}
            tone={decisionMakerMissing ? 'amber' : 'green'}
            hint="已登记的联系人条数；有带电话/微信的关键决策人才算拿到决策人联系方式"
            rateText={decisionMakerMissing ? '缺关键决策人' : '已有关键决策人'}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <KpiCard
            label="停留时长"
            value={lead.stage_stay_days ?? 0}
            suffix="自然日"
            icon={<ClockCircleOutlined />}
            tone={lead.stage_overdue ? 'red' : 'gray'}
            danger={lead.stage_overdue}
            hint="商机环节停留按自然日计时；SLA（首次跟进 / 回收）按工作日计时"
          />
        </Col>
      </Row>

      <Footnote>
        有效商机进入环节一；推进至环节二前仍须补齐关键决策人直接联系方式（否则环节一 → 环节二会被卡住，本次验证不受影响）。
        有效性判定口径 = 客户信息正确 + 有意愿（S1-4），结果由后端自动写入、可核对。SLA：指派后 1 个工作日内首次跟进，
        7 个工作日未跟进自动回收——两项均按工作日计算；商机环节停留按自然日计算。手机号按脱敏展示（138****6208）。
        分配权在管理者，系统不自动推荐负责人；AI 生成的跟进 / 判定均为草稿，需人工确认后才生效，不作为考核依据。
      </Footnote>

      {/* 「…」更多：把低频操作收进一处（设计稿里的更多菜单） */}
      <Modal
        title="更多操作"
        open={moreOpen}
        footer={null}
        onCancel={() => setMoreOpen(false)}
      >
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          {lead.status === 'unassigned' && (
            <Button block onClick={() => claim.mutate()} loading={claim.isPending}>
              领取到我的（公海线索）
            </Button>
          )}
          {lead.status === 'invalid' && (
            <Button block onClick={() => setReviveOpen(true)}>
              复活为跟进中
            </Button>
          )}
          <Button block onClick={() => navigate(`/leads/${leadId}/edit`)}>
            编辑线索
          </Button>
          <Button block onClick={() => navigate('/assign')}>
            去分配管理
          </Button>
          <Button block onClick={() => navigate('/leads')}>
            返回线索池
          </Button>
          <Button
            block
            onClick={() => {
              setMoreOpen(false);
              setActiveTab('logs');
            }}
          >
            查看分配与操作日志
          </Button>
          {lead.status === 'unassigned' && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              领取后按「指派」计 SLA：1 个工作日内首次跟进，7 个工作日未跟进自动回收。
            </Typography.Text>
          )}
        </Space>
      </Modal>

      {/* 联系人弹窗 */}
      <Modal
        title={editingContact ? '编辑联系人' : '新增联系人'}
        open={contactOpen}
        onCancel={() => {
          setContactOpen(false);
          setEditingContact(null);
        }}
        onOk={() => contactForm.submit()}
        confirmLoading={saveContact.isPending}
        destroyOnClose
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="关键决策人请填职位（如 校长 / 教务主任 / 信息中心主任）"
          description="系统按职位关键词识别是否已获取关键决策人直接联系方式；这决定环节一 → 环节二能不能推进。"
        />
        <Form form={contactForm} layout="vertical" onFinish={(v) => saveContact.mutate(v)}>
          <Form.Item name="name" label="姓名" rules={[{ required: true, message: '请输入姓名' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="position" label="职位">
            <Input placeholder="如：校长 / 教务主任" />
          </Form.Item>
          <Form.Item name="phone" label="电话" rules={[{ required: true, message: '请输入电话' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="wechat" label="微信">
            <Input />
          </Form.Item>
          <Form.Item name="is_primary" label="设为主联系人" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 无效商机复活 */}
      <Modal
        title="无效商机复活"
        open={reviveOpen}
        onCancel={() => setReviveOpen(false)}
        onOk={() => reviveForm.submit()}
        confirmLoading={revive.isPending}
        destroyOnClose
      >
        <Form form={reviveForm} layout="vertical" onFinish={(v) => revive.mutate(v)}>
          <Form.Item name="remark" label="复活原因" rules={[{ required: true, message: '请填写复活原因' }]}>
            <Input.TextArea rows={3} placeholder="如：客户主动联系，重新评估需求" />
          </Form.Item>
        </Form>
      </Modal>

      {isManager && (
        <Tooltip title="软删除在线索列表页操作；删除后数据保留但不再参与分配与看板统计。">
          <Alert
            type="warning"
            showIcon
            message="管理员操作提示"
            description="软删除线索请前往列表页操作，删除后数据保留但不再参与分配与看板统计。"
          />
        </Tooltip>
      )}
    </Space>
  );
}
