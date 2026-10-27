import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  Card,
  Button,
  Col,
  Form,
  Input,
  Modal,
  Radio,
  Row,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { listDuplicates, resolveDuplicate } from '@/api/leads';
import { fmtDateTime } from '@/utils/format';
import { LeadStatusTag, SourceTag } from '@/components/tags';
import GateBanner from '@/components/GateBanner';
import PageHeader from '@/components/PageHeader';
import { Footnote, NextStepGuide } from '@/components/Guide';
import type { Lead } from '@/types';

/** 1.7 疑似重复裁决台：成对展示，操作「保留 / 合并」 */
export default function Duplicates() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [active, setActive] = useState<Lead | null>(null);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<{ action: 'keep' | 'merge'; keep_id: string; remark: string }>();

  const { data, isLoading } = useQuery({
    queryKey: ['lead-duplicates'],
    queryFn: listDuplicates,
  });

  const resolve = useMutation({
    mutationFn: (v: { keep_id: string; merge_id: string; action: 'keep' | 'merge'; remark: string }) =>
      resolveDuplicate(v),
    onSuccess: () => {
      message.success('裁决完成');
      setOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ['lead-duplicates'] });
      qc.invalidateQueries({ queryKey: ['leads'] });
    },
  });

  const items = data?.items ?? [];

  // 按「客户名称 + 省市区」分组，便于成对展示
  const groups = useMemo(() => {
    const map = new Map<string, Lead[]>();
    items.forEach((l) => {
      const key = `${l.name}|${l.province}|${l.city}|${l.district}`;
      map.set(key, [...(map.get(key) ?? []), l]);
    });
    return Array.from(map.entries()).map(([key, list]) => ({ key, list }));
  }, [items]);

  const columns: ColumnsType<Lead> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    { title: '客户名称', dataIndex: 'name', ellipsis: true },
    { title: '来源', dataIndex: 'source', width: 110, render: (v) => <SourceTag source={v} /> },
    {
      title: '区域',
      key: 'area',
      width: 180,
      render: (_, r) => [r.province, r.city, r.district].filter(Boolean).join(' / ') || '-',
    },
    {
      title: '归属人',
      dataIndex: 'owner_name',
      width: 110,
      render: (v: string | null) => v || '未指派',
    },
    { title: '状态', dataIndex: 'status', width: 100, render: (v) => <LeadStatusTag status={v} /> },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 160,
      render: (v: string) => fmtDateTime(v),
    },
    {
      title: '操作',
      key: 'op',
      width: 100,
      render: (_, r) => (
        <a
          onClick={() => {
            setActive(r);
            setOpen(true);
          }}
        >
          裁决
        </a>
      ),
    },
  ];

  /** 组内除当前外的候选合并对象 */
  const pairCandidates = (lead: Lead | null) =>
    lead
      ? items.filter(
          (i) =>
            i.id !== lead.id &&
            i.name === lead.name &&
            i.province === lead.province &&
            i.city === lead.city &&
            i.district === lead.district,
        )
      : [];

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title="疑似重复裁决台"
        subtitle="逐组裁决「保留 / 合并」——裁决前先看两边客户信息，裁决必须填备注（留痕）；系统不会自动合并。"
        demo
        tags={
          <Space size={6} wrap>
            <Tag color="orange">疑似重复 {items.length} 条</Tag>
            <Tag>分组 {groups.length} 组</Tag>
          </Space>
        }
        actions={
          <Space wrap>
            <Button onClick={() => navigate('/leads?view=duplicate')}>在列表中查看</Button>
            <Button onClick={() => navigate('/leads?view=unassigned')}>去未指派池</Button>
          </Space>
        }
      />

      <Card>
      {isLoading ? (
        <Spin />
      ) : items.length === 0 ? (
        <NextStepGuide
          title="暂无疑似重复线索"
          description="录入或导入时按「客户名称 + 省 / 市 / 区」实时查重，撞单的线索会进入这里等待人工裁决。现在没有待处理的撞单。"
          actions={
            <Space wrap>
              <Button type="primary" onClick={() => navigate('/leads/new')}>
                新增线索
              </Button>
              <Button onClick={() => navigate('/leads')}>去线索池</Button>
            </Space>
          }
        />
      ) : (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          {groups.map((g) => (
            <Card
              key={g.key}
              size="small"
              title={
                <Space>
                  <Typography.Text strong>{g.list[0]?.name}</Typography.Text>
                  <Typography.Text type="secondary">
                    {[g.list[0]?.province, g.list[0]?.city, g.list[0]?.district]
                      .filter(Boolean)
                      .join(' / ')}
                  </Typography.Text>
                  <Tag color="orange">{g.list.length} 条</Tag>
                </Space>
              }
            >
              <Table
                rowKey="id"
                size="small"
                columns={columns}
                dataSource={g.list}
                pagination={false}
                scroll={{ x: 900 }}
              />
            </Card>
          ))}
        </Space>
      )}

      <Modal
        title="疑似重复裁决"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={resolve.isPending}
        destroyOnClose
        width={620}
      >
        <GateBanner
          tone="tip"
          style={{ marginBottom: 12 }}
          title="两种裁决的区别（都会留痕，可在操作日志里查）"
          items={[
            '保留 = 两条都保留，只解除「疑似重复」标记，各自归属与跟进记录都不动；',
            '合并 = 保留你指定的那一条，另一条（及人员/跟进信息）并入后软删除；误操作请在操作日志里追溯。',
          ]}
        />
        <Form
          form={form}
          layout="vertical"
          initialValues={{ action: 'keep', keep_id: active?.id }}
          onFinish={(v) =>
            active &&
            resolve.mutate({
              keep_id: v.keep_id,
              merge_id: pairCandidates(active)[0]?.id ?? active.id,
              action: v.action,
              remark: v.remark,
            })
          }
        >
          <Row gutter={12}>
            <Col span={12}>
              <Typography.Text type="secondary">当前线索</Typography.Text>
              <Typography.Paragraph>
                #{active?.id} {active?.name}（归属人：{active?.owner_name || '未指派'}）
              </Typography.Paragraph>
            </Col>
            <Col span={12}>
              <Typography.Text type="secondary">同组候选</Typography.Text>
              <Typography.Paragraph>
                {pairCandidates(active)
                  .map((c) => `#${c.id} ${c.name}（${c.owner_name || '未指派'}）`)
                  .join('、') || '无'}
              </Typography.Paragraph>
            </Col>
          </Row>
          <Form.Item name="action" label="裁决动作" rules={[{ required: true }]}>
            <Radio.Group>
              <Radio value="keep">保留（两条都保留，仅解除重复标记）</Radio>
              <Radio value="merge">合并（客户资料合并到保留的那条）</Radio>
            </Radio.Group>
          </Form.Item>
          <Form.Item name="keep_id" label="保留哪条" rules={[{ required: true, message: '请选择保留的线索' }]}>
            <Radio.Group>
              {(active ? [active, ...pairCandidates(active)] : []).map((l) => (
                <Radio key={l.id} value={l.id}>
                  #{l.id} {l.name}（{l.owner_name || '未指派'}）
                </Radio>
              ))}
            </Radio.Group>
          </Form.Item>
          <Form.Item name="remark" label="裁决备注" rules={[{ required: true, message: '请填写裁决备注' }]}>
            <Input.TextArea rows={2} placeholder="如：确认为同一客户，保留最早录入的一条" />
          </Form.Item>
        </Form>
      </Modal>

      <Footnote style={{ marginTop: 12 }}>
        查重口径 = 客户名称相同 + 省 / 市 / 区相同（或名称高度相似）；只提示、不自动合并。
        裁决动作（保留 / 合并）与备注全部写入操作日志，可在每条线索详情的「分配与操作日志」里追溯。
        判为「保留」的两条线索各自独立跟进；判为「合并」的另一条会被软删除（数据保留，不再参与分配与看板统计）。
      </Footnote>
      </Card>
    </Space>
  );
}
