import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { useAuthStore } from '@/store/auth';
import {
  AutoComplete,
  Button,
  Card,
  Col,
  Divider,
  Form,
  Input,
  Row,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { checkDuplicate, createLead, getLeadDetail, updateLead } from '@/api/leads';
import { useDicts } from '@/hooks/useDicts';
import { LeadStatusTag } from '@/components/tags';
import GateBanner from '@/components/GateBanner';
import PageHeader from '@/components/PageHeader';
import { Footnote } from '@/components/Guide';
import type { DuplicateBrief, Lead, LeadCreateBody } from '@/types';

type FormValues = LeadCreateBody;

/** 1.3 新增 / 编辑线索：来源必填 + 录入过程实时查重（防抖） */
export default function LeadForm() {
  const { id } = useParams();
  /** 市场专员：录入即归属本人 */
  const isSpecialist = useAuthStore((s) => s.user?.role) === 'market_staff';
  const isEdit = Boolean(id);
  const leadId = id || undefined;
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [form] = Form.useForm<FormValues>();
  const { dicts } = useDicts();
  const [duplicates, setDuplicates] = useState<DuplicateBrief[]>([]);
  const [checking, setChecking] = useState(false);
  const timer = useRef<number | null>(null);
  const seq = useRef(0);

  const { data: detail, isLoading } = useQuery({
    queryKey: ['lead', leadId],
    // 同一缓存键保留完整详情结构，表单只选择其中的线索字段。
    queryFn: () => getLeadDetail(leadId as string),
    select: (data) => data.lead,
    enabled: isEdit,
  });

  // 编辑态回填
  useEffect(() => {
    if (detail) {
      form.setFieldsValue({
        name: detail.name,
        project_name: detail.project_name ?? undefined,
        source: detail.source,
        province: detail.province,
        city: detail.city,
        district: detail.district,
        org_type: detail.org_type,
        remark: detail.remark,
        channel_unit: detail.channel_unit,
        referrer: detail.referrer,
      });
    }
  }, [detail, form]);

  /** 实时查重：客户名称 + 省市区，输入停止 500ms 后触发 */
  const scheduleCheck = () => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      const v = form.getFieldsValue();
      if (!v.name || (!v.province && !v.city && !v.district)) {
        setDuplicates([]);
        return;
      }
      const current = ++seq.current;
      setChecking(true);
      try {
        const resp = await checkDuplicate({
          name: v.name,
          province: v.province,
          city: v.city,
          district: v.district,
        });
        // 只接受最后一次请求的结果，避免旧响应覆盖
        if (current === seq.current) {
          setDuplicates(resp.suspect ? resp.duplicates : []);
        }
      } catch {
        // 查重失败不阻塞录入
      } finally {
        if (current === seq.current) setChecking(false);
      }
    }, 500);
  };

  /** 新增：后端返回 lead + 撞单提示 */
  const create = useMutation({
    mutationFn: (v: FormValues) => createLead(v),
    onSuccess: (resp) => {
      if (resp.duplicate_warning?.suspect) {
        message.warning(
          `撞单提示：已有归属人 ${resp.duplicate_warning.duplicates?.[0]?.owner_name || '（未指派）'}，已标记疑似重复`,
        );
      } else {
        message.success(
          isSpecialist
            ? '线索已创建并归属你本人：请在 1 个工作日内完成首次跟进'
            : '线索创建成功，已进入「未指派」池待分配',
        );
      }
      navigate(`/leads/${resp.lead.id}`);
    },
  });

  /** 编辑：只更新字段 */
  const edit = useMutation({
    mutationFn: (v: Partial<Lead>) => updateLead(leadId as string, v),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['lead', leadId] });
      await qc.invalidateQueries({ queryKey: ['opportunity-detail', leadId] });
      message.success('线索已更新');
      navigate(`/leads/${leadId}`);
    },
  });

  const submitting = create.isPending || edit.isPending;

  const dupColumns: ColumnsType<DuplicateBrief> = [
    { title: '客户名称', dataIndex: 'name' },
    {
      title: '归属人',
      dataIndex: 'owner_name',
      width: 110,
      render: (v: string | null) => v || '未指派',
    },
    {
      title: '区域',
      key: 'area',
      width: 180,
      render: (_, r) => [r.province, r.city, r.district].filter(Boolean).join(' / ') || '-',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (v: string) => <LeadStatusTag status={v} />,
    },
    {
      title: '操作',
      key: 'op',
      width: 80,
      render: (_, r) => <a onClick={() => navigate(`/leads/${r.id}`)}>查看</a>,
    },
  ];

  const provinceOptions = useMemo(
    () => (dicts.provinces ?? []).map((p) => ({ value: p })),
    [dicts.provinces],
  );

  if (isEdit && isLoading) {
    return (
      <Card>
        <Spin />
      </Card>
    );
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title={isEdit ? '编辑线索' : '新增线索'}
        subtitle="录入即查重（客户名称 + 省市区）——查重只提示、不拦截；带 * 的必填，其余可留空后补。"
        demo
        actions={
          <Space wrap>
            <Button onClick={() => navigate('/imports')}>改用 Excel 导入</Button>
            {leadId && (
              <Button onClick={() => navigate(`/leads/${leadId}`)}>返回线索详情</Button>
            )}
          </Space>
        }
      />

      <Card>
      {duplicates.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <GateBanner
            tone="tip"
            title={`撞单提示：已有 ${duplicates.length} 条同名 / 同区域客户（首条归属人：${
              duplicates[0].owner_name || '未指派'
            }）`}
            items={[
              '可以继续提交——查重只提示、不拦截；',
              '提交后这条线索会被标记「疑似重复」，由管理人员在裁决台逐条裁决（保留 / 合并）；',
              '如果是同一个客户，建议先联系现归属人确认，避免重复跟进。',
            ]}
            action={
              <Space wrap>
                <Button size="small" onClick={() => navigate('/duplicates')}>
                  去疑似重复裁决台
                </Button>
                <Button size="small" onClick={() => navigate('/leads?view=duplicate')}>
                  在列表中查看
                </Button>
              </Space>
            }
          />
          <Table
            rowKey="id"
            size="small"
            style={{ marginTop: 8 }}
            pagination={false}
            columns={dupColumns}
            dataSource={duplicates}
          />
        </div>
      )}

      <Form
        form={form}
        layout="vertical"
        onFinish={(v) => (isEdit ? edit.mutate(v as Partial<Lead>) : create.mutate(v))}
        onValuesChange={scheduleCheck}
      >
        <Divider orientation="left" plain>
          客户基本信息
        </Divider>
        <Row gutter={16}>
          <Col xs={24} md={8}>
            <Form.Item
              name="name"
              label="客户名称"
              rules={[{ required: true, message: '请输入客户名称' }]}
            >
              <Input placeholder="学校 / 教育局名称" />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            {/*
              项目名（选填）：客户-项目两层模型。同一客户可以有多个项目商机
              （不同预算年度 / 资金来源 / 功能点各算一个项目），用项目名区分；
              留空则该商机只显示客户名。不设必填，避免挡住"先录进来再说"。
            */}
            <Form.Item
              name="project_name"
              label="项目名（选填）"
              extra="同一客户可有多个项目，如「教学平台试点项目」；留空则只显示客户名。"
            >
              <Input placeholder="如：AI 阅卷系统一期 / 明年预算申报项目" />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            <Form.Item
              name="source"
              label="来源"
              rules={[{ required: true, message: '来源必填' }]}
            >
              <Select placeholder="请选择来源" options={dicts.lead_sources ?? []} />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            <Form.Item
              name="org_type"
              label="客户类型"
              rules={[{ required: true, message: '请选择客户类型' }]}
            >
              <Select placeholder="请选择类型" options={dicts.org_types ?? []} />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            <Form.Item
              name="province"
              label="省份"
              rules={[{ required: true, message: '请输入省份' }]}
            >
              <AutoComplete
                placeholder="可输入，也可从列表选择"
                options={provinceOptions}
                filterOption={(input, option) =>
                  String(option?.value ?? '').includes(input)
                }
              />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            <Form.Item name="city" label="城市" rules={[{ required: true, message: '请输入城市' }]}>
              <Input placeholder="城市" />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            <Form.Item
              name="district"
              label="区县"
              rules={[{ required: true, message: '请输入区县' }]}
            >
              <Input placeholder="区县" />
            </Form.Item>
          </Col>
        </Row>

        <Divider orientation="left" plain>
          联系人信息
        </Divider>
        <Row gutter={16}>
          <Col xs={24} md={6}>
            <Form.Item name="contact_name" label="联系人姓名">
              <Input placeholder="联系人姓名" />
            </Form.Item>
          </Col>
          <Col xs={24} md={6}>
            <Form.Item
              name="contact_phone"
              label="联系人电话"
              rules={[{ pattern: /^\d{6,20}$/, message: '电话格式不正确' }]}
            >
              <Input placeholder="手机号 / 座机" />
            </Form.Item>
          </Col>
          <Col xs={24} md={6}>
            <Form.Item name="contact_wechat" label="联系人微信">
              <Input placeholder="微信号" />
            </Form.Item>
          </Col>
          <Col xs={24} md={6}>
            <Form.Item name="contact_position" label="联系人职位">
              <Input placeholder="如：校长 / 教务主任" />
            </Form.Item>
          </Col>
        </Row>

        <Divider orientation="left" plain>
          来源补充与备注
        </Divider>
        <Row gutter={16}>
          <Col xs={24} md={8}>
            <Form.Item name="channel_unit" label="报备单位（来源=渠道商时填写）">
              <Input placeholder="渠道商 / 合作单位名称" />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            <Form.Item name="referrer" label="报备人 / 介绍人">
              <Input placeholder="内部介绍人姓名" />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            <Form.Item name="remark" label="备注">
              <Input placeholder="补充说明" />
            </Form.Item>
          </Col>
        </Row>

        <Form.Item style={{ marginBottom: 0 }}>
          <Space wrap>
            <Button type="primary" htmlType="submit" loading={submitting || checking}>
              {isEdit ? '保存' : '提交'}
            </Button>
            <Button onClick={() => navigate(-1)}>取消</Button>
            {checking && <Tag color="processing">正在查重…</Tag>}
            {!checking && duplicates.length > 0 && <Tag color="warning">疑似重复 {duplicates.length} 条</Tag>}
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {isEdit
                ? '只提交改动过的字段；来源与客户类型必填，其余可留空后补。'
                : isSpecialist
                  ? '提交后**直接归属你本人**（无需管理者再分配），并从此刻起按工作日计时：1 个工作日内首次跟进，7 个工作日未跟进自动回收。'
                  : '你录入的线索会进入「未指派」池，由管理者分配；分配后 1 个工作日内需首次跟进（按工作日）。'}
            </Typography.Text>
          </Space>
        </Form.Item>
      </Form>

      <Footnote style={{ marginTop: 12 }}>
        查重口径 = 「客户名称 + 省 / 市 / 区」，录入时实时比对（输入停止约 0.5 秒后触发）。
        查重只提示、不拦截提交：疑似重复的线索仍会保存，并进入疑似重复裁决台等待人工裁决。
        来源共 8 类（公司客户池 / 客户转介绍 / 渠道商 / 内部人员介绍 / 协会·机构 / 市场活动 / 公开数据平台 / 运营商）；
        「报备单位」用于渠道商场景、「报备人 / 介绍人」用于内部介绍场景，按实际来源填写即可。
        指派后 1 个工作日内需首次跟进、7 个工作日未跟进自动回收（均按工作日计）。
        「客户名称 + 项目名」是两层：客户（学校 / 教育局）会按名称自动建档，同一个客户名下的多条线索就是它的多个项目，
        各自走一遍 7 环节、各有金额，客户整体价值在「客户」页面统一看。
      </Footnote>
      </Card>
    </Space>
  );
}
