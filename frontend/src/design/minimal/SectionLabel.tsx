import type { SectionLabelProps } from '../contract'
import { cn } from '../../lib/cn'

const CJK = /[㐀-鿿豈-﫿]/

/**
 * 區塊標籤（pill + 圓點 + 等寬字）。
 * 英文標籤用全大寫與寬字距；中文標籤不套用，避免字距過開難讀。
 */
export function SectionLabel({ children, pulse = false, className }: SectionLabelProps) {
  const isCjk = typeof children === 'string' && CJK.test(children)
  return (
    <div
      className={cn(
        'inline-flex items-center gap-3 rounded-full border border-accent/30 bg-accent/5 px-5 py-2',
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn('h-2 w-2 rounded-full bg-accent', pulse && 'motion-safe:animate-pulse-dot')}
      />
      <span
        className={cn(
          'font-mono text-xs text-accent',
          isCjk ? 'tracking-normal' : 'uppercase tracking-[0.15em]',
        )}
      >
        {children}
      </span>
    </div>
  )
}
