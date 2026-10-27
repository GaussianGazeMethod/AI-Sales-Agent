import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Collapse, Drawer, Empty, Skeleton, Tag, Typography } from 'antd';
import { assistant } from '@/api/assistant';
import FactList, { factLabels } from './ProjectFacts';
import { fmtDateTime } from '@/utils/format';

export default function ProjectDossierDrawer({ leadId, open, onClose }: {
  leadId: string; open: boolean; onClose: () => void;
}) {
  const query = useQuery({
    queryKey: ['assistant-dossier-details', leadId],
    queryFn: () => assistant.dossier(leadId, true),
    enabled: open && !!leadId,
    retry: false,
  });
  const data = query.data;
  return <Drawer title={`${data?.customer || '项目'} · 档案与来源证据`}
    width={680} open={open} onClose={onClose}>
    {query.isPending ? <Skeleton active /> : query.isError ? <Alert type="error"
      message="档案读取失败" action={<Button onClick={() => query.refetch()}>重试</Button>} /> :
      data ? <>
        <Tag>档案修订 {data.revision ?? '尚未建立'}</Tag>
        <FactList facts={data.facts} />
        <Typography.Title level={5}>字段来源与历史依据</Typography.Title>
        <Collapse items={Object.entries(data.evidence ?? {}).filter(([key]) => factLabels[key]).map(
          ([key, proof]) => ({ key, label: factLabels[key], children: <>
            <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>
              {proof.quote || '未留存来源原文'}
            </Typography.Paragraph>
            <Typography.Text type="secondary">业务时间：{fmtDateTime(proof.occurred_at)}</Typography.Text>
            {proof.confirmation && <Card size="small" style={{ marginTop: 8 }}>
              <Typography.Text type="secondary">最近再次确认 · {fmtDateTime(proof.confirmed_at)}</Typography.Text>
              <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{proof.confirmation.quote}</Typography.Paragraph>
            </Card>}
            {proof.superseded?.map((old, index) => <Card size="small" key={index} style={{ marginTop: 8 }}>
              <Typography.Text type="secondary">被取代的历史值</Typography.Text>
              <FactList facts={{ [key]: old.value }} />
              <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{old.quote}</Typography.Paragraph>
              <Typography.Text type="secondary">{fmtDateTime(old.occurred_at)}</Typography.Text>
            </Card>)}
          </> }),
        )} />
        <Typography.Title level={5}>事实变更记录</Typography.Title>
        {data.history?.length ? data.history.map(event => <Card key={event.id} size="small"
          style={{ marginBottom: 10 }}>
          <Typography.Text type="secondary">
            发生于 {fmtDateTime(event.occurred_at)} · 记录于 {fmtDateTime(event.recorded_at)}
          </Typography.Text>
          <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{event.content}</Typography.Paragraph>
          {event.changes && <FactList facts={event.changes} />}
        </Card>) : <Empty description="尚无事实变更记录" />}
        <Typography.Title level={5}>关联跟进原文</Typography.Title>
        <Collapse items={(data.followups ?? []).map((followup, index) => ({
          key: `${followup.key}-${index}`,
          label: `${followup.key} · ${fmtDateTime(followup.occurred_at)}`,
          children: <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>
            {followup.content}
          </Typography.Paragraph>,
        }))} />
      </> : <Empty description="尚无项目档案" />}
  </Drawer>;
}
