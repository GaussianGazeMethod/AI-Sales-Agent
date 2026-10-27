import { createBrowserRouter, Navigate } from 'react-router-dom';
import MainLayout from '@/layouts/MainLayout';
import PagePlaceholder from '@/components/PagePlaceholder';
import { Forbidden, NotFound } from '@/components/ErrorPages';
import { MENU } from '@/router/menu';
import LoginPage from '@/pages/Login';
import Workbench from '@/pages/Workbench';
import Todos from '@/pages/Todos';
import NotificationsPage from '@/pages/Notifications';
import SearchPage from '@/pages/Search';
import Leads from '@/pages/Leads';
import LeadSimpleList from '@/pages/LeadSimpleList';
import { useAuthStore } from '@/store/auth';
function LeadEntry() { const role = useAuthStore(s => s.user?.role); return role === 'market_staff' ? <LeadSimpleList /> : <Leads />; }
import LeadForm from '@/pages/LeadForm';
import LeadSimpleDetail from '@/pages/LeadSimpleDetail';
import LeadQuickCreate from '@/pages/LeadQuickCreate';
import Imports from '@/pages/Imports';
import ImportBatches from '@/pages/ImportBatches';
import Assign from '@/pages/Assign';
import Duplicates from '@/pages/Duplicates';
import Targets from '@/pages/Targets';
import LeadBoard from '@/pages/LeadBoard';
import MyAchievement from '@/pages/MyAchievement';
import MyProfile from '@/pages/MyProfile';
import Opportunities from '@/pages/Opportunities';
import OpportunityDetail from '@/pages/OpportunityDetail';
import Customers from '@/pages/Customers';
import CustomerDetail from '@/pages/CustomerDetail';
import MyPerformance from '@/pages/MyPerformance';
import RiskCenter from '@/pages/RiskCenter';
import RiskRules from '@/pages/RiskRules';
import LostAnalysis from '@/pages/LostAnalysis';
import Reviews from '@/pages/Reviews';
import Payments from '@/pages/Payments';
import Funnel from '@/pages/reports/Funnel';
import WeeklyReport from '@/pages/reports/WeeklyReport';
import WeeklyMeeting from '@/pages/reports/WeeklyMeeting';
import AiChat from '@/pages/ai/AiChat';
import AssistantChat from '@/pages/ai/AssistantChat';
import AiDrafts from '@/pages/ai/AiDrafts';
import AiDiagnoses from '@/pages/ai/AiDiagnoses';
import OpportunityFunnel from '@/pages/reports/OpportunityFunnel';
import TeamTargets from '@/pages/reports/TeamTargets';

/** 已实现的路径集合，其余菜单项统一落到占位页 */
const IMPLEMENTED = new Set([
  '/workbench',
  '/todos',
  '/notifications',
  '/search',
  '/leads',
  '/leads/new',
  '/imports',
  '/imports/batches',
  '/assign',
  '/duplicates',
  '/targets',
  '/reports/lead-board',
  '/admin/users',
  '/admin/dicts',
  '/admin/logs',
  '/me/achievement',
  '/me/profile',
  // 2 商机管理
  '/opportunities',
  // 客户（客户-项目两层模型）：列表 + 详情
  '/customers',
  '/reports/funnel',
  '/risks',
  '/me/performance',
  // 5 AI 销售助手
  '/ai/chat',
  '/ai/drafts',
  '/ai/diagnoses',
  // P1-c 四类预警 / P1-d 周报·评审·周会·丢单·回款
  '/admin/risk-rules',
  '/lost',
  '/reviews',
  '/reports/weekly',
  '/reports/weekly-meeting',
  '/payments',
  // P2-a 闭环三交付核心（3.1~3.5 + 6.6）
  '/contracts',
  '/contracts/detail',
  '/contracts/handover',
  '/contracts/delivery-items',
  '/deliveries',
  '/admin/delivery-standards',
  // P2-b 运营与客户成功（3.6~3.12 + 4.6）
  '/support',
  '/issues',
  '/complaints',
  '/operation-metrics',
  '/operation-records',
  '/acceptances',
  '/renewals',
  '/reports/operation-review',
  '/reports/overview',
  '/reports/opportunity-funnel',
  '/reports/team-targets',
  '/admin/roles',
  '/admin/departments',
  '/admin/stage-standards',
  '/admin/notification-templates',
  '/admin/adapters',
]);

