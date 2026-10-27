import type { ReactNode } from 'react';
import { WarningFilled, InfoCircleOutlined } from '@ant-design/icons';

/**
 * 门槛 / 缺口提示（设计稿的琥珀色横幅）。
 *
 *  这是「简化事 · 少判断」的核心组件：**任何"不能继续"都必须由它讲清
 * "为什么不能 + 缺什么 + 怎么补"**，而不是只弹一句 409。
 *
 * 用法：
 * ```tsx
 * <GateBanner
 *   title="缺少《客户需求分析报告》，暂不能推进"
 *   items={['输出客户需求分析报告并摸清预算（硬门槛，需附报告文件）']}
 *   action={<Button size="small" onClick={upload}>上传报告</Button>}
 * />
 * ```
 */
export interface GateBannerProps {
  /** 一句话说清"卡在哪、为什么不能继续" */
  title: ReactNode;
  /** 缺什么（逐条列，便于对照去补） */
  items?: ReactNode[];
  /** 就地补齐的入口（上传/去填写/去处理） */
  action?: ReactNode;
  /** 更轻的形态：只做提示、不带"不能推进"的语义 */
  tone?: 'gate' | 'tip';
  style?: React.CSSProperties;
}

export default function GateBanner({
  title,
  items,
  action,
  tone = 'gate',
  style,
}: GateBannerProps) {
  return (
    <div className="gate-banner" style={style}>
      <span className="gate-icon">
        {tone === 'gate' ? <WarningFilled /> : <InfoCircleOutlined />}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="gate-title">{title}</div>
        {items && items.length > 0 && (
          <ul className="gate-items">
            {items.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        )}
        {action && <div className="gate-action">{action}</div>}
      </div>
    </div>
  );
}
