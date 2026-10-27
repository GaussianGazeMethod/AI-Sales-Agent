import { Button, Card, Result, Typography } from 'antd';
import { useNavigate } from 'react-router-dom';

interface Props {
  /** 占位模块标题，如「2 商机管理 / 2.1 商机列表」 */
  title: string;
  description?: string;
}

/** 未实现模块统一占位页（2 商机管理 / 3 客户交付 / 5 AI 销售助手） */
export default function PagePlaceholder({
  title,
  description = '该模块正在开发中（P1/P2 阶段）',
}: Props) {
  const navigate = useNavigate();
  return (
    <Card>
      <Result
        status="info"
        title={title}
        subTitle={description}
        extra={
          <Button type="primary" onClick={() => navigate('/workbench')}>
            返回工作台
          </Button>
        }
      />
      <Typography.Paragraph type="secondary" style={{ textAlign: 'center' }}>
        菜单与路由已就绪，页面能力将在后续阶段接入。
      </Typography.Paragraph>
    </Card>
  );
}
