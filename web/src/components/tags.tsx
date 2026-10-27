import { Space, Tag, Tooltip } from 'antd';
import { useDicts } from '@/hooks/useDicts';
import {
  COUNTDOWN_COLOR,
  REMINDER_COLOR,
  RISK_COLOR,
  STATUS_COLOR,
  STATUS_LABEL,
  countdownToDeadline,
  firstFollowCountdown,
  fmtDate,
  sourceColor,
  toLabelMap,
} from '@/utils/format';

/** 线索状态标签（字典优先，后端未返回时用本地兜底） */
export function LeadStatusTag({ status }: { status?: string | null }) {
  const { dicts } = useDicts();
  if (!status) return <Tag>-</Tag>;
  const label = toLabelMap(dicts.lead_status)[status] ?? STATUS_LABEL[status] ?? status;
  return <Tag color={STATUS_COLOR[status] ?? 'default'}>{label}</Tag>;
}

/** 线索来源标签（8 类循环取色） */
export function SourceTag({ source }: { source?: string | null }) {
  const { dicts } = useDicts();
  if (!source) return <Tag>-</Tag>;
  const label = toLabelMap(dicts.lead_sources)[source] ?? source;
  return <Tag color={sourceColor(source)}>{label}</Tag>;
}

/** 客户类型标签 */
export function OrgTypeTag({ orgType }: { orgType?: string | null }) {
  const { dicts } = useDicts();
  if (!orgType) return <Tag>-</Tag>;
  const label = toLabelMap(dicts.org_types)[orgType] ?? orgType;
  return <Tag>{label}</Tag>;
}

/**
 * 首次跟进超时倒计时（S1-5：指派后 1 个工作日内需首次跟进）
 *
 * 优先用后端下发的 `first_follow_deadline`（已按工作日 + 法定节假日算好，权威）；
 * 没有该字段时才退回按 24 小时自然时长的本地估算。
 */
export function FollowCountdown({
  assignAt,
  firstFollowAt,
  deadline,
}: {
  assignAt?: string | null;
  firstFollowAt?: string | null;
  deadline?: string | null;
}) {
  const cd = firstFollowAt
    ? { text: '-', level: 'none' as const }
    : deadline
      ? countdownToDeadline(deadline)
      : firstFollowCountdown(assignAt, firstFollowAt);
  if (cd.level === 'none') return <span>-</span>;
  return <Tag color={COUNTDOWN_COLOR[cd.level]}>{cd.text}</Tag>;
}

/** 提醒类型标签 */
export function ReminderTag({ type }: { type?: string | null }) {
  const { dicts } = useDicts();
  if (!type) return <Tag>-</Tag>;
  const label = toLabelMap(dicts.reminder_types)[type] ?? type;
  return <Tag color={REMINDER_COLOR[type] ?? 'blue'}>{label}</Tag>;
}

/** 风险标记 R1–R5 */
export function RiskTags({ types }: { types?: string[] | null }) {
  const { dicts } = useDicts();
  if (!types?.length) return <span>-</span>;
  const map = toLabelMap(dicts.risk_types);
  return (
    <Space>
      {types.map((t) => (
        <Tooltip key={t} title={t}>
          <Tag color={RISK_COLOR[t] ?? 'orange'}>{map[t] ?? t}</Tag>
        </Tooltip>
      ))}
    </Space>
  );
}

/** 日期展示 */
export function DateText({ value }: { value?: string | null }) {
  return <span>{fmtDate(value)}</span>;
}
