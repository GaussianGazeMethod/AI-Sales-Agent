import type { ReactNode } from 'react';
import { Progress, Tooltip } from 'antd';
import { KPI_TONES, type KpiTone } from '@/theme';

/**
 * KPI 指标卡（设计稿形态）：左侧彩色圆角图标 + 标签 + 值（可带分母）+ 可选进度条与达成率。
 *
 * 判据 C3：任何比率都要能就地看到口径 —— 用 `hint` 传口径说明，会挂到 Tooltip 上。
 *
 * 用法：
 * ```tsx
 * <KpiCard label="本月有效商机" value={12} total="/ 20" icon={<FileTextOutlined />}
 *          tone="brand" progress={60} rateText="达成 60%" hint="有效商机 = 状态为「有效商机」的线索数" />
 * ```
 */
export interface KpiCardProps {
  label: ReactNode;
  value: ReactNode;
  /** 分母，如 "/ 20" 或 "/ ¥100万" */
  total?: ReactNode;
  /** 数值后缀（如「万」），等价于写在 value 里，便于统一字号 */
  suffix?: ReactNode;
  icon?: ReactNode;
  tone?: KpiTone;
  /** 0–100 的进度（传了才渲染进度条） */
  progress?: number;
  /** 进度条右侧文案，如「达成 60%」 */
  rateText?: ReactNode;
  /** 口径说明（Tooltip）——判据 C3 要求 */
  hint?: ReactNode;
  /** 数值是否用警示色（超期、缺口等） */
  danger?: boolean;
}

export default function KpiCard({
  label,
  value,
  total,
  suffix,
  icon,
  tone = 'brand',
  progress,
  rateText,
  hint,
  danger,
}: KpiCardProps) {
  const colors = KPI_TONES[tone];
  const body = (
    <div className="kpi-card">
      {icon && (
        <div className="kpi-icon" style={{ background: colors.bg, color: colors.fg }}>
          {icon}
        </div>
      )}
      <div className="kpi-body">
        <div className="kpi-label">{label}</div>
        <div className="kpi-value" style={danger ? { color: '#F04438' } : undefined}>
          {value}
          {suffix && <span className="kpi-total">{suffix}</span>}
          {total && <span className="kpi-total">{total}</span>}
        </div>
        {(progress !== undefined || rateText) && (
          <div className="kpi-foot">
            {progress !== undefined && (
              <Progress
                percent={Math.max(0, Math.min(100, progress))}
                showInfo={false}
                size={['100%', 6]}
                strokeColor={progress >= 100 ? '#12B76A' : '#465FFF'}
                trailColor="#EEF1FF"
              />
            )}
            {rateText && <span className="kpi-rate">{rateText}</span>}
          </div>
        )}
      </div>
    </div>
  );
  return hint ? <Tooltip title={hint}>{body}</Tooltip> : body;
}
