import type { ReactNode } from 'react';
import { Empty, Space, Typography } from 'antd';
import { InfoCircleOutlined, RobotOutlined, SafetyCertificateOutlined } from '@ant-design/icons';

/**
 * 设计系统小件集合（对应 spec/11-UI设计规范.md 第三节）。
 *
 * 放在一起是因为它们都是"一句话/一个小标签"级别的原语，
 * 单独建文件反而增加跳转成本（与 tags.tsx 的做法一致）。
 */

/* ══════════════ ⓘ 口径脚注（判据 C3）══════════════ */

/** 页脚口径说明。**任何比率/时长口径都应在界面上出现一次**，不要只写在文档里。 */
export function Footnote({ children, style }: { children: ReactNode; style?: React.CSSProperties }) {
  return (
    <div className="footnote" style={style}>
      <InfoCircleOutlined className="footnote-icon" />
      <span>{children}</span>
    </div>
  );
}

/* ══════════════ 面板角标（如「未确认不生效」）══════════════ */

export function PanelFlag({ children }: { children: ReactNode }) {
  return <span className="panel-flag">{children}</span>;
}

/* ══════════════ AI 免责标识（赋能边界 1）══════════════ */

/**
 * 「AI 建议仅供参考 · 不作为考核依据」。
 *
 *  这不是装饰：设计稿要求它**出现在每个 AI 产出旁边**，
 * 目的是让人敢用、敢否掉 AI 结论，而不是被 AI 追着跑。
 */
export function AiDisclaimer({ text = 'AI 建议仅供参考，不作为考核依据' }: { text?: string }) {
  return (
    <span className="panel-flag" style={{ background: '#F4F0FF', borderColor: '#E4DAFF', color: '#5B34D6' }}>
      <RobotOutlined />
      {text}
    </span>
  );
}

/* ══════════════ 演示数据标签（诚实底线）══════════════ */

export function DemoDataTag({ text = '演示数据' }: { text?: string }) {
  return <span className="panel-flag">{text}</span>;
}

/* ══════════════ 空态引导（判据 C2：少判断）══════════════ */

/**
 * 空态不再只说"没有数据"，而是回答**"下一步该做什么"**，并给直达入口。
 *
 * 用法：
 * ```tsx
 * <NextStepGuide
 *   title="本月尚未设定目标"
 *   description="目标设定在 1.8 市场目标设定；设好目标后本页自动出现达成率。"
 *   actions={<Button type="primary" onClick={go}>去设定目标</Button>}
 * />
 * ```
 */
export function NextStepGuide({
  title,
  description,
  actions,
  icon,
  compact,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  icon?: ReactNode;
  /** 卡片内嵌用（更紧凑） */
  compact?: boolean;
}) {
  if (compact) {
    return (
      <div className="guide-block">
        <Space direction="vertical" size={4}>
          <Typography.Text strong>{title}</Typography.Text>
          {description && (
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              {description}
            </Typography.Text>
          )}
        </Space>
        {actions && <div className="guide-actions">{actions}</div>}
      </div>
    );
  }
  return (
    <Empty
      image={icon ?? Empty.PRESENTED_IMAGE_SIMPLE}
      description={
        <Space direction="vertical" size={4} style={{ maxWidth: 520 }}>
          <Typography.Text strong>{title}</Typography.Text>
          {description && (
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              {description}
            </Typography.Text>
          )}
        </Space>
      }
    >
      {actions && <div className="guide-actions">{actions}</div>}
    </Empty>
  );
}

/** ⓘ 一行"口径已写在界面上"的合规提示（用于导出/考核相关页面） */
export function NotForAssessment({ children }: { children?: ReactNode }) {
  return (
    <Footnote>
      <SafetyCertificateOutlined style={{ marginRight: 4 }} />
      {children ?? '本页数据用于过程管理，AI 结论与系统预警不作为考核依据。'}
    </Footnote>
  );
}
