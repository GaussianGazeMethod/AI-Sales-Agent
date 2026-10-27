import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Button, Card, Input, Space, Table, Tabs } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { globalSearch } from '@/api/dashboard';
import PageHeader from '@/components/PageHeader';
import { Footnote, NextStepGuide } from '@/components/Guide';
import { LeadStatusTag, SourceTag } from '@/components/tags';
import { fmtDateTime } from '@/utils/format';
import type { Contact, Lead } from '@/types';

/**
 * 0.5 全局搜索：客户 / 联系人 / 电话（顶栏搜索框跳转而来）。
 *
 * 判据对照：C1 少填（顶栏关键词直接带过来，不用重敲）；C2 空态给下一步入口；
 * C3 说明搜索范围受数据权限限制。
 */
export default function Search() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const keyword = params.get('keyword') ?? '';
  const [value, setValue] = useState(keyword);

  useEffect(() => setValue(keyword), [keyword]);

  const { data, isLoading } = useQuery({
    queryKey: ['global-search', keyword],
    queryFn: () => globalSearch(keyword),
    enabled: Boolean(keyword),
  });

  const leadCount = data?.leads?.length ?? 0;
  const contactCount = data?.contacts?.length ?? 0;

  const leadColumns: ColumnsType<Lead> = [
    {
      title: '客户名称',
      dataIndex: 'name',
      render: (v: string, r) => <a onClick={() => navigate(`/leads/${r.id}`)}>{v}</a>,
    },
    { title: '来源', dataIndex: 'source', width: 120, render: (v) => <SourceTag source={v} /> },
    {
      title: '区域',
      key: 'area',
      width: 200,
      render: (_, r) => [r.province, r.city, r.district].filter(Boolean).join(' / ') || '-',
    },
    {
      title: '归属人',
      dataIndex: 'owner_name',
      width: 110,
      render: (v: string | null) => v || '未指派',
    },
    { title: '状态', dataIndex: 'status', width: 110, render: (v) => <LeadStatusTag status={v} /> },
    {
      title: '操作',
      key: 'op',
      width: 100,
      render: (_, r) => (
        <Space size={8}>
          <a onClick={() => navigate(`/leads/${r.id}`)}>查看线索</a>
        </Space>
      ),
    },
  ];

  const contactColumns: ColumnsType<Contact> = [
    { title: '姓名', dataIndex: 'name', width: 140 },
    { title: '职位', dataIndex: 'position', width: 140, render: (v) => v || '-' },
    { title: '电话', dataIndex: 'phone', width: 150 },
    { title: '微信', dataIndex: 'wechat', width: 150, render: (v) => v || '-' },
    {
      title: '所属客户',
      dataIndex: 'lead_id',
      width: 160,
      render: (v: string) => (v ? <a onClick={() => navigate(`/leads/${v}`)}>查看所属客户</a> : '-'),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 160,
      render: (v: string) => fmtDateTime(v),
    },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <PageHeader
        title="全局搜索"
        subtitle="按客户名称 / 联系人 / 电话搜索；搜索范围受你的数据权限限制。"
        meta={keyword ? [{ label: '关键词', value: keyword }] : [{ label: '关键词', value: '未输入' }]}
        actions={
          <Input.Search
            style={{ width: 320 }}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onSearch={(v) => setParams(v ? { keyword: v } : {})}
            placeholder="客户名称 / 联系人 / 电话"
            allowClear
            enterButton
          />
        }
      />

      <Card>
        {!keyword ? (
          <NextStepGuide
            title="请输入关键词开始搜索"
            description="可以搜客户名称、联系人姓名或电话。搜索不到时：客户可能不在你的数据范围内（市场专员仅能搜到本人负责的线索），可去线索池按来源/区域筛选。"
            actions={
              <Space>
                <Button type="primary" onClick={() => navigate('/leads')}>
                  去线索池
                </Button>
                <Button onClick={() => navigate('/opportunities')}>去商机列表</Button>
              </Space>
            }
          />
        ) : (
          <Tabs
            items={[
              {
                key: 'leads',
                label: `客户（${leadCount}）`,
                children: (
                  <Table
                    rowKey="id"
                    size="small"
                    loading={isLoading}
                    columns={leadColumns}
                    dataSource={data?.leads ?? []}
                    pagination={false}
                    locale={{
                      emptyText: (
                        <NextStepGuide
                          title="未找到匹配客户"
                          description="换个关键词（如只输入学校名的两个字）再试；也可能是该客户不在你的数据权限范围内。"
                          actions={
                            <Button size="small" onClick={() => navigate('/leads')}>
                              去线索池查看全部
                            </Button>
                          }
                        />
                      ),
                    }}
                  />
                ),
              },
              {
                key: 'contacts',
                label: `联系人（${contactCount}）`,
                children: (
                  <Table
                    rowKey="id"
                    size="small"
                    loading={isLoading}
                    columns={contactColumns}
                    dataSource={data?.contacts ?? []}
                    pagination={false}
                    locale={{
                      emptyText: (
                        <NextStepGuide
                          title="未找到匹配联系人"
                          description="联系人维护在客户详情页；可先搜到客户，再到详情里补充联系人。"
                          actions={
                            <Button size="small" onClick={() => navigate('/leads')}>
                              去线索池
                            </Button>
                          }
                        />
                      ),
                    }}
                  />
                ),
              },
            ]}
          />
        )}
      </Card>

      <Footnote>
        ⓘ 口径：搜索范围受数据权限限制 —— 市场专员仅能搜到本人负责的线索，管理人员可搜全部；
        联系人只返回归属你可见客户的记录。AI 与预警不参与搜索排序。
      </Footnote>
    </Space>
  );
}
