import { ReactNode } from 'react';
import { Space, Typography } from 'antd';

/** 行内 **加粗** 解析（不引 Markdown 库，只处理加粗） */
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const parts = text.split('**');
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <Typography.Text strong key={`${keyPrefix}-b${i}`}>
        {part}
      </Typography.Text>
    ) : (
      <span key={`${keyPrefix}-t${i}`}>{part}</span>
    ),
  );
}

/**
 * AI 输出正文的轻量渲染：把 Markdown 文本按行渲染成标题 / 列表 / 段落。
 * 只做 `### 标题`、`- 列表`、`**加粗**` 三类语法，避免引入 Markdown 依赖。
 */
export default function AiContent({ content }: { content?: string | null }) {
  if (!content) return <Typography.Text type="secondary">暂无正文</Typography.Text>;

  return (
    <Space direction="vertical" size={4} style={{ width: '100%' }}>
      {content.split('\n').map((raw, idx) => {
        const line = raw.replace(/\s+$/, '');
        const key = `ai-line-${idx}`;
        if (!line.trim()) return <div key={key} style={{ height: 4 }} />;
        const heading = /^#{2,4}\s*(.+)$/.exec(line.trim());
        if (heading) {
          return (
            <Typography.Text strong key={key} style={{ fontSize: 14, display: 'block' }}>
              {heading[1]}
            </Typography.Text>
          );
        }
        const bullet = /^[-*]\s+(.+)$/.exec(line.trim()) ?? /^\d+[.、]\s*(.+)$/.exec(line.trim());
        if (bullet) {
          return (
            <div key={key} style={{ paddingLeft: 12, fontSize: 13, lineHeight: 1.7 }}>
              · {renderInline(bullet[1], key)}
            </div>
          );
        }
        return (
          <div key={key} style={{ fontSize: 13, lineHeight: 1.7 }}>
            {renderInline(line, key)}
          </div>
        );
      })}
    </Space>
  );
}
