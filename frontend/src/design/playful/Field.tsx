import type { InputProps, SelectProps, TextareaProps } from '../contract'
import { cn } from '../../lib/cn'

const field = [
  'w-full rounded-lg border-2 border-foreground bg-card px-4 text-sm text-foreground',
  'placeholder:text-muted-foreground transition-shadow duration-300 ease-bounce',
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:shadow-pop-sm',
  'disabled:cursor-not-allowed disabled:opacity-50',
]

/** 文字輸入框：2px 深色框，focus 時出現硬陰影（高度 48px）。 */
export function Input({ className, ...props }: InputProps) {
  return <input className={cn(field, 'h-12', className)} {...props} />
}

/** 下拉選單（高度 48px）。 */
export function Select({ className, ...props }: SelectProps) {
  return <select className={cn(field, 'h-12 pr-8', className)} {...props} />
}

/** 多行輸入框。 */
export function Textarea({ className, ...props }: TextareaProps) {
  return <textarea className={cn(field, 'min-h-24 py-3 leading-relaxed', className)} {...props} />
}
