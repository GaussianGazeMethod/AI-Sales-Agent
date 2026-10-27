/**
 * 设计系统 Token（来源：UIdesign/ 的 9 张高保真稿 + manifest.json）。
 *
 * 口径：这里只定义**呈现层**常量，不改任何业务口径。
 * 改色/改圆角请同时更新 spec/11-UI设计规范.md 的 token 表。
 */
import type { ThemeConfig } from 'antd';

/** 主色（靛蓝）：主按钮 / 选中态 / 进度条 / 链接 */
export const BRAND = '#465FFF';
/** 主色浅底：侧栏与菜单选中背景 */
export const BRAND_SOFT = '#EEF1FF';
/** 页面底色 */
export const BG_LAYOUT = '#F5F7FA';
/** 卡片边框 */
export const BORDER = '#EAECF0';
/** 主文本 / 次文本 */
export const TEXT_MAIN = '#182230';
export const TEXT_SUB = '#667085';
/** 状态色 */
export const SUCCESS = '#12B76A';
export const WARNING = '#F79009';
export const DANGER = '#F04438';

/** 卡片圆角 */
export const RADIUS = 8;

/** 图标底色（KPI 卡左侧彩色圆角块，与设计稿一致：蓝/绿/紫/橙循环） */
export const KPI_TONES = {
  brand: { bg: BRAND_SOFT, fg: BRAND },
  green: { bg: '#E7F8F0', fg: SUCCESS },
  purple: { bg: '#F4F0FF', fg: '#7A5AF8' },
  amber: { bg: '#FEF4E6', fg: WARNING },
  red: { bg: '#FEECEB', fg: DANGER },
  gray: { bg: '#F2F4F7', fg: TEXT_SUB },
} as const;

export type KpiTone = keyof typeof KPI_TONES;

/** AntD 主题：与设计稿对齐 */
export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: BRAND,
    colorLink: BRAND,
    colorSuccess: SUCCESS,
    colorWarning: WARNING,
    colorError: DANGER,
    colorTextBase: TEXT_MAIN,
    colorTextSecondary: TEXT_SUB,
    colorBgLayout: BG_LAYOUT,
    colorBorderSecondary: BORDER,
    borderRadius: RADIUS,
    fontSize: 14,
    /** 设计稿为"密集但可读"：卡片内边距比 AntD 默认略小 */
    padding: 16,
    paddingLG: 20,
  },
  components: {
    Layout: {
      headerBg: '#FFFFFF',
      headerHeight: 60,
      headerPadding: '0 20px',
      bodyBg: BG_LAYOUT,
      siderBg: '#FFFFFF',
    },
    Menu: {
      itemSelectedBg: BRAND_SOFT,
      itemSelectedColor: BRAND,
      itemHeight: 40,
      itemMarginInline: 8,
      itemBorderRadius: RADIUS,
      iconSize: 16,
    },
    Card: {
      headerFontSize: 16,
      headerHeight: 48,
      /** 细边框、无重阴影（设计稿为扁平风） */
      boxShadowTertiary: '0 1px 2px rgba(16, 24, 40, 0.04)',
    },
    Table: {
      headerBg: '#F9FAFB',
      headerColor: TEXT_SUB,
      headerSplitColor: 'transparent',
      rowHoverBg: '#F5F8FF',
      cellPaddingBlock: 10,
      cellPaddingInline: 12,
    },
    Tabs: {
      horizontalItemPadding: '10px 0',
      titleFontSize: 14,
    },
    Button: {
      /** 主按钮保持实心靛蓝、次按钮白底蓝边（设计稿两种形态） */
      primaryShadow: 'none',
      defaultBorderColor: '#D0D5DD',
    },
    Statistic: {
      contentFontSize: 24,
    },
  },
};
