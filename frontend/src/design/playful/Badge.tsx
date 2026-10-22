import { cva } from 'class-variance-authority'

import type { BadgeProps } from '../contract'
import { cn } from '../../lib/cn'

const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-full border-2 border-foreground px-2.5 py-0.5 text-xs font-bold whitespace-nowrap',
  {
    variants: {
      tone: {
        neutral: 'bg-muted text-foreground',
        info: 'bg-info-muted text-info',
        success: 'bg-success-muted text-success',
        warning: 'bg-warning-muted text-warning',
        danger: 'bg-danger-muted text-danger',
      },
    },
    defaultVariants: { tone: 'neutral' },
  },
)

/** 膠囊標籤：2px 深色框＋語意色底與文字；不加彈跳或其他動畫。 */
export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />
}
