import { Progress, Space, Typography } from 'antd';

export interface BarItem {
  label: string;
  value: number;
  /** 次要文本，如「有效 3 / 12（25.0%）」 */
  extra?: string;
}

interface Props {
  items: BarItem[];
  /** 进度条颜色 */
  color?: string;
  /** 空态 */
  emptyText?: string;
  /** 按最大值归一化（默认 true） */
  normalize?: boolean;
}

/**
 * 轻量条形图：不引入图表库，
 * 用 Progress + 文本展示分布类数据（来源分布 / 漏斗 / 人均产出）。
 */
export default function SimpleBar({
  items,
  color = '#1677ff',
  emptyText = '暂无数据',
  normalize = true,
}: Props) {
  if (!items.length) {
    return <Typography.Text type="secondary">{emptyText}</Typography.Text>;
  }
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <Space direction="vertical" size={4} style={{ width: '100%' }}>
      {items.map((i) => {
        const percent = normalize ? Math.round((i.value / max) * 100) : Math.min(i.value, 100);
        return (
          <div key={i.label}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <Typography.Text style={{ fontSize: 13 }}>{i.label}</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {i.value}
                {i.extra ? ` · ${i.extra}` : ''}
              </Typography.Text>
            </div>
            <Progress percent={percent} showInfo={false} strokeColor={color} size="small" />
          </div>
        );
      })}
    </Space>
  );
}
