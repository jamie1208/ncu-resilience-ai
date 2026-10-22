import type { CardProps } from '../contract'
import { cn } from '../../lib/cn'

/**
 * 貼紙卡：白底、2px 深色框、無模糊硬陰影（featured 用粉紅陰影）。
 * 只有 interactive 才有 hover 搖擺（左轉 1 度、放大 1.02），且限 motion-safe。
 */
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
        'rounded-xl border-2 border-foreground bg-card p-5',
        featured ? 'shadow-sticker-featured' : 'shadow-sticker',
        interactive &&
          'transition-transform duration-300 ease-bounce motion-safe:hover:-rotate-1 motion-safe:hover:scale-[1.02]',
        className,
      )}
      {...props}
    />
  )
}
