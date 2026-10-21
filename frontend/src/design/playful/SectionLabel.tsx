import type { SectionLabelProps } from '../contract'
import { cn } from '../../lib/cn'

const CJK = /[㐀-鿿豈-﫿]/

/**
 * 區塊標籤（黃色膠囊貼紙 + 粉紅菱形 + 粗體標題字）。
 * 英文標籤用全大寫與寬字距；中文標籤不套用，避免字距過開難讀。
 */
export function SectionLabel({ children, pulse = false, className }: SectionLabelProps) {
  const isCjk = typeof children === 'string' && CJK.test(children)
  return (
    <div
      className={cn(
        'inline-flex items-center gap-2.5 rounded-full border-2 border-foreground bg-tertiary px-4 py-1.5 shadow-pop-sm',
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'h-2.5 w-2.5 rotate-45 border-2 border-foreground bg-secondary',
          pulse && 'motion-safe:animate-pulse-dot',
        )}
      />
      <span
        className={cn(
          'font-display text-xs font-bold text-foreground',
          isCjk ? 'tracking-normal' : 'uppercase tracking-wide',
        )}
      >
        {children}
      </span>
    </div>
  )
}
