import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Descriptions, Empty, Space, Spin, Tag, Timeline } from 'antd';
import { getLeadDetail, claimLead } from '@/api/leads';
import { SourceTag } from '@/components/tags';
import PageHeader from '@/components/PageHeader';
export default function LeadSimpleDetail() {
  const { id } = useParams(); const navigate = useNavigate();
  const { data, isLoading, refetch } = useQuery({ queryKey: ['lead', id], queryFn: () => getLeadDetail(id!) });
  if (isLoading) return <Spin />; if (!data) return <Empty />;
  const lead = data.lead; const converted = !!lead.current_stage; const status = converted ? '已转商机' : lead.status === 'invalid' ? '无效，可重新验证' : '待验证';
  return <Space direction="vertical" size={20} style={{ width: '100%' }}><PageHeader title={lead.name} tags={<Space><Tag color={converted ? 'green' : 'blue'}>{status}</Tag><SourceTag source={lead.source} /></Space>} actions={<Space wrap>{!lead.owner_id && <Button type="primary" onClick={async () => { await claimLead(id!); await refetch(); }}>领取线索</Button>}<Button type="primary" disabled={!lead.owner_id} onClick={() => navigate(converted ? `/opportunities/${id}` : `/ai/chat?lead=${id}`)}>{converted ? '进入商机' : '告诉 AI 联系结果'}</Button></Space>} />{converted && <Alert type="success" showIcon message="线索验证已完成，后续项目进度在商机中推进。" />}
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(300px,1fr))', gap: 20 }}><Card title="基本资料"><Descriptions column={1}><Descriptions.Item label="负责人">{lead.owner_name || '待分配'}</Descriptions.Item><Descriptions.Item label="来源"><SourceTag source={lead.source} /></Descriptions.Item>{data.contacts.map(c => <Descriptions.Item key={c.id} label="联系人">{c.name} · {c.position || '职位待确认'}</Descriptions.Item>)}<Descriptions.Item label="最近联系">{lead.last_follow_at?.replace('T', ' ') || '尚未联系'}</Descriptions.Item>{!converted && <Descriptions.Item label="回收安排">{lead.appointment_waiting ? `客户约定时间待到期：${lead.next_action_at?.replace('T', ' ') || '见待办'}` : lead.recycle_deadline?.replace('T', ' ') || '按首次联系与跟进规则计算'}</Descriptions.Item>}</Descriptions></Card><Card title="下一步"><p>{lead.next_action || '联系结果直接发给 AI，系统整理验证依据并承接下一步行动。'}</p><Space wrap><Button onClick={() => navigate(`/ai/chat?lead=${id}`)}>继续对话</Button><Button onClick={() => navigate('/todos')}>查看待办</Button></Space></Card></div>
    <Card title={converted ? '转化前联系记录' : '联系记录'}>{data.followups.filter(f => !converted || !f.stage).length ? <Timeline items={data.followups.filter(f => !converted || !f.stage).map(f => ({ children: <><small>{f.followed_at.replace('T', ' ')} · {f.user_name}</small><p style={{ whiteSpace: 'pre-wrap' }}>{f.content}</p></> }))} /> : <Empty description="尚无联系记录" />}</Card></Space>;
}
