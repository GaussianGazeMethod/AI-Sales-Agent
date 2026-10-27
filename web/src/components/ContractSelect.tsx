import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Select, Space, Tag, Typography } from 'antd';
import { listContracts } from '@/api/delivery';
import { NextStepGuide } from '@/components/Guide';
import { fmtDate } from '@/utils/format';
import { EXPIRE_COLOR, EXPIRE_TEXT, expireLevel } from '@/utils/delivery';
import type { ContractOut } from '@/types';

interface Props {
  /** 当前选中的合同 id */
  value?: string | null;
  /** 选中回调：同时回传合同对象，便于取 lead_id / customer_name */
  onChange?: (contractId: string | undefined, contract?: ContractOut) => void;
  /**
   * 选中回调（拿合同对象）。
   *  在 Form.Item 内使用时 `onChange` 会被 antd 接管，取合同对象请用 onPick。
   */
  onPick?: (contract?: ContractOut) => void;
  placeholder?: string;
  allowClear?: boolean;
  style?: React.CSSProperties;
  disabled?: boolean;
}

/**
 * 客户（合同）选择器。
 *
 *  交付角色（delivery_staff/manager）**无权访问 /leads**，因此这里不能复用 LeadSelect，
 * 统一走 `/contracts`（交付域可见范围）：既能拿到客户名，又能同时取到 lead_id 与 contract_id。
 * 支持按客户名 / 合同编号关键词做服务端搜索。
 */
export default function ContractSelect({
  value,
  onChange,
  onPick,
  placeholder = '搜索客户名称 / 合同编号',
  allowClear = true,
  style,
  disabled,
}: Props) {
  const [keyword, setKeyword] = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['contracts', 'select-options', keyword],
    queryFn: () => listContracts({ keyword: keyword || undefined, page: 1, page_size: 30 }),
    staleTime: 60 * 1000,
  });

  const items = data?.items ?? [];
  /** 有关键词但一条都没查到（用于空态引导，区别于「还没输入」） */
  const searchedNothing = !isLoading && keyword.trim().length > 0 && items.length === 0;

  const options = useMemo(
    () =>
      items.map((c) => ({
        value: c.id,
        label: c.customer_name,
        contract: c,
      })),
    [items],
  );

  return (
    <Select
      showSearch
      allowClear={allowClear}
      disabled={disabled}
      loading={isLoading}
      value={value ?? undefined}
      placeholder={placeholder}
      style={{ minWidth: 280, ...style }}
      filterOption={false}
      onSearch={setKeyword}
      onChange={(v) => {
        const hit = items.find((c) => c.id === v);
        onChange?.(v ?? undefined, hit);
        onPick?.(hit);
      }}
      notFoundContent={
        isLoading ? (
          '加载中…'
        ) : searchedNothing ? (
          <NextStepGuide
            compact
            title="没有匹配的客户"
            description="客户来自签约（闭环二环节七）后自动生成的合同；也可用「补录合同」登记历史合同。"
          />
        ) : (
          '输入客户名称 / 合同编号搜索'
        )
      }
      options={options}
      optionRender={(opt) => {
        const c = (opt.data as { contract?: ContractOut }).contract;
        if (!c) return opt.label;
        const level = expireLevel(c.days_to_expire);
        return (
          <Space size={6} wrap>
            <span>{c.customer_name}</span>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {c.contract_no}
            </Typography.Text>
            <Tag color={EXPIRE_COLOR[level]} style={{ marginInlineEnd: 0 }}>
              {EXPIRE_TEXT[level]}
              {c.days_to_expire !== null && c.days_to_expire !== undefined
                ? c.days_to_expire < 0
                  ? ` ${Math.abs(c.days_to_expire)} 天`
                  : ` 剩 ${c.days_to_expire} 天`
                : ''}
            </Tag>
            {c.service_end ? (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                服务期至 {fmtDate(c.service_end)}
              </Typography.Text>
            ) : null}
          </Space>
        );
      }}
    />
  );
}
