import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Select, Space, Tag } from 'antd';
import { listLeads } from '@/api/leads';
import type { Lead } from '@/types';

interface Props {
  value?: string | null;
  onChange?: (leadId: string | undefined, lead?: Lead) => void;
  placeholder?: string;
  /** 是否允许清空（默认允许，用于「不选则由后端自动定位」） */
  allowClear?: boolean;
  style?: React.CSSProperties;
  disabled?: boolean;
}

/**
 * 商机（客户）选择器：拉取当前用户名下线索，支持名称/城市/负责人模糊搜索。
 * 5.1 录入定位、5.2 未定位草稿补客户、2.2 诊断入口均复用。
 */
export default function LeadSelect({
  value,
  onChange,
  placeholder = '选择客户（不选则由 AI 自动定位）',
  allowClear = true,
  style,
  disabled,
}: Props) {
  const { data, isLoading } = useQuery({
    queryKey: ['leads', 'mine-options'],
    queryFn: () => listLeads({ view: 'mine', page_size: 50, page: 1 }),
    staleTime: 60 * 1000,
  });

  const leads = data?.items ?? [];

  const options = useMemo(
    () =>
      leads.map((l) => ({
        value: l.id,
        label: [l.name, l.project_name].filter(Boolean).join(' · '),
        lead: l,
      })),
    [leads],
  );

  return (
    <Select
      showSearch
      allowClear={allowClear}
      disabled={disabled}
      loading={isLoading}
      value={value ?? undefined}
      placeholder={placeholder}
      style={{ minWidth: 260, ...style }}
      optionFilterProp="label"
      filterOption={(input, option) => {
        const lead = (option as { lead?: Lead } | undefined)?.lead;
        const kw = input.trim();
        if (!kw) return true;
        return [lead?.name, lead?.project_name, lead?.city, lead?.owner_name]
          .filter(Boolean)
          .some((t) => String(t).includes(kw));
      }}
      onChange={(v) => onChange?.(v ?? undefined, leads.find((l) => l.id === v))}
      options={options}
      optionRender={(opt) => {
        const lead = (opt.data as { lead?: Lead }).lead;
        if (!lead) return opt.label;
        return (
          <Space size={6}>
            <span>{[lead.name, lead.project_name].filter(Boolean).join(' · ')}</span>
            <Tag color="default" style={{ marginInlineEnd: 0 }}>
              {lead.city || '—'}
            </Tag>
            <Tag color="blue" style={{ marginInlineEnd: 0 }}>
              {lead.status_label ?? lead.status}
            </Tag>
          </Space>
        );
      }}
    />
  );
}
