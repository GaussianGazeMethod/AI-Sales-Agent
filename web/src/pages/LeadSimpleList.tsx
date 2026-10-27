import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, Input, Space, Table, Tabs, Tag } from 'antd';
import { useNavigate } from 'react-router-dom';
import { listLeads } from '@/api/leads';
import { SourceTag } from '@/components/tags';
import PageHeader from '@/components/PageHeader';
import type { Lead, LeadQuery } from '@/types';

type Tab = 'active' | 'pool' | 'invalid' | 'converted';

export default function LeadSimpleList() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>('active');
  const [keyword, setKeyword] = useState('');
  const [page, setPage] = useState(1);
  const params: LeadQuery = {
    view: tab === 'pool' ? 'pool' : tab === 'invalid' ? 'invalid' : 'mine',
    lifecycle: tab === 'converted' ? 'opportunity' : 'lead',
    active_only: tab === 'active',
    keyword, page, page_size: 20,
  };
  const { data, isLoading } = useQuery({ queryKey: ['simple-leads', params], queryFn: () => listLeads(params) });
  return <Space direction="vertical" size={20} style={{ width: '100%' }}>
    <PageHeader title="线索" subtitle="记录客户，建立联系，确认推进意愿。" actions={<Space wrap><Button onClick={() => navigate('/leads/new')}>填写四项资料</Button><Button type="primary" onClick={() => navigate('/ai/chat')}>告诉 AI 新线索</Button></Space>} />
    <Card>
      <Tabs activeKey={tab} onChange={value => { setTab(value as Tab); setPage(1); }} items={[
        { key: 'active', label: '我的待验证线索' }, { key: 'pool', label: '公海线索' },
        { key: 'invalid', label: '无效线索' }, { key: 'converted', label: '已转商机' },
      ]} />
      <Input.Search placeholder="搜索客户名称" allowClear onSearch={value => { setKeyword(value); setPage(1); }} style={{ maxWidth: 360, marginBottom: 20 }} />
      <Table<Lead> rowKey="id" loading={isLoading} dataSource={data?.items} pagination={{ current: page, pageSize: 20, total: data?.total, onChange: setPage }} columns={[
        { title: '客户', dataIndex: 'name', render: (name, lead) => <Button type="link" style={{ padding: 0 }} onClick={() => navigate(`/leads/${lead.id}`)}>{name}</Button> },
        { title: '联系人', render: (_, lead) => [lead.contact_name, lead.contact_position].filter(Boolean).join(' · ') || '待补充' },
        { title: '来源', dataIndex: 'source', render: source => <SourceTag source={source} /> },
        { title: '负责人', dataIndex: 'owner_name', render: name => name || '待领取' },
        { title: '最近联系', dataIndex: 'last_follow_at', render: value => value?.slice(0, 10) || '尚未联系' },
        { title: '状态', render: (_, lead) => <Tag color={lead.current_stage ? 'green' : lead.status === 'invalid' ? 'default' : 'blue'}>{lead.current_stage ? '已转商机' : lead.status === 'invalid' ? '无效，可重新验证' : '待验证'}</Tag> },
        { title: '下一步', render: (_, lead) => <>{lead.next_action || '待安排'}{lead.next_action && <small style={{ display: 'block', color: '#8994a5' }}>{lead.next_action_at?.slice(0, 10) || '时间待确认'}</small>}</> },
        { title: '', render: (_, lead) => <Button onClick={() => navigate(lead.current_stage ? `/opportunities/${lead.id}` : `/leads/${lead.id}`)}>{lead.current_stage ? '进入商机' : '查看线索'}</Button> },
      ]} />
    </Card>
  </Space>;
}