/** 占位路由兜底：菜单里若有未登记进 IMPLEMENTED 的项，仍落到占位页而不是 404（当前已无此类项） */
const placeholderRoutes = MENU.flatMap((g) =>
  (g.children ?? [])
    .filter((c) => !IMPLEMENTED.has(c.key) && c.key !== '/todos')
    .map((c) => ({
      path: c.key.replace(/^\//, ''),
      element: <PagePlaceholder title={`${g.label} / ${c.label}`} />,
    })),
);

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  { path: '/m/ai/chat', element: <AssistantChat mobile /> },
  { path: '/403', element: <Forbidden /> },
  {
    path: '/',
    element: <MainLayout />,
    children: [
      { index: true, element: <Navigate to="/workbench" replace /> },
      { path: 'workbench', element: <Workbench /> },
      { path: 'todos', element: <Todos /> },
      { path: 'notifications', element: <NotificationsPage /> },
      { path: 'search', element: <SearchPage /> },
      { path: 'leads', element: <LeadEntry /> },
      { path: 'leads/new', element: <LeadQuickCreate /> },
      { path: 'leads/:id', element: <LeadSimpleDetail /> },
      { path: 'leads/:id/edit', element: <LeadForm /> },
      { path: 'imports', element: <Imports /> },
      { path: 'imports/batches', element: <ImportBatches /> },
      { path: 'assign', element: <Assign /> },
      { path: 'duplicates', element: <Duplicates /> },
      { path: 'targets', element: <Targets /> },
      { path: 'reports/lead-board', element: <LeadBoard /> },
      { path: 'me/achievement', element: <MyAchievement /> },
      { path: 'me/profile', element: <MyProfile /> },
      { path: 'me/performance', element: <MyPerformance /> },
      { path: 'opportunities', element: <Opportunities /> },
      // 客户（客户-项目两层模型）：列表自带筛选，菜单项不带 id
      { path: 'customers', element: <Customers /> },
      { path: 'customers/:id', element: <CustomerDetail /> },
      // 2.2 菜单项本身不带 id，统一回到列表由用户选择具体商机
      { path: 'opportunities/detail', element: <Navigate to="/opportunities" replace /> },
      { path: 'opportunities/:id', element: <OpportunityDetail /> },
      { path: 'reports/funnel', element: <Funnel /> },
      { path: 'risks', element: <RiskCenter /> },
      { path: 'admin/risk-rules', element: <RiskRules /> },
      { path: 'lost', element: <LostAnalysis /> },
      { path: 'reviews', element: <Reviews /> },
      { path: 'payments', element: <Payments /> },
      { path: 'reports/weekly', element: <WeeklyReport /> },
      { path: 'reports/weekly-meeting', element: <WeeklyMeeting /> },
      { path: 'ai/chat', element: <AssistantChat /> },
      { path: 'ai/legacy-chat', element: <AiChat /> },
      { path: 'ai/drafts', element: <AiDrafts /> },
      { path: 'ai/diagnoses', element: <AiDiagnoses /> },
      // 3 客户交付（闭环三）：3.2/3.3/3.4 的菜单项本身不带 id，统一回到列表由用户选择客户
      // 3 客户交付（闭环三）· P2-b 运营与客户成功
      // 4 报表与看板 · 4.6 运营复盘
      { path: 'reports/opportunity-funnel', element: <OpportunityFunnel /> },
      { path: 'reports/team-targets', element: <TeamTargets /> },
      ...placeholderRoutes,
      { path: '*', element: <NotFound /> },
    ],
  },
]);
