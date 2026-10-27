import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Button, Card, Col, Progress, Row, Space, Table, Tag, Tooltip, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { CheckCircleOutlined, CloseCircleOutlined, FileExcelOutlined } from '@ant-design/icons';
import { listImportBatches } from '@/api/leads';
import { fmtDateTime } from '@/utils/format';
import KpiCard from '@/components/KpiCard';
import PageHeader from '@/components/PageHeader';
import { Footnote, NextStepGuide } from '@/components/Guide';
import type { ImportBatch } from '@/types';

/** 1.5 导入批次历史：批次、条数、成功率、失败原因 */
export default function ImportBatches() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const { data, isLoading } = useQuery({
    queryKey: ['import-batches', page, pageSize],
    queryFn: () => listImportBatches({ page, page_size: pageSize }),
  });

  const columns: ColumnsType<ImportBatch> = [
    { title: '批次号', dataIndex: 'batch_no', width: 140, render: (v, r) => v || `#${r.id}` },
    { title: '文件名', dataIndex: 'file_name', ellipsis: true },
    { title: '导入人', dataIndex: 'importer_name', width: 110, render: (v) => v || '-' },
    {
      title: '导入时间',
      dataIndex: 'created_at',
      width: 160,
      render: (v: string) => fmtDateTime(v),
    },
    { title: '总条数', dataIndex: 'total', width: 90 },
    {
      title: '成功',
      dataIndex: 'success',
      width: 90,
      render: (v: number) => <Tag color="green">{v}</Tag>,
    },
    {
      title: '失败',
      dataIndex: 'failed',
      width: 90,
      render: (v: number) => (v > 0 ? <Tag color="red">{v}</Tag> : <Tag>0</Tag>),
    },
    {
      title: (
        <Tooltip title="成功率 = 成功条数 / 该批次总条数；失败行不入库">
          <span>成功率</span>
        </Tooltip>
      ),
      key: 'rate',
      width: 160,
      render: (_, r) => {
        const rate = r.total ? Math.round((r.success / r.total) * 100) : 0;
        return <Progress percent={rate} size="small" status={rate === 100 ? 'success' : 'normal'} />;
      },
    },
    {
      title: '失败原因',
      key: 'errors',
      ellipsis: true,
      render: (_, r) =>
        r.errors?.length ? (
          <Typography.Text type="danger" style={{ fontSize: 12 }}>
            {r.errors
              .slice(0, 3)
              .map((e) => `第${e.row}行：${e.message}`)
              .join('；')}
            {r.errors.length > 3 ? ` 等 ${r.errors.length} 条` : ''}
          </Typography.Text>
        ) : (
          '-'
        ),
    },
  ];

  const rows = data?.items ?? [];
  const batchSuccess = rows.reduce((sum, r) => sum + (r.success ?? 0), 0);
  const batchFailed = rows.reduce((sum, r) => sum + (r.failed ?? 0), 0);

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title="导入批次历史"
        subtitle="只读回看每次导入的结果——失败行不入库，可据此定位是哪个文件、哪几行出的问题。"
        demo
        actions={
          <Space wrap>
            <Button type="primary" onClick={() => navigate('/imports')}>
              去 Excel 导入
            </Button>
          </Space>
        }
      />

      <Row gutter={[12, 12]}>
        <Col xs={24} sm={8}>
          <KpiCard
            label="批次数"
            value={data?.total ?? 0}
            icon={<FileExcelOutlined />}
            tone="brand"
            hint="每次上传文件生成一条导入记录（含文件名、导入人、时间与结果）"
          />
        </Col>
        <Col xs={24} sm={8}>
          <KpiCard
            label="本页成功入库"
            value={batchSuccess}
            icon={<CheckCircleOutlined />}
            tone="green"
            hint="仅统计当前页各批次「成功」条数之和；全量看分页"
          />
        </Col>
        <Col xs={24} sm={8}>
          <KpiCard
            label="本页校验失败"
            value={batchFailed}
            icon={<CloseCircleOutlined />}
            tone={batchFailed > 0 ? 'amber' : 'gray'}
            danger={batchFailed > 0}
            hint="仅统计当前页各批次「失败」条数之和；失败行不入库，可回到原文件按行号修正后重传"
          />
        </Col>
      </Row>

      <Card>
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={rows}
        locale={{
          emptyText: (
            <NextStepGuide
              title="暂无导入批次"
              description="还没有上传过 Excel。去「Excel 导入」下载模板、填写后上传，这里会自动出现批次记录与失败明细。"
              actions={
                <Button type="primary" onClick={() => navigate('/imports')}>
                  去 Excel 导入
                </Button>
              }
            />
          ),
        }}
        pagination={{
          current: page,
          pageSize,
          total: data?.total ?? 0,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => {
            setPage(p);
            setPageSize(ps);
          },
        }}
      />
      </Card>

      <Footnote>
        批次口径：一次上传文件 = 一条批次记录，记录文件名 / 导入人 / 导入时间 / 总条数 / 成功 / 失败。
        成功率 = 成功条数 / 该批次总条数；失败行不入库，按行号回到原文件修正后重传即可（已成功的行不会重复导入）。
        导入结果用于定位问题文件，不作为对导入人的考核依据。
      </Footnote>
    </Space>
  );
}
