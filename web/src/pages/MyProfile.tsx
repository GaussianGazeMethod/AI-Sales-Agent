import { useMutation, useQuery } from '@tanstack/react-query';
import { Card, Col, Descriptions, Form, Input, Row, Space, Tag, Typography, Button, message } from 'antd';
import { changePassword, fetchMe } from '@/api/auth';
import PageHeader from '@/components/PageHeader';
import { Footnote } from '@/components/Guide';
import { useAuthStore } from '@/store/auth';
import { useDicts } from '@/hooks/useDicts';
import { ROLE_LABEL } from '@/utils/constants';
import { fmtDateTime, toLabelMap } from '@/utils/format';

/**
 * 7.3 账号设置：改密码 + 个人信息。
 *
 * 判据对照：C2 说清「哪些字段自己改不了、该找谁」；C3 说明角色/数据范围的含义（影响你能看到哪些数据）。
 */
export default function MyProfile() {
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const { dicts } = useDicts();
  const [form] = Form.useForm<{ old_password: string; new_password: string; confirm: string }>();

  // 拉取最新用户信息，保证页面展示与后端一致
  useQuery({
    queryKey: ['auth-me'],
    queryFn: async () => {
      const me = await fetchMe();
      setUser(me);
      return me;
    },
    retry: false,
  });

  const change = useMutation({
    mutationFn: (v: { old_password: string; new_password: string }) => changePassword(v),
    onSuccess: () => {
      message.success('密码修改成功，请牢记新密码');
      form.resetFields();
    },
  });

  const roleMap = toLabelMap(dicts.roles);
  const scopeMap = toLabelMap(dicts.data_scopes);

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <PageHeader
        title="个人中心"
        subtitle="查看账号信息与修改密码；姓名、角色、部门由管理员维护。"
        meta={[
          { label: '账号', value: user?.username ?? '-' },
          { label: '角色', value: roleMap[user?.role ?? ''] ?? ROLE_LABEL[user?.role ?? ''] ?? user?.role ?? '-' },
          { label: '数据范围', value: scopeMap[user?.data_scope ?? ''] ?? user?.data_scope ?? '-' },
        ]}
      />

      <Row gutter={[12, 12]}>
        <Col xs={24} lg={12}>
          <Card title="个人信息">
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="账号">{user?.username ?? '-'}</Descriptions.Item>
              <Descriptions.Item label="姓名">{user?.name ?? '-'}</Descriptions.Item>
              <Descriptions.Item label="角色">
                <Tag color="blue">
                  {roleMap[user?.role ?? ''] ?? ROLE_LABEL[user?.role ?? ''] ?? user?.role ?? '-'}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="数据范围">
                {scopeMap[user?.data_scope ?? ''] ?? user?.data_scope ?? '-'}
              </Descriptions.Item>
              <Descriptions.Item label="部门">{user?.department || '-'}</Descriptions.Item>
              <Descriptions.Item label="手机号">{user?.phone || '-'}</Descriptions.Item>
              <Descriptions.Item label="邮箱">{user?.email || '-'}</Descriptions.Item>
              <Descriptions.Item label="账号状态">
                {user?.is_active ? <Tag color="success">启用</Tag> : <Tag>停用</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">{fmtDateTime(user?.created_at)}</Descriptions.Item>
            </Descriptions>
            <Typography.Paragraph type="secondary" style={{ marginTop: 12, fontSize: 12 }}>
              姓名、角色、部门等信息由管理员维护；如需调整请联系系统管理员（6.1 用户与角色）。
            </Typography.Paragraph>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="修改密码">
            <Form
              form={form}
              layout="vertical"
              onFinish={(v) => change.mutate({ old_password: v.old_password, new_password: v.new_password })}
            >
              <Form.Item
                name="old_password"
                label="当前密码"
                rules={[{ required: true, message: '请输入当前密码' }]}
              >
                <Input.Password />
              </Form.Item>
              <Form.Item
                name="new_password"
                label="新密码"
                rules={[
                  { required: true, message: '请输入新密码' },
                  { min: 6, message: '密码至少 6 位' },
                ]}
              >
                <Input.Password />
              </Form.Item>
              <Form.Item
                name="confirm"
                label="确认新密码"
                dependencies={['new_password']}
                rules={[
                  { required: true, message: '请再次输入新密码' },
                  ({ getFieldValue }) => ({
                    validator(_, value) {
                      if (!value || getFieldValue('new_password') === value) return Promise.resolve();
                      return Promise.reject(new Error('两次输入的密码不一致'));
                    },
                  }),
                ]}
              >
                <Input.Password />
              </Form.Item>
              <Space>
                <Button type="primary" htmlType="submit" loading={change.isPending}>
                  保存修改
                </Button>
                <Button onClick={() => form.resetFields()}>重置</Button>
              </Space>
            </Form>
          </Card>
        </Col>
      </Row>

      <Footnote>
        ⓘ 口径：密码至少 6 位，修改后立即生效；「数据范围」决定你在各页面能看到的数据（本人 / 本部门 / 全部）。
        系统不会用账号信息做考核推算，AI 结论亦不作为考核依据。
      </Footnote>
    </Space>
  );
}
