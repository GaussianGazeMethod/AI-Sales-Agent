import type { Role } from '@/types';

/**
 * 侧边栏菜单配置（对齐 UIdesign 的信息架构）。
 *
 *  两条设计口径（此前踩过坑，别再改回去）：
 *  1. **一级项收敛**：工作台 / 线索管理 / **客户** / 商机管理 / 客户交付 / 报表与看板 /
 *     AI 销售助手 / 系统设置 / 个人中心 —— 折叠式二级菜单，不要把所有页面平铺。
 *     （「客户」是客户-项目两层模型新增的一级项，只有一个子项，故不再塞进「商机管理」
 *     的子菜单里 —— 否则商机管理的子项会更多，也会让"客户整体价值"藏进二级。）
 *  2. **标签不带页面编号**：用户看到的是「线索列表」，不是「1.1 线索列表」；
 *     页面编号只属于 spec/08，属于实现面，不属于界面。
 */

export interface MenuLeaf {
  key: string;
  label: string;
  /** 可见角色；不写表示继承分组权限 */
  roles?: Role[];
  /** 占位页（本轮不实现） */
  placeholder?: boolean;
}

/** 一级菜单图标键（在 MainLayout 里映射为图标组件，保持本文件无 JSX） */
export type MenuIconKey =
  | 'home'
  | 'lead'
  | 'opportunity'
  | 'customer'
  | 'delivery'
  | 'report'
  | 'ai'
  | 'me'
  | 'setting';

export interface MenuGroup {
  key: string;
  label: string;
  roles: Role[];
  icon?: MenuIconKey;
  /** 无 children 时，一级项自己就是一个可点击的页面（如「工作台」） */
  path?: string;
  children?: MenuLeaf[];
}

/** 展示版只开放销售与销售管理。 */
export const ALL_ROLES: Role[] = ['market_staff', 'market_manager'];
const MANAGERS: Role[] = ['market_manager'];
export const MENU: MenuGroup[] = [
  { key: 'workbench', label: '工作台', roles: ALL_ROLES, icon: 'home', path: '/workbench' },
  { key: 'leads', label: '线索管理', roles: ALL_ROLES, icon: 'lead', children: [
    { key: '/leads', label: '线索列表' }, { key: '/leads/new', label: '新增线索' },
    { key: '/assign', label: '分配管理', roles: MANAGERS },
    { key: '/imports', label: 'Excel 导入', roles: MANAGERS },
    { key: '/imports/batches', label: '导入历史', roles: MANAGERS },
    { key: '/duplicates', label: '重复线索裁决', roles: MANAGERS },
    { key: '/targets', label: '销售目标', roles: MANAGERS }] },
  { key: 'customers', label: '客户档案', roles: ALL_ROLES, icon: 'customer', path: '/customers' },
  { key: 'opportunities', label: '商机管理', roles: ALL_ROLES, icon: 'opportunity', children: [
    { key: '/opportunities', label: '商机列表' }, { key: '/reports/funnel', label: '业绩漏斗' },
    { key: '/risks', label: '风险与卡单', roles: MANAGERS },
    { key: '/reviews', label: '项目评审', roles: MANAGERS },
    { key: '/lost', label: '丢单分析' }, { key: '/payments', label: '回款管理' }] },
  { key: 'ai', label: 'AI 销售助手', roles: ALL_ROLES, icon: 'ai', children: [
    { key: '/ai/chat', label: '对话与录入' }, { key: '/ai/drafts', label: '待确认草稿' },
    { key: '/ai/diagnoses', label: '诊断报告' }] },
  { key: 'reports', label: '销售管理', roles: ALL_ROLES, icon: 'report', children: [
    { key: '/reports/lead-board', label: '线索看板' }, { key: '/reports/weekly', label: '周报' },
    { key: '/reports/weekly-meeting', label: '团队周会', roles: MANAGERS },
    { key: '/reports/team-targets', label: '团队目标', roles: MANAGERS }] },
  { key: 'me', label: '个人中心', roles: ALL_ROLES, icon: 'me', children: [
    { key: '/todos', label: '我的待办' }, { key: '/me/profile', label: '个人信息' }] },
];

export function visibleMenu(role?: Role): MenuGroup[] {
  if (!role) return [];
  return MENU.filter((g) => g.roles.includes(role))
    .map((g) => ({
      ...g,
      children: (g.children ?? []).filter((c) => !c.roles || c.roles.includes(role)),
    }))
    // 一级单项（如「工作台」）没有 children，按自身可见性保留
    .filter((g) => Boolean(g.path) || (g.children?.length ?? 0) > 0);
}

/** 当前路径所属的一级菜单（面包屑与默认展开用） */
export function groupOf(pathname: string, groups: MenuGroup[]): MenuGroup | undefined {
  return groups.find((g) =>
    (g.children ?? []).some(
      (c) => pathname === c.key || pathname.startsWith(`${c.key}/`),
    ) || (g.path ? pathname === g.path || pathname.startsWith(`${g.path}/`) : false),
  );
}

/** 当前路径对应的菜单标签（面包屑末级） */
export function labelOf(pathname: string, groups: MenuGroup[]): string | undefined {
  for (const g of groups) {
    const hit = (g.children ?? [])
      .filter((c) => pathname === c.key || pathname.startsWith(`${c.key}/`))
      .sort((a, b) => b.key.length - a.key.length)[0];
    if (hit) return hit.label;
    if (g.path && (pathname === g.path || pathname.startsWith(`${g.path}/`))) return g.label;
  }
  return undefined;
}

export const ROUTE_ROLES: Record<string, Role[]> = {
  '/imports': MANAGERS, '/assign': MANAGERS, '/duplicates': MANAGERS,
  '/targets': MANAGERS, '/reviews': MANAGERS, '/risks': MANAGERS,
  '/reports/weekly-meeting': MANAGERS, '/reports/team-targets': MANAGERS,
  '/admin/risk-rules': MANAGERS,
};
/** 判断角色是否有权访问某路径（前缀匹配，覆盖 /leads/:id 之类子路由） */
export function canAccess(pathname: string, role?: Role): boolean {
  if (!role || !ALL_ROLES.includes(role)) return false;
  const entries = Object.entries(ROUTE_ROLES).sort((a, b) => b[0].length - a[0].length);
  for (const [prefix, roles] of entries) {
    if (pathname === prefix || pathname.startsWith(`${prefix}/`)) {
      return roles.includes(role);
    }
  }
  return true;
}
