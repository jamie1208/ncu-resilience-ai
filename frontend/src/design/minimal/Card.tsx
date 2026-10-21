import type { CardProps } from '../contract'
import { cn } from '../../lib/cn'

/** 基礎卡片：白底、細框、柔和陰影；只有 interactive 才有 hover 浮起。 */
export function Card({
  as: Tag = 'div',
  featured = false,
  interactive = false,
  className,
  ...props
}: CardProps) {
  return (
    <Tag
      className={cn(
        'rounded-xl border bg-card p-4 shadow-sm',
        featured ? 'border-accent/30' : 'border-border',
        interactive &&
          'transition-all duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md',
        className,
      )}
      {...props}
    />
  )
}
