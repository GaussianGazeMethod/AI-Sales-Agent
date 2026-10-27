import { useMemo, useRef, useState } from 'react';
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Avatar, Dropdown, Input, Layout, Menu, Space, Tag, Typography } from 'antd';
import {
  AimOutlined,
  BarChartOutlined,
  BellOutlined,
  CustomerServiceOutlined,
  HomeOutlined,
  LogoutOutlined,
  RobotOutlined,
  SearchOutlined,
  SettingOutlined,
  ShopOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { useAuthStore } from '@/store/auth';
import { canAccess, groupOf, labelOf, visibleMenu, type MenuIconKey } from '@/router/menu';
import { ROLE_LABEL } from '@/utils/constants';

const { Header, Sider, Content } = Layout;

/** 一级菜单图标（设计稿为线性图标） */
const MENU_ICONS: Record<MenuIconKey, React.ReactNode> = {
  home: <HomeOutlined />,
  lead: <TeamOutlined />,
  opportunity: <AimOutlined />,
  customer: <ShopOutlined />,
  delivery: <CustomerServiceOutlined />,
  report: <BarChartOutlined />,
  ai: <RobotOutlined />,
  me: <UserOutlined />,
  setting: <SettingOutlined />,
};

/** 主框架：侧边栏（按角色裁剪）+ 顶栏（搜索 / 通知 / 用户） */
export default function MainLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const token = useAuthStore((s) => s.token);
  const logout = useAuthStore((s) => s.logout);
  const [keyword, setKeyword] = useState('');

  const groups = useMemo(() => visibleMenu(user?.role), [user?.role]);

  // 未登录直接跳登录页
  if (!token) return <Navigate to="/login" replace />;

  // 设计稿口径：一级可折叠子菜单；「工作台」这种一级单项直接可点
  const menuItems = groups.map((g) =>
    g.children && g.children.length > 0
      ? {
          key: g.key,
          label: g.label,
          icon: g.icon ? MENU_ICONS[g.icon] : undefined,
          children: g.children.map((c) => ({ key: c.key, label: c.label })),
        }
      : {
          key: g.path ?? g.key,
          label: g.label,
          icon: g.icon ? MENU_ICONS[g.icon] : undefined,
        },
  );

  // 选中项：取最长前缀匹配的菜单 key
  const allKeys = groups.flatMap((g) =>
    g.children && g.children.length > 0 ? g.children.map((c) => c.key) : [g.path ?? g.key],
  );
  const selected =
    allKeys
      .filter((k) => location.pathname === k || location.pathname.startsWith(`${k}/`))
      .sort((a, b) => b.length - a.length)[0] ?? location.pathname;

  // 面包屑：一级分组 / 当前页（设计稿顶栏形态）
  const currentGroup = groupOf(location.pathname, groups);
  const currentLabel = labelOf(location.pathname, groups);

  // 展开态：默认只展开当前所在组（设计稿不会把所有子项铺开）
  const activeGroupKey = currentGroup?.children?.length ? currentGroup.key : undefined;
  const [openKeys, setOpenKeys] = useState<string[]>(activeGroupKey ? [activeGroupKey] : []);
  const lastGroupRef = useRef<string | undefined>(activeGroupKey);
  if (lastGroupRef.current !== activeGroupKey) {
    lastGroupRef.current = activeGroupKey;
    if (activeGroupKey && !openKeys.includes(activeGroupKey)) {
      setOpenKeys((keys) => [...keys, activeGroupKey]);
    }
  }

  if (!canAccess(location.pathname, user?.role)) {
    return <Navigate to="/403" replace />;
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={200} theme="light" style={{ borderRight: '1px solid var(--border)' }}>
        <div
          style={{
            height: 60,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '0 16px',
            fontWeight: 600,
            fontSize: 16,
            color: 'var(--brand)',
          }}
        >
          <span
            style={{
              width: 22,
              height: 22,
              borderRadius: 7,
              background: 'var(--brand)',
              color: '#fff',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 13,
            }}
          >
            市
          </span>
          市场助手
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selected]}
          openKeys={openKeys}
          onOpenChange={(keys) => setOpenKeys(keys as string[])}
          items={menuItems}
          style={{ borderInlineEnd: 'none', paddingBottom: 24 }}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            background: '#fff',
            padding: '0 20px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid var(--border)',
            height: 60,
            lineHeight: '60px',
          }}
        >
          <Space size={12} style={{ flex: 1, minWidth: 0 }}>
            {/* 面包屑：一级分组 / 当前页 */}
            <Typography.Text type="secondary" style={{ fontSize: 13, whiteSpace: 'nowrap' }}>
              {currentGroup?.label ?? '工作台'}
              {currentLabel && currentLabel !== currentGroup?.label ? ` / ${currentLabel}` : ''}
            </Typography.Text>
            <Input
              allowClear
              prefix={<SearchOutlined style={{ color: '#98A2B3' }} />}
              placeholder="搜索客户名称 / 联系人 / 电话"
              style={{ maxWidth: 380, borderRadius: 999 }}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              onPressEnter={() => {
                if (keyword.trim())
                  navigate(`/search?keyword=${encodeURIComponent(keyword.trim())}`);
              }}
            />
          </Space>
          <Space size={16}>
            <a onClick={() => navigate('/notifications')} style={{ color: 'inherit' }}>
              <BellOutlined /> 通知
            </a>
            <Dropdown
              menu={{
                items: [
                  { key: 'profile', icon: <UserOutlined />, label: '账号设置' },
                  { key: 'setting', icon: <SettingOutlined />, label: '我的目标与业绩' },
                  { type: 'divider' },
                  { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true },
                ],
                onClick: ({ key }) => {
                  if (key === 'profile') navigate('/me/profile');
                  if (key === 'setting') navigate('/me/achievement');
                  if (key === 'logout') {
                    logout();
                    navigate('/login', { replace: true });
                  }
                },
              }}
            >
              <Space style={{ cursor: 'pointer' }}>
                <Avatar size="small" icon={<UserOutlined />} />
                <Typography.Text>{user?.name}</Typography.Text>
                <Tag color="blue">{ROLE_LABEL[user?.role ?? ''] ?? user?.role}</Tag>
              </Space>
            </Dropdown>
          </Space>
        </Header>
        <Content style={{ padding: 16, background: 'var(--bg-layout)' }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
