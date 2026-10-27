import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Button, Card, Divider, Form, Input, Space, Tag, Typography, message } from 'antd';
import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { login } from '@/api/auth';
import { useAuthStore } from '@/store/auth';

/** 两个内置演示账号，可点击登录。 */
const DEMO_ACCOUNTS = [
  { username: 'sales', label: '演示销售 · 销售人员', landing: '/ai/chat' },
  { username: 'manager', label: '演示经理 · 管理人员', landing: '/leads' },
];
const DEMO_USERNAMES = new Set(DEMO_ACCOUNTS.map((a) => a.username));

export default function LoginPage() {
  const navigate = useNavigate();
  const loginStore = useAuthStore((s) => s.login);
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm<{ username: string; password: string }>();
  const [search] = useSearchParams();
  const autoTried = useRef(false);

  /** 演示直登只允许内置账号。 */
  const doLogin = useCallback(
    async (username: string, next?: string) => {
      if (!DEMO_USERNAMES.has(username)) {
        message.warning('演示直登仅支持内置演示账号');
        return;
      }
      setLoading(true);
      try {
        const resp = await login({ username, password: '123456' });
        loginStore(resp.access_token, resp.user);
        message.success(`已以「${resp.user.name}」身份进入`);
        const landing =
          next || DEMO_ACCOUNTS.find((a) => a.username === username)?.landing || '/workbench';
        navigate(landing, { replace: true });
      } catch {
        // 错误提示已由 axios 拦截器统一处理
      } finally {
        setLoading(false);
      }
    },
    [loginStore, navigate],
  );

  useEffect(() => {
    const as = search.get('as');
    if (!as || autoTried.current) return;
    autoTried.current = true; // 只自动尝试一次，避免失败后反复重试
    void doLogin(as, search.get('next') ?? undefined);
  }, [search, doLogin]);

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const resp = await login(values);
      loginStore(resp.access_token, resp.user);
      message.success(`欢迎回来，${resp.user.name}`);
      const requested = search.get('next');
      navigate(requested?.startsWith('/') && !requested.startsWith('//') ? requested : '/workbench', { replace: true });
    } catch {
      // 错误提示已由 axios 拦截器统一处理
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--bg-layout)',
        padding: 24,
      }}
    >
      <Card style={{ width: 400 }} styles={{ body: { padding: 28 } }}>
        <Space align="center" size={10} style={{ marginBottom: 6 }}>
          <span
            style={{
              width: 28,
              height: 28,
              borderRadius: 8,
              background: 'var(--brand)',
              color: '#fff',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 15,
              fontWeight: 600,
            }}
          >
            市
          </span>
          <Typography.Title level={4} style={{ margin: 0 }}>
            市场助手
          </Typography.Title>
          <Tag color="blue" style={{ marginInlineStart: 4 }}>
            演示数据
          </Tag>
        </Space>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 20, fontSize: 13 }}>
          AI 销售助手：线索获取与验证 → 商机跟进与签单。
        </Typography.Paragraph>

        <Form form={form} layout="vertical" onFinish={onFinish} autoComplete="off">
          <Form.Item name="username" label="账号" rules={[{ required: true, message: '请输入账号' }]}>
            <Input prefix={<UserOutlined />} placeholder="请输入账号" size="large" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="请输入密码"
              size="large"
              onPressEnter={() => form.submit()}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" block size="large" loading={loading}>
            登录
          </Button>
        </Form>

        <Divider plain style={{ margin: '20px 0 12px', fontSize: 12, color: 'var(--text-sub)' }}>
          演示账号（密码均为 123456）—— **点一下即以该角色进入**；也可用直登链接：/login?as=账号
        </Divider>
        <Space size={[6, 6]} wrap>
          {DEMO_ACCOUNTS.map((item) => (
            <Tag
              key={item.username}
              style={{ cursor: 'pointer', margin: 0 }}
              onClick={() => void doLogin(item.username)}
            >
              {item.label}
            </Tag>
          ))}
        </Space>
        <div className="footnote" style={{ marginTop: 14 }}>
          <span>
            仅包含两个演示账号，所有业务数据均为合成样例。
            <br />
            演示直登仅支持内置账号（形如 /login?as=manager&amp;next=/leads），不构成任意账号后门。
          </span>
        </div>
      </Card>
    </div>
  );
}
