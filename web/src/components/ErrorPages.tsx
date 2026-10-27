import { Button, Result } from 'antd';
import { useNavigate } from 'react-router-dom';

/** 403 无权限页 */
export function Forbidden() {
  const navigate = useNavigate();
  return (
    <Result
      status="403"
      title="403"
      subTitle="抱歉，你没有访问该页面的权限。"
      extra={
        <Button type="primary" onClick={() => navigate('/workbench')}>
          返回工作台
        </Button>
      }
    />
  );
}

/** 404 页面不存在 */
export function NotFound() {
  const navigate = useNavigate();
  return (
    <Result
      status="404"
      title="404"
      subTitle="页面不存在或已被移除。"
      extra={
        <Button type="primary" onClick={() => navigate('/workbench')}>
          返回工作台
        </Button>
      }
    />
  );
}
