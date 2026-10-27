import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { AxiosError } from 'axios';
import {
  Alert,
  Button,
  Card,
  Dropdown,
  Empty,
  Form,
  Input,
  Modal,
  Space,
  Spin,
  Statistic,
  Steps,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  CheckCircleFilled,
  EditOutlined,
  EllipsisOutlined,
  EyeOutlined,
  FileTextOutlined,
  PaperClipOutlined,
  PlusOutlined,
  UndoOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import {
  completeStage,
  getDeliveryStage,
  reopenStage,
  toggleStageMaterial,
} from '@/api/delivery';
import { fmtDateTime } from '@/utils/format';
import { BRAND, SUCCESS, TEXT_SUB } from '@/theme';
import GateBanner from '@/components/GateBanner';
import { DELIVERY_PHASE_ORDER, baseText, isOverdue, phaseLabel, stayText } from '@/utils/delivery';
import type { DeliveryMaterialItem, DeliveryStageProgress, DeliveryStageSummary } from '@/types';

interface Props {
  contractId: string;
  /** 10 阶段汇总（交付总览返回） */
  stagesSummary?: DeliveryStageSummary[];
  /** 当前阶段进度（交付总览返回，用于默认定位） */
  progress?: DeliveryStageProgress | null;
  /** 只读（管理层） */
  readOnly?: boolean;
  /** 数据变更后通知父页面刷新交付总览 */
  onChanged?: () => void;
  /** 受控：当前查看的阶段（工作台把阶段导航提到页面上方时使用） */
  phase?: number;
  /** 受控：切换阶段 */
  onPhaseChange?: (phase: number) => void;
  /** 隐藏内置的 10 阶段 Steps（工作台已在页面上方渲染两行阶段导航） */
  hideStageNav?: boolean;
}

/** 取后端 409/422 的 detail 文案（不吞掉业务提示） */
function detailOf(err: unknown): string {
  const e = err as AxiosError<{ detail?: string }>;
  return e?.response?.data?.detail ?? e?.message ?? '操作失败';
}

/**
 * 3.5 交付工作台核心面板：当前阶段产出材料清单（勾选必须附证据）+ 完成/重新打开。
 *
 * 判据落点：
 * - C2 少判断：清单右侧固定「证据」列——已附= 证据 + 查看；未附=白底蓝边「上传证据」按钮。
 *   必填未完成时，清单下方用 GateBanner 列出缺哪几条，并把「完成当前阶段」按钮**置灰 + Tooltip 说明原因**，
 *   而不是等点了才收到 409。
 * - C3 口径可见：`base_days = 0` 表示「未设停留基准」，此时不显示超期红色标记，只提示「基准未设」。
 */
export default function DeliveryStagePanel({
  contractId,
  stagesSummary,
  progress,
  readOnly = false,
  onChanged,
  phase: controlledPhase,
  onPhaseChange,
  hideStageNav = false,
}: Props) {
  const qc = useQueryClient();
  const [innerPhase, setInnerPhase] = useState<number | null>(null);
  const [evidenceItem, setEvidenceItem] = useState<DeliveryMaterialItem | null>(null);
  const [viewingItem, setViewingItem] = useState<DeliveryMaterialItem | null>(null);
  const [action, setAction] = useState<'complete' | 'reopen' | null>(null);
  const [actionError, setActionError] = useState('');
  const [evidenceForm] = Form.useForm<{ evidence: string }>();
  const [actionForm] = Form.useForm<{ remark: string }>();

  /** 10 阶段清单（后端汇总优先，缺失时用本地阶段名兜底） */
  const stages = useMemo<DeliveryStageSummary[]>(() => {
    if (stagesSummary?.length) {
      return [...stagesSummary].sort((a, b) => Number(a.phase) - Number(b.phase));
    }
    return DELIVERY_PHASE_ORDER.map((p) => ({
      phase: p,
      phase_label: phaseLabel(p),
      is_done: false,
      done_count: 0,
      total_count: 0,
      stay_days: 0,
      base_days: 0,
      overdue: false,
    }));
  }, [stagesSummary]);

  /** 默认定位：当前阶段 → 第一个未完成阶段 → 第一阶段 */
  const defaultPhase = useMemo(() => {
    if (progress?.phase) return Number(progress.phase);
    const firstOpen = stages.find((s) => !s.is_done);
    return firstOpen?.phase ?? stages[0]?.phase ?? 1;
  }, [progress?.phase, stages]);

  useEffect(() => {
    setInnerPhase((prev) => prev ?? defaultPhase);
  }, [defaultPhase]);

  const setPhase = (p: number) => {
    if (onPhaseChange) onPhaseChange(p);
    else setInnerPhase(p);
  };

  const activePhase = controlledPhase ?? innerPhase ?? defaultPhase;

  const stageQuery = useQuery({
    queryKey: ['delivery-stage', contractId, activePhase],
    queryFn: () => getDeliveryStage(contractId, activePhase),
    enabled: !!contractId,
  });

  /** 父级已带回当前阶段时作为首屏兜底，避免重复 loading */
  const stage: DeliveryStageProgress | null =
    stageQuery.data ?? (progress && Number(progress.phase) === activePhase ? progress : null);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['delivery-overview', contractId] });
    qc.invalidateQueries({ queryKey: ['deliveries'] });
    onChanged?.();
  };

  /* ---------------- 勾选 / 取消产出材料 ---------------- */
  const doToggle = useMutation({
    mutationFn: (v: { standardId: string; isDone: boolean; evidence?: string }) =>
      toggleStageMaterial(contractId, activePhase, v.standardId, {
        is_done: v.isDone,
        evidence: v.evidence,
      }),
    onSuccess: (r) => {
      message.success('产出材料已更新');
      qc.setQueryData(['delivery-stage', contractId, activePhase], r.stage);
      setEvidenceItem(null);
      evidenceForm.resetFields();
      refresh();
    },
  });

  /* ---------------- 完成阶段 / 重新打开 ---------------- */
  const doAction = useMutation({
    mutationFn: (v: { type: 'complete' | 'reopen'; remark?: string }) =>
      v.type === 'complete'
        ? completeStage(contractId, activePhase, v.remark)
        : reopenStage(contractId, activePhase, v.remark),
    onSuccess: () => {
      message.success(action === 'complete' ? '本阶段已完成' : '本阶段已重新打开');
      // 后端返回的 stage 是「操作后的当前阶段」，可能与本次操作阶段不同，统一失效重取
      qc.invalidateQueries({ queryKey: ['delivery-stage', contractId] });
      setAction(null);
      setActionError('');
      actionForm.resetFields();
      refresh();
    },
    onError: (err) => {
      // 必填材料未完成（409）等业务提示原样展示，不关闭弹窗
      setActionError(detailOf(err));
    },
  });

  const items = stages.map((s, idx) => {
    const current = Number(s.phase) === Number(activePhase);
    const status = s.is_done ? 'finish' : current ? 'process' : 'wait';
    return {
      key: `phase-${s.phase}`,
      title: (
        <Typography.Text strong={current} type={s.is_done ? undefined : current ? undefined : 'secondary'}>
          {phaseLabel(s.phase, s.phase_label)}
        </Typography.Text>
      ),
      status: status as 'finish' | 'process' | 'wait',
      description: (
        <Space direction="vertical" size={0}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {stayText(s.stay_days, s.base_days)}
          </Typography.Text>
          {isOverdue(s.overdue, s.base_days) ? (
            <Tag color="red" style={{ marginInlineEnd: 0 }}>
              超期
            </Tag>
          ) : null}
          {s.total_count > 0 && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              材料 {s.done_count}/{s.total_count}
            </Typography.Text>
          )}
        </Space>
      ),
      disabled: false,
      index: idx,
    };
  });

  const doneCount = stages.filter((s) => s.is_done).length;
  const overdue = stage ? isOverdue(stage.overdue, stage.base_days) : false;
  const missing = stage?.missing_required ?? [];
  /** 完成当前阶段被拦下的原因（C2：置灰必须说明「为什么 + 怎么办」） */
  const blockedReason = readOnly
    ? '管理层为只读视图：勾选产出材料与完成阶段需由运营交付专员操作。'
    : !stage
      ? '阶段数据加载中，请稍候。'
      : stage.is_done
        ? '本阶段已完成；如需修改材料勾选，请先点「重新打开」。'
        : missing.length > 0
          ? `还有 ${missing.length} 条必填产出材料未完成，无法完成本阶段。请先在下方清单中上传证据并勾选。`
          : '';

  const canComplete = !readOnly && !!stage && !stage.is_done && missing.length === 0;

  /** 证据列（设计稿 08）：已附 =  证据摘要（蓝链）+「…」菜单；未附 = 白底蓝边「上传证据」/「新增回访」 */
  const evidenceCell = (item: DeliveryMaterialItem) => {
    const { completion, standard } = item;
    /** 回访类材料（材料名含「回访」）按设计稿用「新增回访」而非「上传证据」 */
    const isVisit = standard.content.includes('回访');
    if (completion.is_done) {
      return (
        <Space size={6} align="start" style={{ width: '100%' }}>
          <PaperClipOutlined style={{ color: BRAND, marginTop: 3 }} />
          <div style={{ minWidth: 0, flex: 1 }}>
            <Tooltip title={completion.evidence || '（未填写证据说明）'}>
              <Typography.Text
                style={{ fontSize: 13, color: BRAND, display: 'block', cursor: 'pointer' }}
                ellipsis
                onClick={() => setViewingItem(item)}
              >
                {completion.evidence || '（未填写证据说明）'}
              </Typography.Text>
            </Tooltip>
            <a style={{ fontSize: 12 }} onClick={() => setViewingItem(item)}>
              查看
            </a>
          </div>
          <Dropdown
            trigger={['click']}
            disabled={readOnly || stage?.is_done}
            menu={{
              items: [
                { key: 'view', label: '查看证据', icon: <EyeOutlined /> },
                { key: 'edit', label: '修改证据', icon: <EditOutlined /> },
                { type: 'divider' },
                { key: 'undo', label: '取消完成（改回未完成）', icon: <UndoOutlined />, danger: true },
              ],
              onClick: ({ key, domEvent }) => {
                domEvent.stopPropagation();
                if (key === 'view') setViewingItem(item);
                if (key === 'edit') {
                  setEvidenceItem(item);
                  evidenceForm.setFieldsValue({ evidence: completion.evidence ?? '' });
                }
                if (key === 'undo') doToggle.mutate({ standardId: standard.id, isDone: false });
              },
            }}
          >
            <Button type="text" size="small" style={{ color: TEXT_SUB }} aria-label="更多操作">
              <EllipsisOutlined />
            </Button>
          </Dropdown>
        </Space>
      );
    }
    return (
      <Space size={6} align="center">
        <Button
          size="small"
          icon={isVisit ? <PlusOutlined /> : <UploadOutlined />}
          disabled={readOnly || stage?.is_done}
          style={{ background: '#FFFFFF', borderColor: BRAND, color: BRAND }}
          onClick={() => {
            setEvidenceItem(item);
            evidenceForm.setFieldsValue({ evidence: completion.evidence ?? '' });
          }}
        >
          {isVisit ? '新增回访' : '上传证据'}
        </Button>
        <Dropdown
          trigger={['click']}
          disabled={readOnly || stage?.is_done}
          menu={{
            items: [
              {
                key: 'hint',
                label: `要求：${standard.evidence_hint || '文档 / 签字件 / 截图'}`,
                disabled: true,
              },
            ],
          }}
        >
          <Button type="text" size="small" style={{ color: TEXT_SUB }} aria-label="证据要求">
            <EllipsisOutlined />
          </Button>
        </Dropdown>
      </Space>
    );
  };

  const materialColumns: ColumnsType<DeliveryMaterialItem> = [
    {
      title: '序号',
      key: 'seq',
      width: 56,
      align: 'center',
      render: (_, item, idx) => (
        <Typography.Text type="secondary">{item.standard.seq || idx + 1}</Typography.Text>
      ),
    },
    {
      title: '材料名称',
      key: 'name',
      render: (_, item) => {
        const { standard, completion } = item;
        return (
          <div>
            <Space size={6} wrap>
              {standard.is_required ? <Tag color="red">必填</Tag> : <Tag>可选</Tag>}
              <Typography.Text delete={completion.is_done}>{standard.content}</Typography.Text>
              {completion.source === 'ai_confirmed' && <Tag color="geekblue">AI 生成·已确认</Tag>}
            </Space>
          </div>
        );
      },
    },
    {
      title: '完成要求',
      key: 'requirement',
      width: 240,
      render: (_, item) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {item.standard.evidence_hint || '文档 / 签字件 / 截图 / 确认人 + 时间'}
        </Typography.Text>
      ),
    },
    {
      title: '完成状态',
      key: 'status',
      width: 120,
      render: (_, item) =>
        item.completion.is_done ? (
          <Space size={6}>
            <CheckCircleFilled style={{ color: SUCCESS }} />
            <span style={{ color: SUCCESS }}>已完成</span>
          </Space>
        ) : (
          <Space size={6}>
            <span style={{ display: 'inline-block', width: 12, height: 12, borderRadius: '50%', border: '1.5px solid #D0D5DD' }} />
            <span style={{ color: TEXT_SUB }}>未完成</span>
          </Space>
        ),
    },
    {
      title: '证据文件 / 操作',
      key: 'evidence',
      width: 260,
      render: (_, item) => evidenceCell(item),
    },
    {
      title: '完成人 / 完成时间',
      key: 'who',
      width: 160,
      render: (_, item) =>
        item.completion.is_done ? (
          <Space direction="vertical" size={0}>
            <span>{item.completion.done_by_name || '-'}</span>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {fmtDateTime(item.completion.done_at)}
            </Typography.Text>
          </Space>
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            未完成
          </Typography.Text>
        ),
    },
  ];

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {/* 未隐藏阶段导航时（客户档案页）保留原 Steps 形态 */}
      {!hideStageNav && (
        <Card size="small" title="10 阶段交付进度（一 项目管理贯穿全程，其余顺序执行）">
          <Steps
            size="small"
            labelPlacement="vertical"
            current={stages.findIndex((s) => Number(s.phase) === Number(activePhase))}
            items={items}
            onChange={(idx) => setPhase(stages[idx]?.phase ?? 1)}
          />
          <Space size={24} style={{ marginTop: 16 }} wrap>
            <Statistic title="已完成阶段" value={doneCount} suffix={`/ ${stages.length}`} />
            <Statistic
              title="当前阶段"
              value={phaseLabel(progress?.phase, progress?.phase_label) || '—'}
            />
            <Statistic title="本阶段停留" value={stage?.stay_days ?? 0} suffix="天" />
            <Statistic title="停留时长基准" value={baseText(stage?.base_days)} />
            <Statistic
              title="产出材料完成"
              value={stage?.done_count ?? 0}
              suffix={`/ ${stage?.total_count ?? 0}`}
            />
          </Space>
        </Card>
      )}

      <Card
        size="small"
        title={
          <Space size={8} wrap>
            <span>{phaseLabel(activePhase, stage?.phase_label)}产出材料</span>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              已完成阶段 {doneCount}/{stages.length} · {stayText(stage?.stay_days, stage?.base_days)}
            </Typography.Text>
          </Space>
        }
        extra={
          <Space>
            {stage?.is_done && (
              <Tooltip title="重新打开后该阶段回到进行中，可继续修改材料勾选与证据。">
                <Button
                  disabled={readOnly || !stage}
                  onClick={() => {
                    setActionError('');
                    actionForm.resetFields();
                    setAction('reopen');
                  }}
                >
                  重新打开
                </Button>
              </Tooltip>
            )}
            <Tooltip title="材料勾选与证据提交后即时落库；此按钮用于重新拉取最新进度（多人协作时确认没有被覆盖）。">
              <Button
                onClick={() => {
                  qc.invalidateQueries({ queryKey: ['delivery-stage', contractId] });
                  refresh();
                  message.success('已按最新数据刷新本阶段进度');
                }}
              >
                刷新进度
              </Button>
            </Tooltip>
          </Space>
        }
      >
        {stageQuery.isLoading && !stage ? (
          <Spin />
        ) : !stage ? (
          <Empty description="该阶段暂无可查看的进度数据" />
        ) : (
          <Space direction="vertical" size={10} style={{ width: '100%' }}>
            {overdue && (
              <Alert
                type="error"
                showIcon
                message={`本阶段已停留 ${stage.stay_days} 天，超过基准 ${stage.base_days} 天`}
                description="建议尽快补齐产出材料并完成本阶段，或说明延期原因（客户配合 / 产研排期 / 主动运营）。"
              />
            )}
            {!stage.base_days && (
              <Alert
                type="info"
                showIcon
                message="本阶段未设停留时长基准（不报交付延期）"
                description="停留时长基准为 0 表示未设基准，系统不会标记超期；可按需在「6.6 交付阶段产出材料配置」中设置。"
              />
            )}
            {stage.is_done && (
              <Alert
                type="success"
                showIcon
                message="本阶段已完成"
                description="如需修改材料勾选，请先点右上角「重新打开」。"
              />
            )}

            {stage.materials.length ? (
              <Table<DeliveryMaterialItem>
                rowKey={(r) => r.standard.id}
                size="small"
                columns={materialColumns}
                dataSource={stage.materials}
                pagination={false}
                tableLayout="fixed"
              />
            ) : (
              <Empty
                description="该阶段未配置产出材料，可在「6.6 交付阶段产出材料配置」中维护"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            )}

            {/*  必填未完成：清单下方用 GateBanner 列出缺哪几条，并给就地补齐入口 */}
            {missing.length > 0 && !stage.is_done && (
              <GateBanner
                title={`缺少 ${missing.length} 条必填产出材料，暂不能完成「${phaseLabel(
                  activePhase,
                  stage.phase_label,
                )}」`}
                items={missing.map((m) => `${m}（需上传证据）`)}
                action={
                  <Space size={8} wrap>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      在上方清单对应行点「上传证据」并填写证据后，本按钮会自动可用。
                    </Typography.Text>
                  </Space>
                }
              />
            )}

            {/* 清单下方操作条（设计稿 08）：左「保存进度」实心蓝；右「完成当前阶段」把禁用原因写进按钮文案 */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                flexWrap: 'wrap',
                borderTop: '1px solid #EAECF0',
                paddingTop: 12,
              }}
            >
              <Tooltip title="产出材料勾选与证据提交后已即时落库；此按钮重新拉取最新进度，确保保存成功。">
                <Button
                  type="primary"
                  onClick={() => {
                    qc.invalidateQueries({ queryKey: ['delivery-stage', contractId] });
                    refresh();
                    message.success('进度已保存（材料勾选与证据即时落库）');
                  }}
                >
                  保存进度
                </Button>
              </Tooltip>
              <Tooltip title={canComplete ? '完成本阶段后进入下一阶段。' : blockedReason}>
                <Button
                  disabled={!canComplete}
                  onClick={() => {
                    setActionError('');
                    actionForm.resetFields();
                    setAction('complete');
                  }}
                >
                  完成当前阶段
                  {missing.length > 0 ? '（请先完成必填证据）' : ''}
                  {readOnly ? '（只读角色不可操作）' : ''}
                  {stage?.is_done ? '（本阶段已完成）' : ''}
                </Button>
              </Tooltip>
              {!canComplete && !!blockedReason && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {blockedReason}
                </Typography.Text>
              )}
            </div>
          </Space>
        )}
      </Card>

      {/* 勾选证据弹窗：勾选产出材料必须附证据（后端缺证据返回 422） */}
      <Modal
        open={!!evidenceItem}
        title="填写证据"
        okText="提交并勾选"
        cancelText="取消"
        confirmLoading={doToggle.isPending}
        onCancel={() => {
          setEvidenceItem(null);
          evidenceForm.resetFields();
        }}
        onOk={async () => {
          const v = await evidenceForm.validateFields();
          if (!evidenceItem) return;
          doToggle.mutate({
            standardId: evidenceItem.standard.id,
            isDone: true,
            evidence: v.evidence.trim(),
          });
        }}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="勾选产出材料必须附证据（不看这个也能勾，但业务上不算完成）"
          description={`要求：${evidenceItem?.standard.evidence_hint || '文档 / 签字件 / 截图 / 确认人 + 时间'}`}
        />
        <Typography.Paragraph>{evidenceItem?.standard.content}</Typography.Paragraph>
        <Form form={evidenceForm} layout="vertical">
          <Form.Item
            label="证据"
            name="evidence"
            rules={[{ required: true, message: '请填写证据后再勾选' }]}
            extra="如：《项目启动确认单》已双方签字（2026-10-08）；或写明文件名 / 截图名 + 确认人。"
          >
            <Input.TextArea rows={3} placeholder="如：《项目启动确认单》已双方签字（2026-10-08）" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 查看证据（已附材料的「查看」入口） */}
      <Modal
        open={!!viewingItem}
        title="查看证据"
        footer={
          <Button onClick={() => setViewingItem(null)}>关闭</Button>
        }
        onCancel={() => setViewingItem(null)}
      >
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Typography.Text strong>{viewingItem?.standard.content}</Typography.Text>
          <Space size={6} wrap>
            {viewingItem?.standard.is_required ? <Tag color="red">必填</Tag> : <Tag>可选</Tag>}
            {viewingItem?.completion.source === 'ai_confirmed' && <Tag color="geekblue">AI 生成·已确认</Tag>}
          </Space>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              证据要求：{viewingItem?.standard.evidence_hint || '文档 / 签字件 / 截图 / 确认人 + 时间'}
            </Typography.Text>
          </div>
          <Card size="small">
            <Space size={6} align="start">
              <FileTextOutlined style={{ color: BRAND, marginTop: 3 }} />
              <Typography.Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>
                {viewingItem?.completion.evidence || '（未填写证据说明）'}
              </Typography.Paragraph>
            </Space>
          </Card>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            完成人 {viewingItem?.completion.done_by_name || '-'} · 完成时间{' '}
            {fmtDateTime(viewingItem?.completion.done_at)}
          </Typography.Text>
        </Space>
      </Modal>

      {/* 完成阶段 / 重新打开弹窗 */}
      <Modal
        open={!!action}
        title={action === 'complete' ? '完成本阶段' : '重新打开本阶段'}
        okText={action === 'complete' ? '确认完成' : '确认重新打开'}
        cancelText="取消"
        confirmLoading={doAction.isPending}
        onCancel={() => {
          setAction(null);
          setActionError('');
        }}
        onOk={async () => {
          const v = await actionForm.validateFields();
          if (!action) return;
          doAction.mutate({ type: action, remark: v.remark?.trim() });
        }}
      >
        {actionError && (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 12 }}
            message="操作未通过"
            description={actionError}
          />
        )}
        <Alert
          type={action === 'complete' ? 'info' : 'warning'}
          showIcon
          style={{ marginBottom: 12 }}
          message={
            action === 'complete'
              ? `完成「${phaseLabel(activePhase, stage?.phase_label)}」后进入下一阶段；必填产出材料未完成会被拦截`
              : `重新打开后该阶段回到进行中，可继续修改材料勾选与证据`
          }
        />
        <Form form={actionForm} layout="vertical">
          <Form.Item label="备注" name="remark">
            <Input.TextArea
              rows={3}
              placeholder={action === 'complete' ? '可选：本阶段完成情况说明' : '可选：重新打开原因'}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
