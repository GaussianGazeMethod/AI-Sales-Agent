import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  Button,
  Card,
  Col,
  Divider,
  Result,
  Row,
  Space,
  Table,
  Tag,
  Upload,
  message,
} from 'antd';
import type { UploadFile } from 'antd/es/upload/interface';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  DownloadOutlined,
  FileExcelOutlined,
  InboxOutlined,
} from '@ant-design/icons';
import { IMPORT_TEMPLATE_URL, importLeads } from '@/api/leads';
import GateBanner from '@/components/GateBanner';
import KpiCard from '@/components/KpiCard';
import PageHeader from '@/components/PageHeader';
import { Footnote, NextStepGuide } from '@/components/Guide';
import type { ImportResultResp } from '@/types';

/** 1.4 Excel 导入：模板下载 → 上传 → 成功/失败明细 */
export default function Imports() {
  const navigate = useNavigate();
  const [result, setResult] = useState<ImportResultResp | null>(null);
  const [fileList, setFileList] = useState<UploadFile[]>([]);

  const upload = useMutation({
    mutationFn: (file: File) => importLeads(file),
    onSuccess: (r) => {
      setResult(r);
      if (r.failed === 0) {
        message.success(`导入成功：共 ${r.total} 条，全部成功`);
      } else {
        message.warning(`导入完成：成功 ${r.success} 条，失败 ${r.failed} 条`);
      }
      setFileList([]);
    },
  });

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title="Excel 导入"
        subtitle="先下载模板按列填写，再上传——校验不过的行不会入库，改完可以重传。"
        demo
        actions={
          <Space wrap>
            <Button
              icon={<DownloadOutlined />}
              href={IMPORT_TEMPLATE_URL}
              target="_blank"
              rel="noreferrer"
            >
              下载导入模板
            </Button>
            <Button onClick={() => navigate('/imports/batches')}>查看批次历史</Button>
          </Space>
        }
      />

      {result && (
        <Row gutter={[12, 12]}>
          <Col xs={24} sm={8}>
            <KpiCard
              label="本次解析行数"
              value={result.total}
              icon={<FileExcelOutlined />}
              tone="brand"
              hint="文件里被读取到的数据行数（不含表头与「填写说明」工作表）"
            />
          </Col>
          <Col xs={24} sm={8}>
            <KpiCard
              label="校验通过并入库"
              value={result.success}
              icon={<CheckCircleOutlined />}
              tone="green"
              hint="通过校验并写入系统的线索数；填了「归属人」会直接指派并启动首次跟进计时"
            />
          </Col>
          <Col xs={24} sm={8}>
            <KpiCard
              label="校验失败（不入库）"
              value={result.failed}
              icon={<CloseCircleOutlined />}
              tone={result.failed > 0 ? 'red' : 'gray'}
              danger={result.failed > 0}
              hint="校验失败 = 必填缺失 / 格式错误 / 地区无法识别等；失败行一条都不会入库，修正后可重传"
            />
          </Col>
        </Row>
      )}

      <Card>
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <GateBanner
            tone="tip"
            title="导入流程（4 步）"
            items={[
              '① 下载模板：表头 = 需求确认稿表 1 + 表 2 共 16 列，另含「填写说明」工作表；',
              '② 按模板填写：客户名称 / 来源 / 省份 / 城市 / 区县 / 类型 必填；',
              '③ 上传文件：单次建议不超过 2000 条；',
              '④ 看结果明细：填了「归属人」会直接指派并启动跟进计时，未填则进入未指派池等待分配。',
            ]}
          />
          <Divider />
          <Upload.Dragger
            accept=".xlsx,.xls"
            maxCount={1}
            fileList={fileList}
            beforeUpload={(file) => {
              setFileList([file as unknown as UploadFile]);
              upload.mutate(file as unknown as File);
              return false; // 阻止默认上传，走自定义接口
            }}
            onRemove={() => setFileList([])}
            disabled={upload.isPending}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽 .xlsx 文件到此处上传</p>
            <p className="ant-upload-hint">单次导入建议不超过 2000 条</p>
          </Upload.Dragger>
        </Space>
      </Card>

      {result && (
        <Card
          title="导入结果"
          extra={
            <Space>
              <Tag color="green">成功 {result.success}</Tag>
              <Tag color="red">失败 {result.failed}</Tag>
              <Tag>总计 {result.total}</Tag>
            </Space>
          }
        >
          {result.failed === 0 ? (
            <Result
              status="success"
              title="全部导入成功"
              subTitle={`批次：${result.batch?.file_name ?? '-'}，共 ${result.total} 条`}
              extra={
                <Space wrap>
                  <Button type="primary" onClick={() => navigate('/leads?view=unassigned')}>
                    去分配
                  </Button>
                  <Button onClick={() => navigate('/imports/batches')}>查看批次历史</Button>
                </Space>
              }
            />
          ) : (
            <>
              <GateBanner
                title={`有 ${result.failed} 行校验未通过，未入库（其余 ${result.success} 行已成功导入）`}
                items={[
                  '失败行不会入库，也不会占用线索名额；',
                  '按行号回到 Excel 修正后重新上传整份文件即可（已成功的行不会重复导入）。',
                ]}
                action={
                  <Space wrap>
                    <Button size="small" href={IMPORT_TEMPLATE_URL} target="_blank" rel="noreferrer">
                      对照模板核对列
                    </Button>
                    <Button size="small" onClick={() => navigate('/imports/batches')}>
                      查看批次历史
                    </Button>
                  </Space>
                }
              />
              <Table
                rowKey={(r) => `${r.row}-${r.message}`}
                size="small"
                pagination={{ pageSize: 10 }}
                dataSource={result.errors ?? []}
                locale={{
                  emptyText: (
                    <NextStepGuide
                      compact
                      title="无失败明细"
                      description="后端未返回逐行原因；可到「导入批次历史」查看该批次的失败汇总。"
                      actions={
                        <Button size="small" onClick={() => navigate('/imports/batches')}>
                          查看批次历史
                        </Button>
                      }
                    />
                  ),
                }}
                columns={[
                  { title: '行号', dataIndex: 'row', width: 100 },
                  { title: '失败原因', dataIndex: 'message' },
                ]}
              />
            </>
          )}
        </Card>
      )}

      {!result && (
        <Card>
          <NextStepGuide
            title="还没有上传文件"
            description="先下载模板，按列填写后上传（.xlsx）。校验不过的行不会入库，改完可重传；已成功的行不会重复导入。"
            actions={
              <Space wrap>
                <Button type="primary" icon={<DownloadOutlined />} href={IMPORT_TEMPLATE_URL} target="_blank" rel="noreferrer">
                  下载导入模板
                </Button>
                <Button onClick={() => navigate('/imports/batches')}>查看批次历史</Button>
              </Space>
            }
          />
        </Card>
      )}

      <Footnote>
        导入是「先校验、后入库」：失败行一条都不入库，也不占用线索名额；修正后上传整份文件即可，已成功的行不会重复导入。
        必填列为 客户名称 / 来源 / 省份 / 城市 / 区县 / 客户类型；填了「归属人」会直接指派并启动首次跟进计时（1 个工作日，按工作日计），
        未填则进入未指派池。疑似重复（客户名称 + 省市区撞单）会进入疑似重复裁决台，等待人工裁决。每次上传都会生成一条导入批次记录。
      </Footnote>
    </Space>
  );
}
