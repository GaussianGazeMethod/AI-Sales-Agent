import type { ReactNode } from 'react';
import { Space } from 'antd';

/**
 * 页面标题区（设计稿统一形态）：大标题 + 副标题 + 可选演示数据标签 + 一行元信息 + 右侧操作区。
 *
 * 用法：
 * ```tsx
 * <PageHeader
 *   title="云川三中 / AI阅卷项目"
 *   subtitle="优先处理即将超时的跟进与待确认草稿。"
 *   demo
 *   meta={[{ label: '客户', value: '云川三中' }, { label: '负责人', value: '陈悦' }]}
 *   actions={<Button type="primary">新增线索</Button>}
 * />
 * ```
 */
export interface PageMetaItem {
  label?: string;
  value: ReactNode;
}

export interface PageHeaderProps {
  title: ReactNode;
  /** 副标题：写"这一页该先干什么"，不要写营销话术 */
  subtitle?: ReactNode;
  /** 一行元信息（客户 / 行业 / 区域 / 负责人 / 创建时间…） */
  meta?: PageMetaItem[];
  /** 右侧操作区 */
  actions?: ReactNode;
  /** 展示「演示数据」标签（种子数据页面建议打开，避免被误当真实业绩） */
  demo?: boolean;
  /** 标题右侧额外标签（状态、环节…） */
  tags?: ReactNode;
}

export default function PageHeader({
  title,
  subtitle,
  meta,
  actions,
  demo,
  tags,
}: PageHeaderProps) {
  return (
    <div className="page-header">
      <div style={{ minWidth: 0 }}>
        <Space size={8} align="center" wrap>
          <h1 className="page-title">{title}</h1>
          {demo && <span className="panel-flag">演示数据</span>}
          {tags}
        </Space>
        {subtitle && <p className="page-subtitle">{subtitle}</p>}
        {meta && meta.length > 0 && (
          <div className="page-meta">
            {meta.map((item, index) => (
              <span className="meta-item" key={`${item.label ?? ''}-${index}`}>
                {item.label && <span style={{ color: '#98A2B3' }}>{item.label}：</span>}
                <span>{item.value}</span>
              </span>
            ))}
          </div>
        )}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}
