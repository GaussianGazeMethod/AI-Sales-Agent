import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Button, Card, List, Space, Spin, Tag, Typography } from 'antd';
import { NotificationOutlined } from '@ant-design/icons';
import { listNotifications } from '@/api/dashboard';
import PageHeader from '@/components/PageHeader';
import { Footnote, NextStepGuide } from '@/components/Guide';
import { fmtDateTime } from '@/utils/format';

/**
 * 0.4 通知中心：站内消息列表（预留 IM 推送）。
 *
 * 判据对照：C2 空态给「下一步」；C3 说明通知来源与未读口径；C4 通知只做提醒，不代替人做对外动作。
 */
export default function NotificationsPage() {
  const navigate = useNavigate();
  const { data, isLoading } = useQuery({
    queryKey: ['notifications'],
    queryFn: listNotifications,
  });

  const unread = (data?.items ?? []).filter((n) => !n.is_read).length;

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <PageHeader
        title="通知中心"
        subtitle="系统通知与站内提醒；点击标题可跳到对应业务页面处理。"
        meta={[
          { label: '共', value: `${data?.total ?? 0} 条` },
          { label: '未读', value: `${unread} 条` },
        ]}
      />

      <Card>
        {isLoading ? (
          <Spin />
        ) : !data?.items?.length ? (
          <NextStepGuide
            title="暂无通知"
            description="通知由系统在预警、分配、审核等事件触发时生成（如卡单、待确认草稿、目标达成预警）。想主动看需要你处理的事，去待办中心或工作台。"
            actions={
              <Space>
                <Button type="primary" onClick={() => navigate('/todos')}>
                  去待办中心
                </Button>
                <Button onClick={() => navigate('/workbench')}>回工作台</Button>
              </Space>
            }
          />
        ) : (
          <List
            dataSource={data.items}
            renderItem={(n) => (
              <List.Item>
                <List.Item.Meta
                  avatar={<NotificationOutlined style={{ color: '#465FFF', fontSize: 18 }} />}
                  title={
                    <Space wrap size={6}>
                      <Typography.Text strong>{n.title}</Typography.Text>
                      {!n.is_read && <Tag color="red">未读</Tag>}
                      <Tag>{n.type}</Tag>
                    </Space>
                  }
                  description={
                    <Space direction="vertical" size={2}>
                      <Typography.Text>{n.content}</Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {fmtDateTime(n.created_at)}
                      </Typography.Text>
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Card>

      <Footnote>
        ⓘ 口径：未读 = 尚未在系统内打开处理的通知；通知只做提醒，不会代替你给客户发消息，也不作为考核依据。
      </Footnote>
    </Space>
  );
}
