import { cva } from 'class-variance-authority'

import type { BadgeProps } from '../contract'
import { cn } from '../../lib/cn'

const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium whitespace-nowrap',
  {
    variants: {
      tone: {
        neutral: 'border-border bg-muted text-muted-foreground',
        info: 'border-info/20 bg-info-muted text-info',
        success: 'border-success/20 bg-success-muted text-success',
        warning: 'border-warning/20 bg-warning-muted text-warning',
        danger: 'border-danger/20 bg-danger-muted text-danger',
      },
    },
    defaultVariants: { tone: 'neutral' },
  },
)

/** 狀態標籤：語意色＋文字，不加動畫（用於風險等級、事件與通知狀態）。 */
export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />
}
